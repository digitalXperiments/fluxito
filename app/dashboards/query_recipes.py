"""Per-platform query recipes for hosted Streamlit dashboards.

Single source of truth for what a model may pass to ``fluxito_data.query``.
The authoring guide, ``get_dashboard_query_recipe``, and
``list_dashboard_connections`` all read from here so the contract cannot
drift between surfaces.
"""

from __future__ import annotations

from typing import Any

from app.dashboards.artifact import CONNECTION_TOOL, CONNECTION_TYPES

# resource identity the host overwrites — never invent or rely on caller values
_INJECT_COMMON = ("connection_id", "account_id")


def _recipe(
    *,
    action: str,
    send: list[str],
    injected: list[str],
    example: dict[str, Any],
    notes: str,
    other_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "send": send,
        "injected": injected,
        "example_params": example,
        "notes": notes,
        "other_actions": other_actions or [],
    }


# Default dashboard query for every bindable type.
RECIPES: dict[str, dict[str, Any]] = {
    "ga4": _recipe(
        action="run_report",
        send=["metrics", "dimensions", "start_date", "end_date"],
        injected=["property_id", *_INJECT_COMMON],
        example={
            "metrics": ["sessions", "totalUsers"],
            "dimensions": ["date"],
            "start_date": "2026-07-16",
            "end_date": "2026-08-15",
        },
        notes="Do not send property_id. Other action: get_realtime (metrics only).",
        other_actions=["get_realtime", "list_properties"],
    ),
    "google_ads": _recipe(
        action="get_campaign_performance",
        send=["start_date", "end_date"],
        injected=["customer_id", *_INJECT_COMMON],
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Bound customer_id wins. Other action: get_campaigns (no dates).",
        other_actions=["get_campaigns"],
    ),
    "meta_ads": _recipe(
        action="get_campaign_performance",
        send=["start_date", "end_date"],
        injected=["ad_account_id", *_INJECT_COMMON],
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Do not send ad_account_id. Other action: get_campaigns.",
        other_actions=["get_campaigns"],
    ),
    "tiktok_ads": _recipe(
        action="get_campaign_performance",
        send=["start_date", "end_date"],
        injected=["advertiser_id", *_INJECT_COMMON],
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Bound advertiser_id wins.",
        other_actions=["get_campaigns"],
    ),
    "snap_ads": _recipe(
        action="get_campaign_performance",
        send=["start_date", "end_date"],
        injected=["ad_account_id", *_INJECT_COMMON],
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Bound ad_account_id wins.",
        other_actions=["get_campaigns"],
    ),
    "apple_ads": _recipe(
        action="get_campaign_performance",
        send=["start_date", "end_date"],
        injected=["account_id", *_INJECT_COMMON],
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Bound account_id wins.",
    ),
    "linkedin_ads": _recipe(
        action="get_campaign_performance",
        send=["start_date", "end_date"],
        injected=_INJECT_COMMON,
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Host injects the bound ad account. Do not invent an account id.",
    ),
    "pinterest_ads": _recipe(
        action="get_campaign_performance",
        send=["start_date", "end_date"],
        injected=_INJECT_COMMON,
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Host injects the bound ad account.",
    ),
    "reddit_ads": _recipe(
        action="get_campaign_performance",
        send=["start_date", "end_date"],
        injected=_INJECT_COMMON,
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Host injects the bound ad account.",
    ),
    "x_ads": _recipe(
        action="get_campaign_performance",
        send=["start_date", "end_date"],
        injected=_INJECT_COMMON,
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Host injects the bound ad account.",
    ),
    "search_console": _recipe(
        action="get_search_analytics",
        send=["start_date", "end_date"],
        injected=["site_url", *_INJECT_COMMON],
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Bound site_url wins. Other action: list_sites (no params).",
        other_actions=["list_sites"],
    ),
    "bing_webmaster": _recipe(
        action="get_search_analytics",
        send=["start_date", "end_date"],
        injected=["site_url", *_INJECT_COMMON],
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Bound site wins. Do not invent a site URL.",
    ),
    "bigquery": _recipe(
        action="run_query",
        send=["query"],
        injected=["connection_id", "engine"],
        example={
            "query": (
                "SELECT date, SUM(sessions) AS sessions FROM `dataset.table` "
                "WHERE date BETWEEN '{start_date}' AND '{end_date}' GROUP BY 1"
            )
        },
        notes=(
            "SELECT only. Fill start_date/end_date from Streamlit widgets into the "
            "SQL string before calling query(). You may send `sql`; the host "
            "rewrites it to `query`. Do not send connection_id."
        ),
    ),
    "redshift": _recipe(
        action="run_query",
        send=["query"],
        injected=["connection_id", "engine"],
        example={"query": "SELECT date, COUNT(*) AS n FROM events WHERE date >= '{start_date}' GROUP BY 1"},
        notes="SELECT only. Host sets connection_id and engine=redshift.",
    ),
    "snowflake": _recipe(
        action="run_query",
        send=["query"],
        injected=["connection_id", "engine"],
        example={"query": "SELECT date, COUNT(*) AS n FROM events WHERE date >= '{start_date}' GROUP BY 1"},
        notes="SELECT only. Host sets connection_id and engine=snowflake.",
    ),
    "amplitude": _recipe(
        action="query_events",
        send=["start_date", "end_date", "events"],
        injected=_INJECT_COMMON,
        example={
            "start_date": "2026-07-16",
            "end_date": "2026-08-15",
            "events": ["page_view"],
        },
        notes="events must be a non-empty list. Host injects connection_id.",
    ),
    "mixpanel": _recipe(
        action="query_events",
        send=["start_date", "end_date", "events"],
        injected=_INJECT_COMMON,
        example={
            "start_date": "2026-07-16",
            "end_date": "2026-08-15",
            "events": ["Page View"],
        },
        notes="events must be a non-empty list. Host injects connection_id.",
    ),
    "posthog": _recipe(
        action="query_events",
        send=["start_date", "end_date", "events"],
        injected=_INJECT_COMMON,
        example={
            "start_date": "2026-07-16",
            "end_date": "2026-08-15",
            "events": ["$pageview"],
        },
        notes="events must be a non-empty list. Host injects connection_id.",
    ),
    "adobe_analytics": _recipe(
        action="run_report",
        send=["metrics", "start_date", "end_date"],
        injected=["connection_id", "report_suite_id", "account_id"],
        example={
            "metrics": ["metrics/visits"],
            "start_date": "2026-07-16",
            "end_date": "2026-08-15",
        },
        notes="Do not send report_suite_id or company id — the bound connection wins.",
    ),
    "gtm": _recipe(
        action="get_container_summary",
        send=[],
        injected=["account_id", "container_id", *_INJECT_COMMON],
        example={},
        notes="No caller params. Host injects account_id + container_id.",
        other_actions=["list_workspaces", "list_tags", "list_triggers", "list_variables"],
    ),
    "adobe_launch": _recipe(
        action="get_property",
        send=[],
        injected=["account_id", "container_id", *_INJECT_COMMON],
        example={},
        notes="Host injects company/property ids. Other: list_properties, list_rules.",
        other_actions=["list_properties", "list_rules"],
    ),
    "adobe_marketo": _recipe(
        action="list_programs",
        send=[],
        injected=_INJECT_COMMON,
        example={},
        notes="Host injects the Marketo connection. Prefer list/read actions only.",
    ),
    "branch": _recipe(
        action="query_events",
        send=["start_date", "end_date"],
        injected=_INJECT_COMMON,
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Host injects connection_id. Do not send API keys.",
    ),
    "appsflyer": _recipe(
        action="query_events",
        send=["start_date", "end_date"],
        injected=_INJECT_COMMON,
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Host injects connection_id. Do not send API tokens.",
    ),
    "adjust": _recipe(
        action="query_events",
        send=["start_date", "end_date"],
        injected=_INJECT_COMMON,
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Host injects connection_id.",
    ),
    "braze": _recipe(
        action="query_events",
        send=["start_date", "end_date"],
        injected=_INJECT_COMMON,
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Host injects connection_id. Do not send REST keys.",
    ),
    "moengage": _recipe(
        action="query_events",
        send=["start_date", "end_date"],
        injected=_INJECT_COMMON,
        example={"start_date": "2026-07-16", "end_date": "2026-08-15"},
        notes="Host injects connection_id.",
    ),
}


def recipe_for(connection_type: str) -> dict[str, Any] | None:
    key = (connection_type or "").strip().lower()
    rec = RECIPES.get(key)
    if rec is None:
        return None
    out = dict(rec)
    out["type"] = key
    out["tool"] = CONNECTION_TOOL.get(key)
    out["call"] = f'fx.query("{key}", action="{rec["action"]}", params={rec["example_params"]})'
    return out


def all_recipes() -> dict[str, dict[str, Any]]:
    return {t: recipe_for(t) for t in sorted(CONNECTION_TYPES) if recipe_for(t)}


def recipes_markdown() -> str:
    lines = [
        "## Per-platform query recipes (use these exact actions)",
        "",
        "Call `fx.query(alias, action, params)` only. The host picks the MCP tool",
        "from `type`. Do not invent actions. If a type is missing here, call",
        "`get_dashboard_query_recipe` rather than guessing.",
        "",
    ]
    for t in sorted(RECIPES):
        rec = RECIPES[t]
        send = ", ".join(rec["send"]) or "(none — host injects identity)"
        injected = ", ".join(rec["injected"])
        lines.append(f"### {t}")
        lines.append(f"- default action: `{rec['action']}`")
        lines.append(f"- send: {send}")
        lines.append(f"- host overwrites (do not send): {injected}")
        if rec.get("other_actions"):
            lines.append("- other actions: " + ", ".join(f"`{a}`" for a in rec["other_actions"]))
        lines.append(f"- notes: {rec['notes']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def assert_recipes_cover_types() -> list[str]:
    missing = sorted(CONNECTION_TYPES - set(RECIPES))
    extra = sorted(set(RECIPES) - CONNECTION_TYPES)
    errors: list[str] = []
    if missing:
        errors.append(f"recipes missing types: {missing}")
    if extra:
        errors.append(f"recipes for unknown types: {extra}")
    return errors
