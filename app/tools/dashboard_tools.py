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

─── Incremental card tools (chat/MCP build-as-you-go flow) ──────────────────
  dashboard_card_preview    — run a card's query + validate its chart spec
                              WITHOUT persisting anything (no dashboard needed)
  dashboard_create          — empty dashboard shell to build onto card-by-card
  dashboard_card_upsert     — add/update ONE card on an existing dashboard by key
  dashboard_card_remove     — delete ONE card from a dashboard by key
See ``dashboard_deploy_batch`` for the full per-platform param reference these
tools' ``card``/``params`` arguments share.

─── Card Schema System ────────────────────────────────────────────────────────
Every card is stored as a structured spec (key, title, chart_type, platform,
tool, action, params, chart_config, filter_hooks). The frontend renders cards
natively — no HTML generation required.

  scorecard   — single metric highlight
  bar         — bar chart
  line        — line chart
  pie         — pie/donut chart
  table       — tabular data
  audit       — findings/issues list
  list        — simple item list
  area        — filled line chart
  combo       — bar+line combo, optional dual axis
  stacked_bar — stacked bar chart
  hbar        — horizontal bar chart
  donut       — donut chart
  scatter     — XY scatter (optional bubble size)
  heatmap     — 2D heatmap (x/y categories, value intensity)
  funnel      — funnel chart
  treemap     — treemap (optional nested hierarchy)
  radar       — radar/spider chart
  gauge       — single-value gauge (optional min/max/target)
  waterfall   — waterfall chart

See ``app/dashboards/chart_spec.py`` for the formal per-type ``chart_config``
schema (also the source of ``validate_chart_config`` used below).

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
from app.dashboards import query_engine
from app.dashboards.chart_spec import validate_chart_config
from app.dashboards.scope import fingerprint
from app.dashboards.snapshot import normalize_snap

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
        filter_presets: list[dict] | None = None,
        filters: list[dict] | None = None,
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
          chart_type (str): one of — scorecard, bar, line, pie, table, audit, list,
                          area, combo, stacked_bar, hbar, donut, scatter, heatmap,
                          funnel, treemap, radar, gauge, waterfall
          platform (str): one of — ga4, bigquery, redshift, snowflake, meta_ads, tiktok_ads,
                          snap_ads, apple_ads, google_ads, amplitude, adobe_analytics, search_console,
                          gtm, adobe_launch
          tool (str): MCP tool category (e.g. analytics_read, marketing_read, warehouse_query,
                      tagmanager_read, seo_read)
          action (str): REQUIRED — the tool action this card runs, a top-level sibling of
                      `tool`/`params` (NOT inside params). e.g. ga4 → "run_report" (or
                      "get_realtime"); warehouse → "run_query"; search_console →
                      "get_search_analytics". A card with no action deploys but returns
                      NO DATA (the refresh dispatches action=None, which the tool rejects).
          params (dict): exact parameters for the tool call (platform-specific, see below).
                      Do NOT put `action` in here — it goes at the top level.
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
          meta_ads/tiktok_ads/snap_ads/apple_ads + marketing_read:
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

        filter_presets: optional list of custom date-range chips for the dashboard UI.
          Each entry must be: {"label": str, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
          Example: [{"label": "Year 2024", "start": "2024-01-01", "end": "2024-12-31"},
                    {"label": "Year 2025", "start": "2025-01-01", "end": "2025-12-31"}]
          These chips are shown alongside the built-in Last 7/30/90 day presets.
          Omit or pass null to keep built-in presets only.

        filters: optional dashboard-level filter widgets (the six types). Each entry:
          {"key": str, "label": str,
           "type": "single_select"|"multi_select"|"search"|"number_range"|
                   "toggle"|"date_range",
           "options": {"source": "static", "values": [...]}      # selects only
                      | {"source": "warehouse", "card": "<key>", "column": "..."},
           "toggle": {"applies": {"<dimension>": "<value>"}},     # toggle only
           "default": ...}
          Each filter must also be WIRED on every card it affects via that card's
          filter_hooks, mapping the filter key to a target:
            GA4:       {"country": "dimension_filter.country"}    (exact/in-list)
            warehouse: {"channel": "channel"}  with `... IN ({channel})` in the SQL
                       (multi_select), `ILIKE {q}` (search), `{x_min}/{x_max}` (range)
          Default date presets + a custom start/end range are ALWAYS shown — you do
          not declare a date_range filter for that. When `filters` is omitted, the
          response includes `suggested_filters` inferred from the cards' dimensions;
          confirm them with the user, then redeploy with `filters` + filter_hooks.

        Returns:
          dashboard_id (str): UUID of dashboard
          url (str): live dashboard URL
          slug (str): share slug
          card_ids (dict): mapping of card key to card UUID
          suggested_filters (list, optional): inferred dropdowns when no filters set
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

        # Validate dashboard-level filters (the six widget types). None => leave
        # existing filters untouched on update / empty on create, and suggest some.
        from app.dashboards.filter_specs import FilterSpecError, validate_filters

        try:
            validated_filters = validate_filters(filters) if filters is not None else None
        except FilterSpecError as exc:
            return {"error": True, "error_type": "invalid_filters", "message": str(exc)}

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
                if filter_presets is not None:
                    dash.filter_presets = _validate_filter_presets(filter_presets)
                if validated_filters is not None:
                    dash.filters = validated_filters
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
                    filter_presets=_validate_filter_presets(filter_presets or []),
                    filters=validated_filters or [],
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
        resp = {
            "dashboard_id": str(dash.id),
            "url": live_url,
            "slug": dash.share_slug,
            "card_ids": card_ids,
        }
        # When the caller didn't specify filters, suggest dimension dropdowns
        # inferred from the cards so the assistant can offer them to the user and
        # redeploy with `filters` (+ matching per-card filter_hooks). Date presets
        # and a custom start/end range are always present in the UI regardless.
        if filters is None:
            suggestions = _suggest_filters(validated_cards)
            if suggestions:
                resp["suggested_filters"] = suggestions
                resp["filter_hint"] = (
                    "No filters were set. Suggested dropdowns inferred from the cards' "
                    "dimensions are in 'suggested_filters'. Confirm with the user which to "
                    "keep, then redeploy passing `filters` plus a matching `filter_hooks` "
                    'on each consuming card (e.g. {"country": "dimension_filter.country"}).'
                )
        return resp

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
    # dashboard_card_preview  — incremental build: try before you buy
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_card_preview")
    async def dashboard_card_preview(
        platform: str,
        tool: str,
        action: str,
        params: dict,
        chart_type: str,
        chart_config: dict | None = None,
        title: str | None = None,
    ) -> dict:
        """Preview ONE card's live data + chart spec. Persists nothing.

        Runs the exact query/normalize path a deployed card would use (via
        ``app.dashboards.query_engine`` + ``normalize_snap``) and validates
        ``chart_config`` against ``chart_type``'s formal schema
        (``app.dashboards.chart_spec``). Use this to show a live preview
        before calling dashboard_card_upsert to actually add the card — no
        dashboard needs to exist yet, and nothing is written to the database.

        Parameters mirror one card spec from dashboard_deploy_batch's `cards`
        list — see that tool's docstring for the full per-platform param
        reference (required params by platform, filter_hooks rules, etc.):
          platform, tool, action, params — the tool call to execute
          chart_type, chart_config       — the display spec to validate
          title                          — optional, cosmetic only (echoed
                                            back in normalized_spec)

        Returns:
          snap (dict): normalized query result (see app.dashboards.snapshot)
          normalized_spec (dict): the validated card spec (key="__preview__"),
                                   chart_config normalized against its schema
          warnings (list[str]): non-fatal chart_config warnings
        """
        fake_card = {
            "key": "__preview__",
            "title": (title or "Preview").strip()[:MAX_TITLE_LEN] or "Preview",
            "chart_type": chart_type,
            "platform": platform,
            "tool": tool,
            "action": action,
            "params": params,
            "chart_config": chart_config,
        }
        try:
            validated, warnings = _validate_single_card_spec(fake_card)
        except ValueError as exc:
            return {"error": True, "error_type": "invalid_card_spec", "message": str(exc)}

        tm = mcp_server._tool_manager
        try:
            raw_result = await query_engine.run_card(
                tm,
                validated,
                tool_name=validated["tool"],
                action=validated["action"],
            )
        except ValueError as exc:
            return {"error": True, "error_type": "tool_not_registered", "message": str(exc)}
        except TimeoutError:
            return {
                "error": True,
                "error_type": "timeout",
                "message": "Query timed out after 25s.",
            }
        except Exception as exc:  # connector/auth/upstream error — surface, don't persist
            return {"error": True, "error_type": "query_failed", "message": str(exc)}

        snap = normalize_snap(raw_result, validated["chart_type"], validated["chart_config"])

        return {
            "snap": snap,
            "normalized_spec": validated,
            "warnings": warnings,
        }

    # -------------------------------------------------------------------------
    # dashboard_create — empty dashboard shell for build-as-you-go
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_create")
    async def dashboard_create(title: str, description: str | None = None) -> dict:
        """Create an empty live dashboard — zero cards.

        Use this to start a build-as-you-go dashboard: create it once, then
        add cards one at a time with dashboard_card_upsert (each call auto-
        extends query_scopes for that card's data source). A card-less
        dashboard renders fine everywhere (live view, public share, PDF) —
        the card grid is simply empty until the first upsert.

        Returns:
          dashboard_id (str), url (str), slug (str)
        """
        if not title or not title.strip():
            return {"error": True, "message": "title must be a non-empty string."}

        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        uid = uuid.UUID(u.user_id)
        proj_ctx = state.current_project_ctx.get()
        project_id = uuid.UUID(proj_ctx.project_id) if proj_ctx else None

        from app.models.dashboard import Dashboard

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
            filter_presets=[],
            filters=[],
            query_token=None,
            query_token_required=False,
        )
        async with state.db_session_factory() as db:
            db.add(dash)
            await db.commit()
            await db.refresh(dash)

        base = settings.APP_BASE_URL
        return {
            "dashboard_id": str(dash.id),
            "url": f"{base}/live-dashboards/{dash.share_slug}",
            "slug": dash.share_slug,
        }

    # -------------------------------------------------------------------------
    # dashboard_card_upsert — add/update ONE card by key
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_card_upsert")
    async def dashboard_card_upsert(dashboard_slug: str, card: dict) -> dict:
        """Add or update ONE card on an existing dashboard, keyed by ``card.key``.

        Companion to dashboard_create + dashboard_card_preview for the
        incremental build flow: create an empty dashboard, preview a card,
        then upsert it one at a time — instead of assembling the whole
        `cards` list upfront for dashboard_deploy_batch.

        Parameters:
          dashboard_slug (str): the dashboard's share_slug (from
                                 dashboard_create or dashboard_deploy_batch's
                                 `slug` response field)
          card (dict): one card spec — same shape as an entry in
                       dashboard_deploy_batch's `cards` list (key, title,
                       chart_type, platform, tool, action, params,
                       chart_config, filter_hooks, filter_options). See that
                       tool's docstring for the full per-platform param
                       reference.

        Behaviour:
          * A card whose `key` matches an existing card on this dashboard is
            UPDATED in place (same position); a new key is APPENDED.
          * `query_scopes` is auto-extended with this card's (platform,
            params) fingerprint if not already covered — no separate
            dashboard_manage_scopes call needed for cards you're adding.
          * Enforces the 20-card-per-dashboard cap (a card being UPDATED
            doesn't count against the cap; only new cards do).

        Returns:
          card_key (str), dashboard_url (str), position (int)
        """
        if not isinstance(card, dict):
            return {"error": True, "message": "card must be an object."}

        try:
            validated, _warnings = _validate_single_card_spec(card)
        except ValueError as exc:
            return {"error": True, "error_type": "invalid_card_spec", "message": str(exc)}

        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        uid = uuid.UUID(u.user_id)

        from sqlalchemy import select as sa_select

        from app.models.dashboard import Dashboard, DashboardCard

        async with state.db_session_factory() as db:
            result = await db.execute(
                sa_select(Dashboard).where(
                    Dashboard.share_slug == dashboard_slug,
                    Dashboard.user_id == uid,
                )
            )
            dash = result.scalar_one_or_none()
            if not dash:
                return {
                    "error": True,
                    "message": f"Dashboard '{dashboard_slug}' not found or not yours.",
                }

            existing_result = await db.execute(
                sa_select(DashboardCard).where(DashboardCard.dashboard_id == dash.id)
            )
            existing_cards = list(existing_result.scalars().all())
            existing_by_key: dict[str, DashboardCard] = {}
            for ec in existing_cards:
                ec_key = (ec.query_params or {}).get("key")
                if ec_key:
                    existing_by_key[ec_key] = ec

            is_update = validated["key"] in existing_by_key
            if not is_update and len(existing_cards) >= MAX_CARDS_PER_DASHBOARD:
                return {
                    "error": True,
                    "error_type": "card_limit_reached",
                    "message": (
                        f"Dashboard '{dashboard_slug}' already has {len(existing_cards)} cards "
                        f"(max {MAX_CARDS_PER_DASHBOARD}). Remove a card first with "
                        "dashboard_card_remove."
                    ),
                }

            card_query_params = {
                "key": validated["key"],
                "platform": validated["platform"],
                "tool": validated["tool"],
                "action": validated["action"],
                **validated["params"],
                "filter_hooks": validated["filter_hooks"],
                "filter_options": validated["filter_options"],
            }

            if is_update:
                card_row = existing_by_key[validated["key"]]
                card_row.title = validated["title"]
                card_row.platform = validated["platform"]
                card_row.tool_name = validated["tool"]
                card_row.chart_type = validated["chart_type"]
                card_row.chart_config = validated["chart_config"]
                card_row.query_params = card_query_params
                position = card_row.position
            else:
                position = max((c.position for c in existing_cards), default=-1) + 1
                card_row = DashboardCard(
                    dashboard_id=dash.id,
                    title=validated["title"],
                    platform=validated["platform"],
                    tool_name=validated["tool"],
                    chart_type=validated["chart_type"],
                    chart_config=validated["chart_config"],
                    query_params=card_query_params,
                    position=position,
                )
                db.add(card_row)

            # Auto-extend query_scopes with this card's fingerprint, mirroring
            # dashboard_deploy_batch's scope-update behaviour, so a card added
            # incrementally is immediately authorized for live refresh without
            # a separate dashboard_manage_scopes call.
            fp = fingerprint(validated["platform"], validated["params"])
            scopes = list(dash.query_scopes or [])
            if fp not in scopes:
                scopes.append(fp)
                dash.query_scopes = scopes

            dash.updated_at = datetime.now(UTC).replace(tzinfo=None)
            await db.commit()
            await db.refresh(dash)

        base = settings.APP_BASE_URL
        return {
            "card_key": validated["key"],
            "dashboard_url": f"{base}/live-dashboards/{dash.share_slug}",
            "position": position,
        }

    # -------------------------------------------------------------------------
    # dashboard_card_remove — delete ONE card by key
    # -------------------------------------------------------------------------

    @mcp_server.tool("dashboard_card_remove")
    async def dashboard_card_remove(dashboard_slug: str, card_key: str) -> dict:
        """Remove ONE card from a dashboard by its stable key.

        Parameters:
          dashboard_slug (str): the dashboard's share_slug
          card_key (str): the card's `key` (as set on dashboard_deploy_batch /
                          dashboard_card_upsert) — NOT its UUID.

        Note: an earlier revision of the tracking-plan schema (migration 063)
        linked tp_metrics rows to a dashboard card via tp_metrics.dashboard_card_id,
        which would have needed nulling out here to avoid a dangling reference.
        Migration 064 (tp_members_type_cleanup) dropped that column entirely —
        TPMetric no longer carries measurement/dashboard-link columns — so there
        is nothing to clear; this tool does not touch tp_metrics.

        Returns:
          success (bool), removed_card_key (str), remaining_cards (int)
        """
        u = _user()
        if not u:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
        uid = uuid.UUID(u.user_id)

        from sqlalchemy import select as sa_select

        from app.models.dashboard import Dashboard, DashboardCard

        async with state.db_session_factory() as db:
            result = await db.execute(
                sa_select(Dashboard).where(
                    Dashboard.share_slug == dashboard_slug,
                    Dashboard.user_id == uid,
                )
            )
            dash = result.scalar_one_or_none()
            if not dash:
                return {
                    "error": True,
                    "message": f"Dashboard '{dashboard_slug}' not found or not yours.",
                }

            existing_result = await db.execute(
                sa_select(DashboardCard).where(DashboardCard.dashboard_id == dash.id)
            )
            existing_cards = list(existing_result.scalars().all())

            target = None
            for ec in existing_cards:
                if (ec.query_params or {}).get("key") == card_key:
                    target = ec
                    break
            if target is None:
                return {
                    "error": True,
                    "message": f"No card with key '{card_key}' on dashboard '{dashboard_slug}'.",
                }

            await db.delete(target)
            dash.updated_at = datetime.now(UTC).replace(tzinfo=None)
            await db.commit()

        return {
            "success": True,
            "removed_card_key": card_key,
            "remaining_cards": len(existing_cards) - 1,
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
