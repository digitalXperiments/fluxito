"""
Dashboard Live Query Routes

Public endpoint — no session auth required. Authorization is via
per-dashboard query_scopes + optional query_token.

Routes:
  POST /api/dashboard-query/{slug}/batch   — run one or more cards' stored specs
  GET  /api/dashboard-query/{slug}/meta    — dashboard scope metadata

The batch endpoint dispatches through the MCP tool registry
(``tool_manager._legacy_tools``). Each card stores its full tool-call
spec in ``dashboard_cards.query_params`` at deploy time:

    {
      "platform": "bigquery",
      "tool": "warehouse_query",
      "action": "run_query",
      "params": {...},
      "filter_hooks": {"date_range.start": "params.start_date", ...}
    }

The browser sends ``{cards: [{card_id, overrides}]}``; the server looks
up each card's spec, merges user filter overrides via ``filter_hooks``,
checks ``query_scopes`` authorization, and dispatches. New connectors
get live-refresh support as soon as they have an MCP tool registered.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

import app.app_state as app_state
from app.auth.mcp_session_manager import build_refresh_context
from app.dashboards import query_engine
from app.dashboards.scope import is_authorized
from app.models.dashboard import Dashboard, DashboardCard, DashboardQueryLog

logger = logging.getLogger(__name__)

# Per-card upstream timeout so one hung query (GA4/BigQuery/…) can't stall the
# whole batch or the filter-options lookup.
_QUERY_TIMEOUT_S = 25

router = APIRouter(prefix="/api/dashboard-query", tags=["dashboard-query"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CardRequest(BaseModel):
    card_id: str
    overrides: dict | None = None


class BatchRequest(BaseModel):
    token: str | None = None
    cards: list[CardRequest] = Field(..., max_length=20, min_length=1)


class CardResult(BaseModel):
    rows: list[dict] | None = None
    row_count: int | None = None
    columns: list[str] | None = None
    cache_hit: bool = False
    error: str | None = None


class BatchResponse(BaseModel):
    results: dict[str, CardResult]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cache_key(slug: str, card_id: str, merged_params: dict) -> str:
    payload = json.dumps({"slug": slug, "card": card_id, "p": merged_params}, sort_keys=True, default=str)
    return "mmq:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


async def _log_query(
    slug: str,
    ip: str | None,
    platform: str,
    resource: str,
    cache_hit: bool,
    duration_ms: int,
    error: str | None,
) -> None:
    try:
        async with app_state.db_session_factory() as db:
            db.add(
                DashboardQueryLog(
                    slug=slug,
                    ip=ip,
                    platform=platform,
                    property_id=resource,  # column is legacy-named but stores any resource id
                    cache_hit=cache_hit,
                    duration_ms=duration_ms,
                    error=error,
                )
            )
            await db.commit()
    except Exception:
        pass  # non-critical


def _tool_manager() -> Any:
    # Late import to avoid the circular dep: app.main imports from app.api.
    from app.main import mcp_server

    if mcp_server is None:
        raise HTTPException(500, "MCP server not initialized")
    return mcp_server._tool_manager


def _normalize_result(platform: str, raw: Any) -> dict:
    """Flatten varied MCP tool return shapes into ``{rows, row_count, columns}``.

    Different connectors return different shapes: GA4 uses
    ``{dimension_headers, metric_headers, rows}`` where rows have
    ``{dimensions, metrics}`` arrays; warehouse queries return
    ``{rows, row_count}`` already in list-of-dict shape; most other
    connectors already return list-of-dict rows.
    """
    if not isinstance(raw, dict):
        return {"rows": None, "row_count": 0, "columns": None}

    if raw.get("error"):
        return {
            "rows": None,
            "row_count": 0,
            "columns": None,
            "error": str(raw.get("message") or raw["error"]),
        }

    # GA4 shape
    if platform == "ga4" and "dimension_headers" in raw and "metric_headers" in raw:
        dim_headers = raw.get("dimension_headers", [])
        met_headers = raw.get("metric_headers", [])
        columns = list(dim_headers) + list(met_headers)
        out_rows = []
        for row in raw.get("rows", []):
            obj: dict = {}
            dims = row.get("dimensions", []) or []
            mets = row.get("metrics", []) or []
            for i, h in enumerate(dim_headers):
                obj[h] = dims[i] if i < len(dims) else ""
            for i, h in enumerate(met_headers):
                raw_val = mets[i] if i < len(mets) else "0"
                try:
                    obj[h] = float(raw_val)
                except (ValueError, TypeError):
                    obj[h] = raw_val
            out_rows.append(obj)
        return {"rows": out_rows, "row_count": raw.get("row_count", len(out_rows)), "columns": columns}

    # Warehouse / generic list-of-dict shape
    rows = raw.get("rows")
    if isinstance(rows, list):
        columns = raw.get("columns")
        if columns is None and rows and isinstance(rows[0], dict):
            columns = list(rows[0].keys())
        return {"rows": rows, "row_count": raw.get("row_count", len(rows)), "columns": columns}

    # Fall-through: pass through as a single "data" wrapper
    return {"rows": [raw], "row_count": 1, "columns": list(raw.keys())}


async def _can_view_dashboard(request: Request, dash: Dashboard) -> bool:
    from app.auth.uid_cookie import get_uid_from_request
    from app.utils import safe_uuid

    if dash.is_public:
        return True
    uid = get_uid_from_request(request)
    if not uid or str(dash.user_id) != uid:
        return False
    # Private dashboard: the owner must still be an active member of the
    # dashboard's project. A user removed from a project loses access to its
    # dashboards even though they originally created them. Legacy dashboards
    # with no project (project_id IS NULL) fall back to owner-only access.
    if dash.project_id is None:
        return True
    user_uuid = safe_uuid(uid)
    if user_uuid is None:
        return False
    from app.models.project import ProjectMember

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == dash.project_id,
                ProjectMember.user_id == user_uuid,
                ProjectMember.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/{slug}/batch", response_model=BatchResponse)
async def batch_query(slug: str, body: BatchRequest, request: Request):
    redis = app_state.redis_client

    # 1. Look up dashboard
    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Dashboard).where(Dashboard.share_slug == slug))
        dash = result.scalar_one_or_none()
    if not dash:
        raise HTTPException(404, "Dashboard not found")
    if not await _can_view_dashboard(request, dash):
        raise HTTPException(404, "Dashboard not found")

    # 2. Optional token gate
    if dash.query_token_required:
        if not body.token or body.token != dash.query_token:
            raise HTTPException(403, "Invalid or missing query token")

    # 3. Rate limit: 100 req/min per (slug, IP)
    client_ip = request.client.host if request.client else "unknown"
    rl_key = f"rl:dq:{slug}:{client_ip}:{int(time.time()) // 60}"
    count = await redis.incr(rl_key)
    if count == 1:
        await redis.expire(rl_key, 60)
    if count > 100:
        raise HTTPException(429, "Rate limit exceeded (100 req/min per IP per dashboard)")

    # 4. Load the requested cards in one shot — reject malformed UUIDs
    #    with 400 instead of bubbling a ValueError into a 500.
    from app.utils import safe_uuid

    card_ids: list[uuid.UUID] = []
    for c in body.cards:
        parsed = safe_uuid(c.card_id)
        if parsed is None:
            raise HTTPException(400, f"Invalid card_id: {c.card_id!r}")
        card_ids.append(parsed)
    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(DashboardCard).where(
                DashboardCard.dashboard_id == dash.id,
                DashboardCard.id.in_(card_ids),
            )
        )
        cards_by_id: dict[str, DashboardCard] = {str(c.id): c for c in result.scalars().all()}

    # 5. Build synthetic MCP context (dashboard owner + project) so the tool
    #    registry can resolve connections without an MCP session.
    refresh_ctx = await build_refresh_context(str(dash.id))
    tm = _tool_manager()

    query_scopes = dash.query_scopes or []

    results: dict[str, CardResult] = {}

    async with refresh_ctx:
        for req in body.cards:
            t0 = time.monotonic()
            cache_hit = False
            error_str: str | None = None
            platform = "unknown"
            resource_id = ""

            try:
                card = cards_by_id.get(req.card_id)
                if card is None:
                    raise HTTPException(404, f"Card {req.card_id} not found on this dashboard")

                spec = card.query_params or {}
                platform = spec.get("platform") or card.platform or "unknown"
                tool_name = spec.get("tool") or card.tool_name
                action = spec.get("action")

                if not tool_name:
                    raise HTTPException(409, f"Card {req.card_id} has no 'tool' in its spec")

                merged_params = query_engine.build_call_args(spec, req.overrides)
                resource_id = str(
                    merged_params.get("property_id")
                    or merged_params.get("connection_id")
                    or merged_params.get("ad_account_id")
                    or ""
                )

                if not is_authorized(query_scopes, platform, merged_params):
                    raise HTTPException(
                        403,
                        f"Dashboard not authorized for {platform} (card {req.card_id}). "
                        f"Current scopes: {query_scopes}",
                    )

                # Cache check
                ck = _cache_key(slug, req.card_id, merged_params)
                cached_raw = await redis.get(ck)
                if cached_raw:
                    data = json.loads(cached_raw)
                    results[req.card_id] = CardResult(**data, cache_hit=True)
                    cache_hit = True
                    dur_ms = int((time.monotonic() - t0) * 1000)
                    asyncio.create_task(
                        _log_query(slug, client_ip, platform, resource_id, True, dur_ms, None)
                    )
                    continue

                # Dispatch through the MCP tool registry
                tool = query_engine.resolve_tool(tm, tool_name)
                if tool is None:
                    raise HTTPException(
                        500, f"Tool '{tool_name}' not registered; cannot refresh card {req.card_id}"
                    )

                call_args: dict[str, Any] = dict(merged_params)
                if action is not None:
                    call_args["action"] = action

                raw_result = await query_engine.dispatch(tool, call_args, _QUERY_TIMEOUT_S)
                normalized = _normalize_result(platform, raw_result)
                if normalized.get("error"):
                    error_str = normalized["error"]
                results[req.card_id] = CardResult(**normalized, cache_hit=False)

                # Only cache successful results
                if not error_str:
                    await redis.set(ck, json.dumps(normalized, default=str), ex=300)

            except HTTPException as http_exc:
                error_str = http_exc.detail if isinstance(http_exc.detail, str) else str(http_exc.detail)
                results[req.card_id] = CardResult(error=error_str)
            except TimeoutError:
                error_str = f"Query exceeded {_QUERY_TIMEOUT_S}s and was aborted"
                logger.warning("dashboard_query timeout slug=%s card=%s", slug, req.card_id)
                results[req.card_id] = CardResult(error=error_str)
            except Exception as exc:
                error_str = str(exc)
                logger.warning("dashboard_query error slug=%s card=%s: %s", slug, req.card_id, exc)
                results[req.card_id] = CardResult(error=error_str)

            dur_ms = int((time.monotonic() - t0) * 1000)
            asyncio.create_task(
                _log_query(slug, client_ip, platform, resource_id, cache_hit, dur_ms, error_str)
            )

    return BatchResponse(results=results)


_DATE_HOOK_KEYS = frozenset({"date_range.start", "date_range.end"})
_WAREHOUSE_PLATFORMS = frozenset({"redshift", "bigquery", "snowflake"})
_SPEC_META_KEYS = frozenset(
    {"key", "platform", "tool", "action", "filter_hooks", "filter_options", "chart_config", "title"}
)


@router.get("/{slug}/filter-options")
async def get_filter_options(slug: str, request: Request, token: str | None = None) -> dict:
    """Return distinct values for dimension filters that have no static options.

    For warehouse cards (redshift/bigquery/snowflake), extracts the main table
    from the card's SQL and runs ``SELECT DISTINCT {col} FROM {table}`` to
    populate the dropdown. Falls back to empty list on any error.
    """
    import re

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Dashboard).where(Dashboard.share_slug == slug))
        dash = result.scalar_one_or_none()
    if not dash:
        raise HTTPException(404, "Dashboard not found")
    if not await _can_view_dashboard(request, dash):
        raise HTTPException(404, "Dashboard not found")

    if dash.query_token_required:
        if not token or token != dash.query_token:
            raise HTTPException(403, "Invalid or missing query token")

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(DashboardCard).where(DashboardCard.dashboard_id == dash.id))
        cards = list(result.scalars().all())

    # Build map: dim_key → first warehouse card that uses it and has no static options
    dim_card_map: dict[str, DashboardCard] = {}
    for card in cards:
        qp = card.query_params or {}
        platform = (qp.get("platform") or "").lower()
        if platform not in _WAREHOUSE_PLATFORMS:
            continue
        action = qp.get("action")
        if action != "run_query":
            continue
        hooks = qp.get("filter_hooks") or {}
        options_map = qp.get("filter_options") or {}
        for hook_key in hooks:
            if hook_key in _DATE_HOOK_KEYS:
                continue
            if not options_map.get(hook_key) and hook_key not in dim_card_map:
                dim_card_map[hook_key] = card

    if not dim_card_map:
        return {}

    refresh_ctx = await build_refresh_context(str(dash.id))
    tm = _tool_manager()
    result_options: dict[str, list[str]] = {}

    async with refresh_ctx:
        for dim_key, card in dim_card_map.items():
            qp = card.query_params or {}
            platform = (qp.get("platform") or "").lower()

            # Support both old storage format (nested under "params") and new (flat)
            if "params" in qp and isinstance(qp["params"], dict):
                params_dict = qp["params"]
            else:
                params_dict = {k: v for k, v in qp.items() if k not in _SPEC_META_KEYS}

            sql_query = params_dict.get("query") or qp.get("query", "")
            if not sql_query:
                continue

            # Validate dim_key as a safe SQL identifier
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", dim_key):
                continue

            # Extract the first table name from the SQL's FROM clause
            table_match = re.search(r"\bFROM\s+([\w.`\-]+)", sql_query, re.IGNORECASE)
            if not table_match:
                continue
            table_name = table_match.group(1)
            # Allow alphanum + underscore + dot + hyphen (BQ project IDs) + backtick
            if not re.match(r"^[a-zA-Z_`][\w.\-`]*$", table_name):
                continue

            if platform == "bigquery":
                col_q = f"`{dim_key}`"
                tbl_q = table_name if table_name.startswith("`") else f"`{table_name}`"
            else:
                col_q = f'"{dim_key}"'
                tbl_q = table_name

            distinct_sql = (
                f"SELECT DISTINCT {col_q} AS val FROM {tbl_q} "
                f"WHERE {col_q} IS NOT NULL AND CAST({col_q} AS VARCHAR) != '' "
                f"ORDER BY 1 LIMIT 200"
            )

            call_args: dict[str, Any] = {}
            for k in ("engine", "connection_id"):
                v = params_dict.get(k) or qp.get(k)
                if v is not None:
                    call_args[k] = v
            call_args["action"] = "run_query"
            call_args["query"] = distinct_sql

            try:
                legacy = getattr(tm, "_legacy_tools", {})
                tool = legacy.get("warehouse_query") or tm._tools.get("warehouse_query")
                if tool is None:
                    continue
                raw_result = await asyncio.wait_for(tool.run(call_args), timeout=_QUERY_TIMEOUT_S)
                if raw_result.get("error"):
                    logger.debug(
                        "filter-options distinct query error dim=%s: %s",
                        dim_key,
                        raw_result.get("message"),
                    )
                    continue
                rows = raw_result.get("rows") or []
                values = [str(r.get("val", r.get(dim_key, ""))) for r in rows if r]
                result_options[dim_key] = [v for v in values if v]
            except Exception as exc:
                logger.warning("filter-options error dim_key=%s: %s", dim_key, exc)

    return result_options


@router.get("/{slug}/meta")
async def dashboard_meta(slug: str, request: Request):
    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Dashboard).where(Dashboard.share_slug == slug))
        dash = result.scalar_one_or_none()
    if not dash:
        raise HTTPException(404, "Dashboard not found")
    if not await _can_view_dashboard(request, dash):
        raise HTTPException(404, "Dashboard not found")
    return {
        "slug": dash.share_slug,
        "scopes": dash.query_scopes or [],
        "token_required": dash.query_token_required,
        "updated_at": dash.updated_at.isoformat() if dash.updated_at else None,
    }
