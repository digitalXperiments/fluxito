"""
Dashboard MCP Tools — deploy, inspect, and manage live card-native dashboards.

Tools exposed to the AI (via the ``dashboard_*`` surface in unified.py):
  dashboard_deploy_batch    — PRIMARY: create or update a full dashboard in one call
  dashboard_manage_scopes   — manage which (platform, property_id) tuples the
                              dashboard's live-query endpoint is authorized for
  dashboard_rotate_token    — rotate the dashboard's public query_token

  dashboard_read dispatcher — action="list" | action="get" for read access
                              (delegates to the internal dashboard_list /
                              dashboard_get implementations preserved in
                              ``tool_manager._legacy_tools``)

─── Card Schema System ────────────────────────────────────────────────────────
Every card is stored as a structured spec (key, title, chart_type, platform,
tool, action, params, chart_config, filter_hooks). The frontend renders cards
natively — no HTML generation required.

  scorecard — single metric highlight
  bar       — bar chart
  line      — line chart
  pie       — pie/donut chart
  table     — tabular data
  audit     — findings/issues list
  list      — simple item list

Sharing (public links), scheduling (email/Slack sends), and PDF export are
strictly user-triggered actions from the /live-dashboards/{slug} web UI — there is no
MCP tool for them.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

import app.app_state as state
from app.config import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_PLATFORMS = {
    "ga4",
    "bigquery",
    "redshift",
    "snowflake",
    "meta_ads",
    "tiktok_ads",
    "snap_ads",
    "google_ads",
    "amplitude",
    "adobe_analytics",
    "search_console",
    "gtm",
    "adobe_launch",
}
VALID_TOOLS = {
    "analytics_read",
    "analytics_audit",
    "analytics_write",
    "tagmanager_read",
    "tagmanager_audit",
    "tagmanager_write",
    "marketing_read",
    "marketing_audit",
    "marketing_write",
    "warehouse_read",
    "warehouse_query",
    "warehouse_audit",
    "seo_read",
    "seo_write",
}
VALID_CHART_TYPES = {"scorecard", "bar", "line", "pie", "table", "audit", "list"}
MAX_CARDS_PER_DASHBOARD = 20

MAX_TITLE_LEN = 120
MAX_DESC_LEN = 400
MAX_INSIGHTS_LEN = 4000


# Per-(platform, action) required params. Only the most common actions are
# listed — missing entries skip hard validation so new tools keep working.
# Each value is a list of keys that must be present (and non-empty list for
# list-typed fields) in the card spec's ``params`` dict.
_CARD_PARAM_REQUIREMENTS: dict[tuple[str, str], list[str]] = {
    ("ga4", "run_report"): ["property_id", "metrics", "dimensions", "start_date", "end_date"],
    ("ga4", "get_realtime"): ["property_id", "metrics"],
    ("ga4", "list_properties"): [],
    ("bigquery", "run_query"): ["query"],
    ("redshift", "run_query"): ["connection_id", "query"],
    ("snowflake", "run_query"): ["connection_id", "query"],
    ("amplitude", "query_events"): ["connection_id", "start_date", "end_date", "events"],
    ("adobe_analytics", "run_report"): [
        "connection_id",
        "report_suite_id",
        "metrics",
        "start_date",
        "end_date",
    ],
    ("meta", "get_campaigns"): ["ad_account_id"],
    ("meta", "get_campaign_performance"): ["ad_account_id", "start_date", "end_date"],
    ("tiktok", "get_campaigns"): ["advertiser_id"],
    ("tiktok", "get_campaign_performance"): ["advertiser_id", "start_date", "end_date"],
    ("snap", "get_campaigns"): ["ad_account_id"],
    ("snap", "get_campaign_performance"): ["ad_account_id", "start_date", "end_date"],
    ("google_ads", "get_campaigns"): ["customer_id"],
    ("google_ads", "get_campaign_performance"): ["customer_id", "start_date", "end_date"],
    ("search_console", "get_search_analytics"): ["site_url", "start_date", "end_date"],
    ("search_console", "list_sites"): [],
}

# Fields whose value must be a non-empty list (not just non-None).
_CARD_PARAM_LIST_FIELDS = frozenset({"metrics", "dimensions", "events", "ad_account_ids"})


def _check_params_for_action(
    key: str,
    platform: str,
    action: str | None,
    params: dict,
) -> list[str]:
    """Return a list of missing-field error strings for a single card, or []."""
    if not action:
        return []
    required = _CARD_PARAM_REQUIREMENTS.get((platform, action))
    if required is None:
        return []
    errors: list[str] = []
    for field in required:
        val = params.get(field)
        if val is None or val == "":
            errors.append(f"card '{key}' ({platform}/{action}): params.{field} is required")
            continue
        if field in _CARD_PARAM_LIST_FIELDS:
            if not isinstance(val, list) or len(val) == 0:
                errors.append(f"card '{key}' ({platform}/{action}): params.{field} must be a non-empty list")
    return errors


def _validate_card_specs(cards: list | None) -> list[dict]:
    """Validate the ``cards`` list passed to dashboard_deploy_batch.

    Checks four layers:

      1. Structural — each entry has ``key``, ``title``, ``chart_type``,
         ``platform``, ``tool``, ``params`` of the correct types, and keys
         are unique.
      2. chart_type — must be one of the known chart types.
      3. Per-tool params — for known (platform, action) pairs, required
         fields (e.g. ``metrics`` + ``dimensions`` for GA4 ``run_report``)
         are present and non-empty.
      4. ``filter_hooks`` is an object if supplied.

    Raises ``ValueError`` with *all* missing-field errors aggregated so Claude
    can fix every card in one retry instead of rediscovering issues on live
    refresh.
    """
    if cards is None:
        return []
    if not isinstance(cards, list):
        raise ValueError("cards must be a list of card specs")

    out: list[dict] = []
    seen_keys: set[str] = set()
    param_errors: list[str] = []

    for i, c in enumerate(cards):
        if not isinstance(c, dict):
            raise ValueError(f"cards[{i}] must be an object")
        key = c.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"cards[{i}].key must be a non-empty string")
        key = key.strip()
        if key in seen_keys:
            raise ValueError(f"cards[{i}].key duplicates an earlier card: {key!r}")
        seen_keys.add(key)

        title = c.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"cards[{i}] ({key}): title must be a non-empty string")

        chart_type = c.get("chart_type")
        if not isinstance(chart_type, str) or not chart_type:
            raise ValueError(f"cards[{i}] ({key}): chart_type must be a non-empty string")
        if chart_type not in VALID_CHART_TYPES:
            raise ValueError(
                f"cards[{i}] ({key}): chart_type '{chart_type}' is not valid. "
                f"Must be one of: {', '.join(sorted(VALID_CHART_TYPES))}"
            )

        platform = c.get("platform")
        tool = c.get("tool")
        params = c.get("params")
        if not isinstance(platform, str) or not platform:
            raise ValueError(f"cards[{i}] ({key}): platform must be a non-empty string")
        if platform not in VALID_PLATFORMS:
            param_errors.append(
                f"cards[{i}] ({key}): unknown platform '{platform}', must be one of: {sorted(VALID_PLATFORMS)}"
            )
        if not isinstance(tool, str) or not tool:
            raise ValueError(f"cards[{i}] ({key}): tool must be a non-empty string")
        if tool not in VALID_TOOLS:
            param_errors.append(
                f"cards[{i}] ({key}): unknown tool '{tool}', must be one of: {sorted(VALID_TOOLS)}"
            )
        if not isinstance(params, dict):
            raise ValueError(f"cards[{i}] ({key}): params must be an object")
        action = c.get("action")
        if action is not None and not isinstance(action, str):
            raise ValueError(f"cards[{i}] ({key}): action must be a string or omitted")
        hooks = c.get("filter_hooks")
        if hooks is not None and not isinstance(hooks, dict):
            raise ValueError(f"cards[{i}] ({key}): filter_hooks must be an object or omitted")
        filter_options = c.get("filter_options")
        if filter_options is not None and not isinstance(filter_options, dict):
            raise ValueError(f"cards[{i}] ({key}): filter_options must be an object or omitted")
        chart_config = c.get("chart_config")
        if chart_config is not None and not isinstance(chart_config, dict):
            raise ValueError(f"cards[{i}] ({key}): chart_config must be an object or omitted")

        # Per-(platform, action) required-params check. Collect all errors so
        # Claude can fix every card in one retry.
        param_errors.extend(_check_params_for_action(key, platform, action, params))

        out.append(
            {
                "key": key,
                "title": str(title).strip()[:MAX_TITLE_LEN],
                "chart_type": chart_type,
                "platform": platform,
                "tool": tool,
                "action": action,
                "params": params,
                "chart_config": chart_config or {},
                "filter_hooks": hooks or {},
                "filter_options": filter_options or {},
            }
        )

    if param_errors:
        raise ValueError("Card spec validation failed:\n  - " + "\n  - ".join(param_errors))

    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user():
    return state.current_user_ctx.get()


def _make_slug() -> str:
    return secrets.token_urlsafe(6)


def _card_to_dict(card) -> dict:
    return {
        "id": str(card.id),
        "key": (card.query_params or {}).get("key"),
        "title": card.title,
        "platform": card.platform,
        "tool_name": card.tool_name,
        "chart_type": card.chart_type,
        "chart_config": card.chart_config,
        "query_params": card.query_params,
        "position": card.position,
        "refreshed_at": card.refreshed_at.isoformat() if card.refreshed_at else None,
        "created_at": card.created_at.isoformat() if card.created_at else None,
    }


def _dashboard_to_dict(dash, include_cards: bool = True) -> dict:
    base = settings.APP_BASE_URL
    live_url = f"{base}/live-dashboards/{dash.share_slug}"
    d = {
        "id": str(dash.id),
        "title": dash.title,
        "description": dash.description,
        "insights": getattr(dash, "insights", None),
        "owner_email": getattr(dash, "owner_email", None) or "",
        "owner_name": getattr(dash, "owner_name", None),
        "share_slug": dash.share_slug,
        "is_public": dash.is_public,
        "live_url": live_url,
        "share_url": getattr(dash, "share_url", None)
        or (f"{base}/d/{dash.share_slug}" if dash.is_public else None),
        "shared_at": dash.shared_at.isoformat() if getattr(dash, "shared_at", None) else None,
        "card_count": len(dash.cards) if hasattr(dash, "cards") and dash.cards else 0,
        "created_at": dash.created_at.isoformat() if dash.created_at else None,
        "updated_at": dash.updated_at.isoformat() if dash.updated_at else None,
    }
    if include_cards and hasattr(dash, "cards"):
        d["cards"] = [_card_to_dict(c) for c in (dash.cards or [])]
    return d


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_dashboard_tools(mcp_server):
    # -------------------------------------------------------------------------
    # dashboard_deploy_batch  ← PRIMARY ENTRY POINT
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_deploy_batch")
    async def dashboard_deploy_batch(
        title: str,
        cards: list[dict],
        description: str | None = None,
        dashboard_id: str | None = None,
        query_token_required: bool = False,
    ) -> dict:
        """Deploy a complete dashboard in a single call. PRIMARY tool for LLM dashboard creation.

        Creates a new dashboard (or updates existing if dashboard_id provided) and deploys
        all cards atomically. Returns the live URL immediately — no HTML generation needed.

        ── FILTER HOOKS RULE (mandatory when filters apply) ──────────────────────
        filter_hooks maps dashboard UI filter values to card params. Without it, the
        date-range chips and any dimension filters (country, device, campaign, etc.)
        have no effect on that card.

        Key   = the query-param name the browser sends  (e.g. "date_range.start",
                                                               "country", "device")
        Value = dot-path into the card's params where the value should be written
                (e.g. "start_date", "filters.country")

        Date filter example (REQUIRED for every card with start_date / end_date):
          filter_hooks: {"date_range.start": "start_date", "date_range.end": "end_date"}

        Dimension filter examples:
          filter_hooks: {"country": "country", "device": "device_category"}
          filter_hooks: {"date_range.start": "start_date",
                         "date_range.end":   "end_date",
                         "country":          "filters.country"}

        For warehouse (SQL) cards use {param_name} placeholders in the query string:
          WHERE order_date BETWEEN '{start_date}' AND '{end_date}'
          AND country = '{country}'

        Cards WITHOUT any filterable params (e.g. GTM audit, GA4 realtime) omit
        filter_hooks entirely.
        ───────────────────────────────────────────────────────────────────────────

        Each card in 'cards' must be a dict with:
          key (str): stable snake_case ID, unique within this batch (e.g. "sessions_score")
          title (str): human-readable card title
          chart_type (str): one of — scorecard, bar, line, pie, table, audit, list
          platform (str): one of — ga4, bigquery, redshift, snowflake, meta_ads, tiktok_ads,
                          snap_ads, google_ads, amplitude, adobe_analytics, search_console,
                          gtm, adobe_launch
          tool (str): MCP tool category (e.g. analytics_read, marketing_read, warehouse_query,
                      tagmanager_read, seo_read)
          action (str): tool action (e.g. run_report, run_query)
          params (dict): exact parameters for the tool call (platform-specific, see below)
          filter_hooks (dict): REQUIRED for any card with filterable params (dates,
                              dimensions) — see rule above. Omit only for cards with
                              no user-controllable filters.
          filter_options (dict): declare the dropdown options for each dimension filter
                              key so the UI can render a select instead of a text input.
                              Keys match the dimension keys in filter_hooks; values are
                              lists of strings (include "" as first item for "All"):
                                {"device_type": ["", "mobile", "desktop", "tablet"],
                                 "country":     ["", "AE", "SA", "EG", "US", "UK"]}
                              Omit if using date filters only.

        Optional card fields:
          chart_config (dict): display options:
            color_scheme (str): blue|green|amber|purple|red|teal|pink (default: blue)
            sparkline (bool): show mini trend bar on scorecards (default: true)
            unit (str): number|currency|percent|duration (default: number)
            stacked (bool): stacked bars/lines (default: false)
            donut (bool): donut vs pie chart (default: false)
            show_legend (bool): default true

        Required params by platform:
          ga4 + analytics_read/run_report:
            property_id, metrics (list), dimensions (list), start_date, end_date
            filter_hooks: {"date_range.start": "start_date", "date_range.end": "end_date"}
          ga4 + analytics_read/get_realtime:
            property_id, metrics (list), dimensions (list)
            (no date params → no filter_hooks needed)
          bigquery/redshift/snowflake + warehouse_query/run_query:
            connection_id (required for redshift/snowflake)
            query: SQL string with {start_date} and {end_date} placeholders, e.g.:
              WHERE order_date BETWEEN '{start_date}' AND '{end_date}'
            start_date: default ISO date (e.g. "2025-01-01") used when no UI filter active
            end_date: default ISO date (e.g. "2025-04-23")
            filter_hooks: {"date_range.start": "start_date", "date_range.end": "end_date"}
          meta_ads/tiktok_ads/snap_ads + marketing_read:
            ad_account_id or advertiser_id, start_date, end_date, fields (list)
            filter_hooks: {"date_range.start": "start_date", "date_range.end": "end_date"}
          google_ads + marketing_read:
            customer_id, start_date, end_date
            filter_hooks: {"date_range.start": "start_date", "date_range.end": "end_date"}
          search_console + seo_read:
            site_url, start_date, end_date
            filter_hooks: {"date_range.start": "start_date", "date_range.end": "end_date"}
          amplitude + analytics_read:
            connection_id, start_date, end_date, events (list)
            filter_hooks: {"date_range.start": "start_date", "date_range.end": "end_date"}
          adobe_analytics + analytics_read:
            connection_id, report_suite_id, metrics, dimensions, start_date, end_date
            filter_hooks: {"date_range.start": "start_date", "date_range.end": "end_date"}
          gtm + tagmanager_read:
            account_id, container_id
            (no date params → no filter_hooks needed)

        Returns:
          dashboard_id (str): UUID of dashboard
          url (str): live dashboard URL
          slug (str): share slug
          card_ids (dict): mapping of card key to card UUID
        """
        import secrets as _secrets

        from sqlalchemy import select as sa_select

        from app.models.dashboard import Dashboard, DashboardCard

        if not cards:
            return {"error": True, "message": "cards must be a non-empty list."}

        if len(cards) > MAX_CARDS_PER_DASHBOARD:
            return {
                "error": True,
                "message": f"Too many cards ({len(cards)}). Maximum {MAX_CARDS_PER_DASHBOARD} per dashboard.",
            }

        # Validate ALL card specs upfront — fail fast before any DB writes
        try:
            validated_cards = _validate_card_specs(cards)
        except ValueError as exc:
            return {"error": True, "message": str(exc)}

        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        uid = uuid.UUID(u.user_id)
        proj_ctx = state.current_project_ctx.get()

        from app.dashboards.scope import fingerprint

        project_id = uuid.UUID(proj_ctx.project_id) if proj_ctx else None

        async with state.db_session_factory() as db:
            if dashboard_id:
                try:
                    dash_uuid = uuid.UUID(dashboard_id)
                except (ValueError, AttributeError):
                    return {"error": True, "message": f"Invalid dashboard_id format: '{dashboard_id}'."}

                result = await db.execute(
                    sa_select(Dashboard).where(
                        Dashboard.id == dash_uuid,
                        Dashboard.user_id == uid,
                    )
                )
                dash = result.scalar_one_or_none()
                if not dash:
                    return {
                        "error": True,
                        "message": f"Dashboard '{dashboard_id}' not found or not yours.",
                    }
                # Update metadata fields if provided
                dash.title = title.strip()[:MAX_TITLE_LEN]
                if description is not None:
                    dash.description = (description or "")[:MAX_DESC_LEN] or None
            else:
                token = _secrets.token_urlsafe(32) if query_token_required else None
                dash = Dashboard(
                    user_id=uid,
                    project_id=project_id,
                    owner_email=u.email or "",
                    owner_name=getattr(u, "display_name", None),
                    title=title.strip()[:MAX_TITLE_LEN],
                    description=(description or "")[:MAX_DESC_LEN] or None,
                    share_slug=_make_slug(),
                    is_public=True,
                    query_scopes=[],
                    query_token=token,
                    query_token_required=query_token_required,
                )
                db.add(dash)
                await db.flush()

            # Build a map of existing cards keyed by their stored "key" field
            existing_result = await db.execute(
                sa_select(DashboardCard).where(DashboardCard.dashboard_id == dash.id)
            )
            existing_cards: dict[str, DashboardCard] = {}
            for ec in existing_result.scalars().all():
                ec_key = (ec.query_params or {}).get("key")
                if ec_key:
                    existing_cards[ec_key] = ec

            # Upsert all cards; track card_ids mapping key → UUID
            card_ids: dict[str, str] = {}
            # Compute next position beyond existing cards
            next_pos = max((c.position for c in existing_cards.values()), default=-1) + 1

            # Collect fingerprints to update query_scopes
            new_scopes: list[dict] = list(dash.query_scopes or [])

            for i, spec in enumerate(validated_cards):
                card_query_params = {
                    "key": spec["key"],
                    "platform": spec["platform"],
                    "tool": spec["tool"],
                    "action": spec["action"],
                    **spec["params"],
                    "filter_hooks": spec["filter_hooks"],
                    "filter_options": spec["filter_options"],
                }

                fp = fingerprint(spec["platform"], spec["params"])
                if fp not in new_scopes:
                    new_scopes.append(fp)

                if spec["key"] in existing_cards:
                    card_row = existing_cards[spec["key"]]
                    card_row.title = spec["title"]
                    card_row.platform = spec["platform"]
                    card_row.tool_name = spec["tool"]
                    card_row.chart_type = spec["chart_type"]
                    card_row.chart_config = spec["chart_config"]
                    card_row.query_params = card_query_params
                else:
                    card_row = DashboardCard(
                        dashboard_id=dash.id,
                        title=spec["title"],
                        platform=spec["platform"],
                        tool_name=spec["tool"],
                        chart_type=spec["chart_type"],
                        chart_config=spec["chart_config"],
                        query_params=card_query_params,
                        position=next_pos + i,
                    )
                    db.add(card_row)

                await db.flush()
                card_ids[spec["key"]] = str(card_row.id)

            # Update dashboard query_scopes from all card fingerprints
            dash.query_scopes = new_scopes
            dash.updated_at = datetime.now(UTC).replace(tzinfo=None)

            await db.commit()
            await db.refresh(dash)

        base = settings.APP_BASE_URL
        live_url = f"{base}/live-dashboards/{dash.share_slug}"
        return {
            "dashboard_id": str(dash.id),
            "url": live_url,
            "slug": dash.share_slug,
            "card_ids": card_ids,
        }

    # -------------------------------------------------------------------------
    # dashboard_manage_scopes
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_manage_scopes")
    async def dashboard_manage_scopes(
        dashboard_id: str,
        action: str,
        scopes: list | None = None,
    ) -> dict:
        """Manage which data sources a deployed dashboard can query.

        action:
          list    — return current scopes (no scopes param needed)
          add     — append new scopes to existing ones
          remove  — remove matching scope entries
          replace — set scopes to exactly the provided list

        scopes shape: [{"platform": "ga4", "property_id": "279951751"}, ...]
        Omit property_id to allow any property on that platform.
        """
        from sqlalchemy import select as sa_select

        from app.models.dashboard import Dashboard

        if action not in ("list", "add", "remove", "replace"):
            return {"error": True, "message": "action must be one of: list, add, remove, replace"}

        u = _user()
        uid = uuid.UUID(u.user_id)

        async with state.db_session_factory() as db:
            result = await db.execute(
                sa_select(Dashboard).where(
                    Dashboard.id == uuid.UUID(dashboard_id),
                    Dashboard.user_id == uid,
                )
            )
            dash = result.scalar_one_or_none()
            if not dash:
                return {"error": True, "message": f"Dashboard '{dashboard_id}' not found or not yours."}

            current: list = list(dash.query_scopes or [])

            if action == "list":
                return {"dashboard_id": dashboard_id, "query_scopes": current}

            if action == "replace":
                dash.query_scopes = scopes or []
            elif action == "add":
                for s in scopes or []:
                    if s not in current:
                        current.append(s)
                dash.query_scopes = current
            elif action == "remove":
                dash.query_scopes = [s for s in current if s not in (scopes or [])]

            dash.updated_at = datetime.now(UTC).replace(tzinfo=None)
            await db.commit()
            await db.refresh(dash)

        return {
            "success": True,
            "dashboard_id": dashboard_id,
            "action": action,
            "query_scopes": list(dash.query_scopes or []),
        }

    # -------------------------------------------------------------------------
    # dashboard_rotate_token
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_rotate_token")
    async def dashboard_rotate_token(dashboard_id: str) -> dict:
        """Rotate the query token for a token-gated dashboard.

        Returns the new token — shown only once; store it securely.
        Re-deploy the dashboard via dashboard_deploy_batch to update connected
        clients. Existing tokens will be rejected immediately after rotation.

        Use when a token has leaked or after a team member leaves.
        """
        import secrets as _secrets

        from sqlalchemy import select as sa_select

        from app.models.dashboard import Dashboard

        u = _user()
        uid = uuid.UUID(u.user_id)

        async with state.db_session_factory() as db:
            result = await db.execute(
                sa_select(Dashboard).where(
                    Dashboard.id == uuid.UUID(dashboard_id),
                    Dashboard.user_id == uid,
                )
            )
            dash = result.scalar_one_or_none()
            if not dash:
                return {"error": True, "message": f"Dashboard '{dashboard_id}' not found or not yours."}

            new_token = _secrets.token_urlsafe(32)
            dash.query_token = new_token
            dash.query_token_required = True
            dash.updated_at = datetime.now(UTC).replace(tzinfo=None)
            await db.commit()

        return {
            "success": True,
            "dashboard_id": dashboard_id,
            "query_token": new_token,
            "message": (
                "Token rotated. Call dashboard_deploy_batch with query_token_required=True "
                "to update the dashboard if needed."
            ),
        }

    # -------------------------------------------------------------------------
    # dashboard_list
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_list")
    async def dashboard_list() -> dict:
        """
        List all live dashboards belonging to the current user.

        Returns each dashboard's title, card count, live_url, and id.
        Use dashboard_get to fetch full card data. To delete, visit /dashboards.
        """
        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        uid = uuid.UUID(u.user_id)

        from sqlalchemy import select

        from app.models.dashboard import Dashboard, DashboardCard

        proj_ctx = state.current_project_ctx.get()
        async with state.db_session_factory() as db:
            q = select(Dashboard).where(Dashboard.user_id == uid).order_by(Dashboard.updated_at.desc())
            if proj_ctx:
                q = q.where(Dashboard.project_id == uuid.UUID(proj_ctx.project_id))
            result = await db.execute(q)
            dashboards = result.scalars().all()

            output = []
            for dash in dashboards:
                cards_result = await db.execute(
                    select(DashboardCard).where(DashboardCard.dashboard_id == dash.id)
                )
                dash.cards = cards_result.scalars().all()
                output.append(_dashboard_to_dict(dash, include_cards=False))

        base = settings.APP_BASE_URL
        return {
            "dashboards": output,
            "total": len(output),
            "create_hint": "Call dashboard_deploy_batch to create a new dashboard.",
            "manage_url": f"{base}/dashboards",
            "sharing_hint": (
                "Sharing, PDF export, and scheduled email/Slack sends are user-triggered "
                "from the /live-dashboards/{slug} page — there are no MCP tools for them."
            ),
        }

    # -------------------------------------------------------------------------
    # dashboard_get
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_get")
    async def dashboard_get(dashboard_id: str) -> dict:
        """
        Get a full live dashboard including all cards and their cached data.

        Parameters:
          dashboard_id — UUID from dashboard_list or dashboard_deploy_batch response
        """
        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        uid = uuid.UUID(u.user_id)

        # Validate dashboard_id format before querying
        try:
            parsed_dashboard_id = uuid.UUID(dashboard_id)
        except (ValueError, AttributeError):
            return {
                "error": True,
                "message": (
                    f"Invalid dashboard ID format: '{dashboard_id}'. "
                    "Dashboard IDs must be valid UUIDs. "
                    "Call dashboard_list to see your dashboards."
                ),
            }

        from sqlalchemy import select

        from app.models.dashboard import Dashboard, DashboardCard

        async with state.db_session_factory() as db:
            result = await db.execute(
                select(Dashboard).where(
                    Dashboard.id == parsed_dashboard_id,
                    Dashboard.user_id == uid,
                )
            )
            dash = result.scalar_one_or_none()
            if not dash:
                return {
                    "error": True,
                    "message": (
                        f"Dashboard '{dashboard_id}' not found. Call dashboard_list to see your dashboards."
                    ),
                }

            cards_result = await db.execute(
                select(DashboardCard)
                .where(DashboardCard.dashboard_id == dash.id)
                .order_by(DashboardCard.position)
            )
            dash.cards = cards_result.scalars().all()
            dash_dict = _dashboard_to_dict(dash, include_cards=True)

        return {"dashboard": dash_dict}
