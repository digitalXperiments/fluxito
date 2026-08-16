"""
Dashboard MCP Tools — host model-authored Streamlit artifacts.

Primary path (the only way to create or update a dashboard):
  get_dashboard_authoring_guide  — contract for the Streamlit app + manifest
  get_dashboard_query_recipe     — exact action/params per connection type
  list_dashboard_connections     — bindable aliases + recipe (no secrets)
  validate_dashboard_artifact    — reject secrets / invalid entrypoints
  deploy_dashboard               — create a kind=hosted dashboard
  bind_dashboard                 — attach connection aliases; host injects creds
  update_dashboard               — replace the artifact and restart the host
  delete_dashboard               — stop the host and drop the row
  list_dashboards / get_dashboard / dashboard_read — inspect

Do not emit card JSON. dashboard_deploy_batch, dashboard_create,
dashboard_card_upsert, dashboard_card_remove, and dashboard_card_preview are
unregistered. Leftover legacy card rows remain readable via dashboard_read.

Sharing, scheduling, and PDF export stay on the /live-dashboards/{slug} web UI.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

import app.app_state as state
from app.config import settings
from app.dashboards.chart_spec import validate_chart_config

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
    "apple_ads",
    "google_ads",
    "amplitude",
    "mixpanel",
    "posthog",
    "adobe_analytics",
    "search_console",
    "gtm",
    "adobe_launch",
    "adobe_marketo",
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
VALID_CHART_TYPES = {
    # legacy 7 — exposed end-to-end since the original dashboard launch
    "scorecard",
    "bar",
    "line",
    "pie",
    "table",
    "audit",
    "list",
    # first-class as of the dashboard revamp (Phase 1) — previously only
    # reachable as a chart_config.type sub-mode of bar/line/pie
    "area",
    "stacked_bar",
    "hbar",
    "donut",
    # net-new chart families (ECharts already supports these; wiring is new)
    "combo",
    "scatter",
    "heatmap",
    "funnel",
    "treemap",
    "radar",
    "gauge",
    "waterfall",
}
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
    ("mixpanel", "query_events"): ["connection_id", "start_date", "end_date", "events"],
    ("posthog", "query_events"): ["connection_id", "start_date", "end_date", "events"],
    ("adobe_analytics", "run_report"): [
        "connection_id",
        "report_suite_id",
        "metrics",
        "start_date",
        "end_date",
    ],
    # NOTE: keys MUST use the VALID_PLATFORMS names (meta_ads/tiktok_ads/snap_ads).
    # They previously used the short marketing-tool names (meta/tiktok/snap), which
    # never matched a card's platform, so these cards skipped required-param
    # validation entirely (stress-test 2026-06-12, FINDINGS S1 #7).
    ("meta_ads", "get_campaigns"): ["ad_account_id"],
    ("meta_ads", "get_campaign_performance"): ["ad_account_id", "start_date", "end_date"],
    ("tiktok_ads", "get_campaigns"): ["advertiser_id"],
    ("tiktok_ads", "get_campaign_performance"): ["advertiser_id", "start_date", "end_date"],
    ("snap_ads", "get_campaigns"): ["ad_account_id"],
    ("snap_ads", "get_campaign_performance"): ["ad_account_id", "start_date", "end_date"],
    ("apple_ads", "get_campaign_performance"): ["account_id", "start_date", "end_date"],
    ("google_ads", "get_campaigns"): ["customer_id"],
    ("google_ads", "get_campaign_performance"): ["customer_id", "start_date", "end_date"],
    ("search_console", "get_search_analytics"): ["site_url", "start_date", "end_date"],
    ("search_console", "list_sites"): [],
    # gtm cards dispatch through tagmanager_read(platform="gtm", ...) — verified
    # against app/tools/tagmanager_tools.py: every action below hard-requires
    # account_id + container_id there ("account_id and container_id are
    # required for '{action}'"); list_accounts/list_containers need neither
    # and are intentionally NOT listed here (no hard requirement to enforce).
    ("gtm", "get_container_summary"): ["account_id", "container_id"],
    ("gtm", "list_workspaces"): ["account_id", "container_id"],
    ("gtm", "list_tags"): ["account_id", "container_id"],
    ("gtm", "list_triggers"): ["account_id", "container_id"],
    ("gtm", "list_variables"): ["account_id", "container_id"],
    # adobe_launch cards dispatch through tagmanager_read(platform="adobe_launch").
    # Verified against tagmanager_tools.py: list_properties needs account_id
    # (=company_id), get_property/list_rules need container_id (=property_id).
    ("adobe_launch", "list_properties"): ["account_id"],
    ("adobe_launch", "get_property"): ["container_id"],
    ("adobe_launch", "list_rules"): ["container_id"],
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
        # A card with no action is dispatched to its tool with action=None, which
        # every action-based read tool (analytics_read, marketing_read, seo_read,
        # warehouse_query, …) rejects at refresh ("action: Input should be a valid
        # string"). Fail fast at deploy with an actionable message instead of
        # storing a card that silently returns no data. (Was: returned [] and let
        # the broken card through — root cause of the empty-dashboard bug.)
        suggestions = sorted({a for (p, a) in _CARD_PARAM_REQUIREMENTS if p == platform})
        hint = f" e.g. {suggestions[0]!r}" if suggestions else ""
        known = f" Known {platform} actions: {suggestions}." if suggestions else ""
        return [
            f"card '{key}' ({platform}): \"action\" is required{hint} — set the card's "
            f'top-level "action" field (a sibling of "tool" and "params").{known}'
        ]
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


_ISO_DATE_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_filter_presets(presets: list) -> list[dict]:
    """Validate and normalise filter_presets list.

    Each entry must be a dict with:
      label (str)  — button text shown in the UI
      start (str)  — ISO date YYYY-MM-DD
      end   (str)  — ISO date YYYY-MM-DD

    Invalid entries are silently dropped so a bad preset doesn't block a deploy.
    Returns a cleaned list (max 10 presets).
    """
    if not isinstance(presets, list):
        return []
    out: list[dict] = []
    for p in presets:
        if not isinstance(p, dict):
            continue
        label = str(p.get("label") or "").strip()[:60]
        start = str(p.get("start") or "").strip()
        end = str(p.get("end") or "").strip()
        if not label or not _ISO_DATE_RE.match(start) or not _ISO_DATE_RE.match(end):
            continue
        out.append({"label": label, "start": start, "end": end})
        if len(out) >= 10:
            break
    return out


# GA4/analytics dimension API-names worth offering as a dropdown filter, mapped to
# a human label. Used by _suggest_filters when the caller omits `filters`.
_SUGGESTABLE_DIMS = {
    "country": "Country",
    "city": "City",
    "region": "Region",
    "deviceCategory": "Device",
    "browser": "Browser",
    "operatingSystem": "OS",
    "language": "Language",
    "sessionDefaultChannelGroup": "Channel",
    "sessionSource": "Source",
    "sessionMedium": "Medium",
    "sessionCampaignName": "Campaign",
    "newVsReturning": "User type",
    "landingPage": "Landing page",
    "pagePath": "Page",
}


def _suggest_filters(validated_cards: list[dict]) -> list[dict]:
    """Infer dropdown filter suggestions from the cards' dimensions.

    Date presets + a custom range are always rendered by the filter bar, so this
    only suggests dimension dropdowns. Returns normalized single_select specs the
    assistant can present to the user, then pass back (wired with filter_hooks) on
    a follow-up deploy. Pure — no DB or connector calls.
    """
    seen: dict[str, dict] = {}
    for c in validated_cards:
        params = c.get("params") or {}
        dims = params.get("dimensions")
        if not isinstance(dims, list):
            continue
        for d in dims:
            if d in _SUGGESTABLE_DIMS and d not in seen:
                seen[d] = {
                    "key": d,
                    "label": _SUGGESTABLE_DIMS[d],
                    "type": "single_select",
                    "options": {"source": "static", "values": [""]},
                    "default": "",
                    "ui": {},
                }
    return list(seen.values())


def _validate_one_card_spec(i: int, c: dict, seen_keys: set[str]) -> tuple[dict, list[str], list[str]]:
    """Validate + normalize ONE card spec — the per-card body shared by both
    ``_validate_card_specs`` (a whole ``cards`` batch, dashboard_deploy_batch)
    and ``_validate_single_card_spec`` (one card, dashboard_card_preview /
    dashboard_card_upsert).

    Two-tier error model, same as the batch validator:
      * Structural errors (missing/wrong-typed ``key``/``title``/``chart_type``/
        ``platform``/``tool``/``params``/etc.) raise ``ValueError`` immediately —
        these mean the caller sent a malformed object, not a fixable data issue.
      * Per-tool param errors (unknown platform/tool, missing required params,
        invalid chart_config) are returned in the ``errors`` list so the caller
        can aggregate them (a batch call surfaces all card errors in one retry).

    Returns ``(normalized_card, errors, chart_warnings)``:
      normalized_card — dict with defaults applied, ready to store/dispatch
      errors          — non-fatal per-tool validation errors (caller raises)
      chart_warnings  — non-fatal chart_config warnings (e.g. unknown chart_type)
    """
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

    param_errors: list[str] = []

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

    # chart_config schema validation — normalize against the chart_type's
    # formal model; aggregate failures the same way as param_errors so a
    # single retry can fix every card.
    chart_warnings: list[str] = []
    try:
        normalized_chart_config, chart_warnings = validate_chart_config(chart_type, chart_config)
    except ValueError as exc:
        param_errors.append(f"cards[{i}] ({key}): {exc}")
        normalized_chart_config = chart_config or {}

    normalized = {
        "key": key,
        "title": str(title).strip()[:MAX_TITLE_LEN],
        "chart_type": chart_type,
        "platform": platform,
        "tool": tool,
        "action": action,
        "params": params,
        "chart_config": normalized_chart_config or {},
        "filter_hooks": hooks or {},
        "filter_options": filter_options or {},
    }
    return normalized, param_errors, chart_warnings


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
      5. chart_config — validated + normalized against the chart_type's
         formal schema (``app.dashboards.chart_spec``). Legacy shapes (e.g.
         chart_type='bar' + chart_config.type='stacked_bar') are accepted
         unchanged; only genuinely malformed shapes are rejected.

    Raises ``ValueError`` with *all* missing-field errors aggregated so Claude
    can fix every card in one retry instead of rediscovering issues on live
    refresh.

    Per-card validation lives in ``_validate_one_card_spec`` (shared with the
    single-card path used by dashboard_card_preview / dashboard_card_upsert).
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
        # Chart warnings (e.g. unknown chart_type, already a hard error above)
        # are non-fatal and simply dropped in the batch path, same as before.
        normalized, errs, _chart_warnings = _validate_one_card_spec(i, c, seen_keys)
        out.append(normalized)
        param_errors.extend(errs)

    if param_errors:
        raise ValueError("Card spec validation failed:\n  - " + "\n  - ".join(param_errors))

    return out


def _validate_single_card_spec(card: dict) -> tuple[dict, list[str]]:
    """Validate ONE card spec (dashboard_card_preview / dashboard_card_upsert).

    Same checks as a single entry of ``_validate_card_specs`` minus the
    key-uniqueness check (only meaningful across a batch). Raises
    ``ValueError`` for structural errors or aggregated param/chart_config
    errors, matching the batch validator's error format.

    Returns ``(normalized_card, chart_warnings)``.
    """
    if not isinstance(card, dict):
        raise ValueError("card must be an object")
    normalized, errors, chart_warnings = _validate_one_card_spec(0, card, set())
    if errors:
        raise ValueError("Card spec validation failed:\n  - " + "\n  - ".join(errors))
    return normalized, chart_warnings


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
    from app.dashboards.service import hosted_payload

    kind = getattr(dash, "kind", None) or "legacy_cards"
    if kind == "hosted":
        d = hosted_payload(dash, include_manifest=include_cards)
        d["owner_email"] = getattr(dash, "owner_email", None) or ""
        d["owner_name"] = getattr(dash, "owner_name", None)
        d["card_count"] = 0
        return d
    base = settings.APP_BASE_URL
    live_url = f"{base}/live-dashboards/{dash.share_slug}"
    d = {
        "id": str(dash.id),
        "title": dash.title,
        "description": dash.description,
        "kind": kind,
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
    # Hosted web dashboards — PRIMARY
    # -------------------------------------------------------------------------

    @mcp_server.tool("get_dashboard_authoring_guide")
    async def get_dashboard_authoring_guide() -> dict:
        """Return the complete contract for building a Fluxito-hosted web dashboard.

        REQUIRED FIRST CALL before writing any dashboard code. Fluxito hosts a
        production HTML/JS build on an isolated origin. It does not compile JSX
        or run Streamlit. Follow the returned `flow` exactly.

        Returns:
          guide — markdown contract (build, manifest, forbidden items, example)
          recipes — per-type {action, send, injected, example_params, call}
          connection_types / connection_tools — bindable types and host tools
          helper_api — fluxito.query / fluxito.rows
          flow — required tool sequence

        After this, call list_dashboard_connections, build locally (Vite
        base: './'), send index.html + assets + manifest.json, then
        validate_dashboard_artifact. Do not emit Streamlit, card JSON, or
        source .jsx. Do not put secrets in the bundle.
        """
        from app.dashboards.authoring_guide import authoring_guide_payload

        return authoring_guide_payload()

    @mcp_server.tool("get_dashboard_query_recipe")
    async def get_dashboard_query_recipe(connection_type: str | None = None) -> dict:
        """Return the exact fluxito.query contract for a connection type.

        Call this when you are about to write fluxito.query(...) and are unsure of
        the action or params. Do not invent actions.

        connection_type: e.g. "ga4", "google_ads", "bigquery". Omit to get every
        type. Returns action, send (params you write), injected (host overwrites
        — never send), example_params, and a ready-to-paste `call` string.

        You still cannot pass a tool name. The host maps type → MCP tool.
        """
        from app.dashboards.query_recipes import all_recipes, recipe_for

        if connection_type:
            rec = recipe_for(connection_type)
            if rec is None:
                return {
                    "error": True,
                    "error_type": "unknown_type",
                    "message": (
                        f"Unknown connection type {connection_type!r}. "
                        f"Known: {', '.join(sorted(all_recipes()))}."
                    ),
                    "recipes": all_recipes(),
                }
            return rec
        return {"recipes": all_recipes()}

    @mcp_server.tool("validate_dashboard_artifact")
    async def validate_dashboard_artifact(
        files: dict,
        manifest: dict | None = None,
        title: str | None = None,
    ) -> dict:
        """Validate a web dashboard artifact without persisting it.

        files: path → UTF-8 source. Must include index.html (or manifest.entrypoint)
        and manifest.json unless `manifest` is passed separately.

        Send the production build. Fluxito does not compile JSX.

        Rejects secrets, .env, remote scripts, source .jsx/.tsx/.py, Streamlit,
        card JSON, invalid entrypoints, and path traversal. Call this before
        deploy_dashboard and fix every error.
        """
        from app.dashboards.artifact import ArtifactError, validate_artifact

        try:
            artifact = validate_artifact(files, manifest, fallback_title=title)
        except ArtifactError as exc:
            return {
                "ok": False,
                "error": True,
                "error_type": "invalid_artifact",
                "message": str(exc),
                "errors": exc.errors,
            }
        return {
            "ok": True,
            "digest": artifact.digest,
            "manifest": artifact.manifest.to_dict(),
            "warnings": artifact.warnings,
            "file_count": len(artifact.files),
        }

    @mcp_server.tool("deploy_dashboard")
    async def deploy_dashboard(
        title: str,
        files: dict,
        description: str | None = None,
        manifest: dict | None = None,
    ) -> dict:
        """Create and host a model-authored web dashboard.

        Prerequisite: get_dashboard_authoring_guide → list_dashboard_connections
        → validate_dashboard_artifact (ok=true). files must be a production
        build (manifest.json + index.html + assets). Query live data with
        fluxito.query(alias, action, params) using recipes from the guide.

        Writes the artifact to an isolated working directory, binds connection
        aliases to this project's stored credentials (never put secrets in
        files), and returns dashboard_id, slug, url, host_status, bindings.
        Then call bind_dashboard if any alias is missing.
        """
        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        proj_ctx = state.current_project_ctx.get()
        from app.dashboards.service import deploy_hosted

        return await deploy_hosted(
            title=title,
            files=files,
            description=description,
            manifest=manifest,
            user=u,
            project_id=proj_ctx.project_id if proj_ctx else None,
        )

    @mcp_server.tool("update_dashboard")
    async def update_dashboard(
        dashboard_id: str,
        files: dict,
        title: str | None = None,
        description: str | None = None,
        manifest: dict | None = None,
    ) -> dict:
        """Replace a hosted dashboard's artifact (production HTML/JS build).

        dashboard_id is the UUID from deploy_dashboard / list_dashboards.
        Same file + manifest contract as deploy_dashboard.
        """
        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        from app.dashboards.service import update_hosted

        return await update_hosted(
            dashboard_id=dashboard_id,
            files=files,
            title=title,
            description=description,
            manifest=manifest,
            user=u,
        )

    @mcp_server.tool("list_dashboards")
    async def list_dashboards() -> dict:
        """List hosted and legacy dashboards in the active project. Same as dashboard_list."""
        return await _list_dashboards_impl()

    @mcp_server.tool("delete_dashboard")
    async def delete_dashboard(dashboard_id: str) -> dict:
        """Delete a dashboard you own: wipe the working dir and drop the row.

        dashboard_id is the UUID. Irreversible.
        """
        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        from app.dashboards.service import delete_hosted

        return await delete_hosted(dashboard_id, u)

    @mcp_server.tool("list_dashboard_connections")
    async def list_dashboard_connections() -> dict:
        """List bindable project connections for manifest.connections[].

        Returns type, suggested_alias, label, resource, status, and the
        matching query `recipe` (action + send + example_params). Never
        returns tokens or secrets. Call this before writing the manifest.

        Use suggested_alias as manifest.connections[].alias and recipe.action
        / recipe.example_params in fluxito.query. Do not invent params.
        """
        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        proj_ctx = state.current_project_ctx.get()
        project_id = None
        if proj_ctx:
            project_id = uuid.UUID(proj_ctx.project_id)
        from app.dashboards.connections import list_bindable_connections
        from app.dashboards.query_recipes import recipe_for

        items = await list_bindable_connections(project_id, uuid.UUID(u.user_id))
        # Suggest aliases the model can paste into the manifest.
        used: dict[str, int] = {}
        connections = []
        for item in items:
            base = item["type"]
            n = used.get(base, 0)
            used[base] = n + 1
            alias = base if n == 0 else f"{base}_{n + 1}"
            connections.append({**item, "suggested_alias": alias, "recipe": recipe_for(base)})
        return {
            "connections": connections,
            "hint": (
                "Put each needed source in manifest.connections as "
                '{"alias": suggested_alias, "type": type}. Query with '
                "fluxito.query(alias, action=recipe.action, params=recipe.example_params). "
                "Never inline secrets. After deploy, call bind_dashboard."
            ),
        }

    @mcp_server.tool("bind_dashboard")
    async def bind_dashboard(
        dashboard_id: str,
        bindings: list | None = None,
    ) -> dict:
        """Attach live project connections to a hosted dashboard's aliases.

        dashboard_id is the UUID from deploy_dashboard. bindings is optional:
        a list of {alias, type, connection_id?}. The host maps type → MCP tool
        and injects stored credentials — you cannot pass a tool name. If
        bindings is omitted, Fluxito rebinds every alias in the manifest from
        this project's available connections.
        """
        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        from app.dashboards.service import bind_hosted

        return await bind_hosted(dashboard_id=dashboard_id, bindings=bindings, user=u)

    async def _list_dashboards_impl() -> dict:
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
                if getattr(dash, "kind", None) != "hosted":
                    cards_result = await db.execute(
                        select(DashboardCard).where(DashboardCard.dashboard_id == dash.id)
                    )
                    dash.cards = cards_result.scalars().all()
                output.append(_dashboard_to_dict(dash, include_cards=False))

        base = settings.APP_BASE_URL
        return {
            "dashboards": output,
            "total": len(output),
            "create_hint": (
                "Call get_dashboard_authoring_guide, then list_dashboard_connections, "
                "validate_dashboard_artifact, deploy_dashboard, then bind_dashboard. "
                "Do not emit card JSON."
            ),
            "manage_url": f"{base}/live-dashboards",
            "sharing_hint": (
                "Open the live hosted app at /live-dashboards/{slug}. "
                "Sharing and scheduled sends stay on that page."
            ),
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
        Existing tokens are rejected immediately after rotation.
        Hosted dashboards keep using the runtime token injected by the host.

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
                "Token rotated. Hosted dashboards do not need a redeploy; "
                "legacy public links using the old token will stop working."
            ),
        }

    # -------------------------------------------------------------------------
    # dashboard_list
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_list")
    async def dashboard_list() -> dict:
        """List hosted Streamlit dashboards (and any leftover legacy card rows) for this user."""
        return await _list_dashboards_impl()

    # -------------------------------------------------------------------------
    # dashboard_get
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_get")
    async def dashboard_get(dashboard_id: str) -> dict:
        """Get one dashboard by UUID: hosted app metadata + bindings, or legacy cards."""
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

    @mcp_server.tool("get_dashboard")
    async def get_dashboard(dashboard_id: str) -> dict:
        """Get one dashboard by UUID. Same as dashboard_get — hosted metadata or legacy cards."""
        return await dashboard_get(dashboard_id)
