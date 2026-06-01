"""
Unified MCP Tool Surface

Every sub-module still registers its fine-grained tools for internal
dispatch. This module exposes a curated unified surface to MCP clients
and preserves the legacy tools in ``tool_manager._legacy_tools`` for the
dispatchers to call.

Unified dispatchers (action + params):
    analytics_read          analytics_write
    tagmanager_read         tagmanager_write
    marketing_read          marketing_write
    warehouse_read
    seo_read                seo_write
    dashboard_read
    automation_read         automation_write
    get_knowledge           deploy_knowledge
    tracking_plan
    run_audit               run_analysis

Direct tools that survive the rewire (not absorbed into a dispatcher):
    warehouse_query         get_session_context
    dashboard_deploy_batch
    dashboard_manage_scopes  dashboard_rotate_token
    set_active_project      list_my_projects
    run_script
    generic_tool_read       generic_tool_write

Each unified dispatcher accepts (action: str, params: Optional[dict] = None)
and dispatches to one of the pre-registered legacy tools. Action names map
1:1 with the original implementations — no behavioural changes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routing Tables — action_name -> (legacy_tool_name, legacy_action_value | None)
# legacy_action_value = None means the legacy tool does NOT take `action` kwarg.
# ---------------------------------------------------------------------------

# analytics_read: GA4 + Amplitude + Adobe Analytics + Mixpanel
# NOTE: Audit actions live in run_audit; cross-platform blends live in run_analysis.
ANALYTICS_READ_ROUTES: dict[str, tuple[str, str | None]] = {
    # GA4 + Amplitude + Adobe Analytics read actions (from analytics_read)
    "run_report": ("analytics_read", "run_report"),
    "compare_date_ranges": ("analytics_read", "compare_date_ranges"),
    "list_properties": ("analytics_read", "list_properties"),
    "get_property": ("analytics_read", "get_property"),
    "list_audiences": ("analytics_read", "list_audiences"),
    "list_custom_dimensions": ("analytics_read", "list_custom_dimensions"),
    "list_custom_metrics": ("analytics_read", "list_custom_metrics"),
    "get_realtime": ("analytics_read", "get_realtime"),
    "list_data_streams": ("analytics_read", "list_data_streams"),
    "list_conversion_events": ("analytics_read", "list_conversion_events"),
    "get_conversion_events": ("analytics_read", "get_conversion_events"),
    # Amplitude-specific
    "query_events": ("analytics_read", "query_events"),
    "get_active_users": ("analytics_read", "get_active_users"),
    "get_event_properties": ("analytics_read", "get_event_properties"),
    "get_user_properties": ("analytics_read", "get_user_properties"),
    "get_retention": ("analytics_read", "get_retention"),
    "get_funnel": ("analytics_read", "get_funnel"),
    "get_revenue": ("analytics_read", "get_revenue"),
    "list_cohorts": ("analytics_read", "list_cohorts"),
    "list_events": ("analytics_read", "list_events"),
    "get_event_detail": ("analytics_read", "get_event_detail"),
    # Adobe Analytics
    "list_report_suites": ("analytics_read", "list_report_suites"),
    "list_companies": ("analytics_read", "list_companies"),
    "get_dimensions": ("analytics_read", "get_dimensions"),
    "get_metrics": ("analytics_read", "get_metrics"),
    "get_segments": ("analytics_read", "get_segments"),
    "get_calculated_metrics": ("analytics_read", "get_calculated_metrics"),
}

# analytics_write
ANALYTICS_WRITE_ROUTES: dict[str, tuple[str, str | None]] = {
    "create_audience": ("analytics_write", "create_audience"),
    "create_custom_dimension": ("analytics_write", "create_custom_dimension"),
    "create_custom_metric": ("analytics_write", "create_custom_metric"),
    "mark_event_as_conversion": ("analytics_write", "mark_event_as_conversion"),
    "create_calculated_metric": ("analytics_write", "create_calculated_metric"),
    "update_segment": ("analytics_write", "update_segment"),
    "create_segment": ("analytics_write", "create_segment"),
    "delete_segment": ("analytics_write", "delete_segment"),
    "delete_calculated_metric": ("analytics_write", "delete_calculated_metric"),
}

# tagmanager_read (GTM + Adobe Launch) — catalog only.
# Audits (audit_container, consent_mode, etc.) moved to run_audit.
TAGMANAGER_READ_ROUTES: dict[str, tuple[str, str | None]] = {
    # GTM reads
    "list_accounts": ("tagmanager_read", "list_accounts"),
    "list_containers": ("tagmanager_read", "list_containers"),
    "list_workspaces": ("tagmanager_read", "list_workspaces"),
    "list_tags": ("tagmanager_read", "list_tags"),
    "list_triggers": ("tagmanager_read", "list_triggers"),
    "list_variables": ("tagmanager_read", "list_variables"),
    "get_tag_detail": ("tagmanager_read", "get_tag_detail"),
    "get_container_summary": ("tagmanager_read", "get_container_summary"),
    # Adobe Launch reads
    "list_properties": ("tagmanager_read", "list_properties"),
    "get_property": ("tagmanager_read", "get_property"),
    "list_rules": ("tagmanager_read", "list_rules"),
    "get_rule": ("tagmanager_read", "get_rule"),
    "list_data_elements": ("tagmanager_read", "list_data_elements"),
    "list_extensions": ("tagmanager_read", "list_extensions"),
    "list_environments": ("tagmanager_read", "list_environments"),
    "list_libraries": ("tagmanager_read", "list_libraries"),
    "list_builds": ("tagmanager_read", "list_builds"),
}

# tagmanager_write (GTM + Adobe Launch)
TAGMANAGER_WRITE_ROUTES: dict[str, tuple[str, str | None]] = {
    # GTM
    "propose_change": ("tagmanager_write", "propose_change"),
    "create_workspace": ("tagmanager_write", "create_workspace"),
    "create_tag": ("tagmanager_write", "create_tag"),
    "update_tag": ("tagmanager_write", "update_tag"),
    "delete_tag": ("tagmanager_write", "delete_tag"),
    "create_trigger": ("tagmanager_write", "create_trigger"),
    "create_variable": ("tagmanager_write", "create_variable"),
    "publish_container": ("tagmanager_write", "publish_container"),
    # Adobe Launch
    "create_property": ("tagmanager_write", "create_property"),
    "create_rule": ("tagmanager_write", "create_rule"),
    "create_data_element": ("tagmanager_write", "create_data_element"),
    "create_library": ("tagmanager_write", "create_library"),
    "add_resources_to_library": ("tagmanager_write", "add_resources_to_library"),
    "build_library": ("tagmanager_write", "build_library"),
    "transition_library": ("tagmanager_write", "transition_library"),
}

# marketing_read (Google Ads + Meta Ads + TikTok Ads + Snap Ads) — performance queries only.
# Audits (budget_utilization, quality_scores, connection_health) moved to run_audit.
MARKETING_READ_ROUTES: dict[str, tuple[str, str | None]] = {
    "list_accounts": ("marketing_read", "list_accounts"),
    "get_campaign_performance": ("marketing_read", "get_campaign_performance"),
    "get_ad_group_performance": ("marketing_read", "get_ad_group_performance"),
    "get_adgroup_performance": ("marketing_read", "get_adgroup_performance"),
    "get_adset_performance": ("marketing_read", "get_adset_performance"),
    "get_adsquad_performance": ("marketing_read", "get_adsquad_performance"),
    "get_keyword_performance": ("marketing_read", "get_keyword_performance"),
    "get_conversion_actions": ("marketing_read", "get_conversion_actions"),
}

# marketing_write
MARKETING_WRITE_ROUTES: dict[str, tuple[str, str | None]] = {
    "update_campaign_budget": ("marketing_write", "update_campaign_budget"),
    "update_campaign_status": ("marketing_write", "update_campaign_status"),
    "create_campaign": ("marketing_write", "create_campaign"),
}

# warehouse_read (BigQuery + Redshift + Snowflake) — schema + metadata only.
# Audits (audit_dataset, check_data_quality, etc.) moved to run_audit.
WAREHOUSE_READ_ROUTES: dict[str, tuple[str, str | None]] = {
    "list_datasets": ("warehouse_read", "list_datasets"),
    "list_databases": ("warehouse_read", "list_databases"),
    "list_schemas": ("warehouse_read", "list_schemas"),
    "list_warehouses": ("warehouse_read", "list_warehouses"),
    "list_tables": ("warehouse_read", "list_tables"),
    "get_table_schema": ("warehouse_read", "get_table_schema"),
    "preview_table": ("warehouse_read", "preview_table"),
    "list_connections": ("warehouse_read", "list_connections"),
    "get_warehouse_usage": ("warehouse_read", "get_warehouse_usage"),
}

# seo_read (Google Search Console + Bing Webmaster Tools) — queries only.
# Audits (top_movers, striking_distance, etc.) moved to run_audit.
# Route key format: <platform>_<action> for Bing; bare action for GSC (backwards-compat).
SEO_READ_ROUTES: dict[str, tuple[str, str | None]] = {
    # Google Search Console actions (bare names preserved for backwards-compatibility)
    "list_sites": ("search_console_read", "list_sites"),
    "search_analytics": ("search_console_read", "search_analytics"),
    "list_sitemaps": ("search_console_read", "list_sitemaps"),
    "get_sitemap": ("search_console_read", "get_sitemap"),
    "inspect_url": ("search_console_read", "inspect_url"),
    # Google Search Console actions (explicit platform prefix)
    "gsc_list_sites": ("search_console_read", "list_sites"),
    "gsc_search_analytics": ("search_console_read", "search_analytics"),
    "gsc_list_sitemaps": ("search_console_read", "list_sitemaps"),
    "gsc_get_sitemap": ("search_console_read", "get_sitemap"),
    "gsc_inspect_url": ("search_console_read", "inspect_url"),
    # Bing Webmaster Tools actions
    "bing_list_sites": ("bing_webmaster_read", "list_sites"),
    "bing_get_query_stats": ("bing_webmaster_read", "get_query_stats"),
    "bing_get_crawl_stats": ("bing_webmaster_read", "get_crawl_stats"),
    "bing_get_index_coverage": ("bing_webmaster_read", "get_index_coverage"),
    "bing_get_link_counts": ("bing_webmaster_read", "get_link_counts"),
}

# seo_write
SEO_WRITE_ROUTES: dict[str, tuple[str, str | None]] = {
    "submit_sitemap": ("search_console_write", "submit_sitemap"),
    "delete_sitemap": ("search_console_write", "delete_sitemap"),
}

# dashboard_read (merges dashboard_list + dashboard_get)
DASHBOARD_READ_ROUTES: dict[str, tuple[str, str | None]] = {
    "list": ("dashboard_list", None),
    "get": ("dashboard_get", None),
}

# get_knowledge — merges business context, templates, and the three KPI
# operations (list / detail / compute). The legacy full-catalog dump
# (get_kpi_definitions / old "kpis" action) has been removed; Claude should
# call list_kpis for discovery and get_kpi for single-KPI details.
KNOWLEDGE_ROUTES: dict[str, tuple[str, str | None]] = {
    "context": ("get_business_context", None),
    "templates": ("template_list", None),
    "list_kpis": ("list_kpis", None),
    "get_kpi": ("get_kpi", None),
    "compute_kpi": ("compute_kpi", None),
}

# tracking_plan — collapses generate_sdr + refine_sdr into one dispatcher.
# Named "tracking_plan" because that is the industry-standard term for this
# artifact (Amplitude / Avo / Segment all use it). Product/UI copy continues
# to brand it as "SDR" (Solution Design Reference).
#
# Note: refine_sdr's own `action` kwarg (resume, submit_answer, finalize,
# etc.) lives INSIDE params — it is distinct from the dispatcher-level
# `action` which only picks between "generate" and "refine". Because
# legacy_action is None in both routes, params["action"] passes through
# to the legacy tool unchanged.
TRACKING_PLAN_ROUTES: dict[str, tuple[str, str | None]] = {
    "generate": ("generate_sdr", None),
    "save": ("save_sdr", None),
    "refresh_sources": ("refresh_sdr_sources", None),
    "capture_intake": ("capture_sdr_intake", None),
    "get_intake": ("get_sdr_intake", None),
    "list_sources": ("list_sdr_sources", None),
    "diagnose": ("diagnose_sdr", None),
    "refine": ("refine_sdr", None),
}

# automation_read / automation_write — renames of automation_browse /
# automation_action to match the <domain>_read / <domain>_write convention
# used by every other surface. automation_browse is already read-shaped
# (list + single-get); automation_action is already a write dispatcher
# keyed on an internal `action` kwarg (install | save).
AUTOMATION_READ_ROUTES: dict[str, tuple[str, str | None]] = {
    "browse": ("automation_browse", None),
}

AUTOMATION_WRITE_ROUTES: dict[str, tuple[str, str | None]] = {
    "install": ("automation_action", "install"),
    "save": ("automation_action", "save"),
}


# ---------------------------------------------------------------------------
# NEW — Heavy / Composite Surface
#
# run_audit     — every audit_*/check_* action from every domain, prefixed by
#                 platform so action names are globally unambiguous.
# run_analysis  — cross-connector computed insights (blended reports,
#                 attribution models, incrementality, etc.).
#
# Keeping these OUT of the domain read tools means:
#   • simple reads stay fast and have short, focused docstrings;
#   • heavy ops can carry different timeouts, caching, and billing weight;
#   • Claude can tell at tool-selection time whether an op is cheap or heavy.
# ---------------------------------------------------------------------------

# run_audit: prefixed actions, one table across every domain.
#
# NOTE: Only routes with a live handler in the target legacy tool are listed
# here. Historically this table had several "aspirational" routes pointing at
# handlers that were never implemented (ga4_audit_property,
# ga4_audit_tracking_setup, analytics_* composites, gtm_schema_validator,
# gtm_check_taxonomy_health). Those returned "Unknown action" at runtime and
# have been removed — add them back here ONLY when the corresponding handler
# lands in analytics_audit / tagmanager_audit.
AUDIT_ROUTES: dict[str, tuple] = {
    # ── Product analytics (GA4, Amplitude, Adobe Analytics) ────────────
    # Routes below all map to (legacy_tool, legacy_action, extra_kwargs?) and
    # were verified against the action handlers in analytics_audit.
    "ga4_audit_data_streams": ("analytics_audit", "audit_data_streams", {"platform": "ga4"}),
    "ga4_audit_conversion_events": ("analytics_audit", "audit_conversion_events", {"platform": "ga4"}),
    "ga4_audit_custom_definitions": ("analytics_audit", "audit_custom_definitions", {"platform": "ga4"}),
    "ga4_audit_ecommerce": ("analytics_audit", "audit_ecommerce", {"platform": "ga4"}),
    "ga4_schema_validator": ("analytics_audit", "schema_validator", {"platform": "ga4"}),
    "ga4_check_data_anomalies": ("analytics_audit", "check_data_anomalies", {"platform": "ga4"}),
    "amplitude_check_taxonomy_health": (
        "analytics_audit",
        "check_taxonomy_health",
        {"platform": "amplitude"},
    ),
    "amplitude_check_event_volume_anomalies": (
        "analytics_audit",
        "check_event_volume_anomalies",
        {"platform": "amplitude"},
    ),
    "adobe_audit_report_suite": ("analytics_audit", "audit_report_suite", {"platform": "adobe_analytics"}),
    "adobe_check_data_quality": ("analytics_audit", "check_data_quality", {"platform": "adobe_analytics"}),
    # ── Tag Manager (GTM + Adobe Launch) ───────────────────────────────
    # Routes below all map to handlers that exist in tagmanager_audit.
    "gtm_audit_container": ("tagmanager_audit", "audit_container"),
    "gtm_explain_tag": ("tagmanager_audit", "explain_tag"),
    "gtm_simulate_event": ("tagmanager_audit", "simulate_event"),
    "gtm_dependency_map": ("tagmanager_audit", "dependency_map"),
    "gtm_check_ga4_implementation": ("tagmanager_audit", "check_ga4_implementation"),
    "gtm_find_tracking_regression": ("tagmanager_audit", "find_tracking_regression"),
    "gtm_diagnose_conversion_discrepancy": ("tagmanager_audit", "diagnose_conversion_discrepancy"),
    "gtm_generate_audit_report": ("tagmanager_audit", "generate_audit_report"),
    "gtm_suggest_improvements": ("tagmanager_audit", "suggest_improvements"),
    "gtm_benchmark_health": ("tagmanager_audit", "benchmark_health"),
    "adobe_launch_audit_property": (
        "tagmanager_audit",
        "audit_property",
        {"platform": "adobe_launch"},
    ),
    "adobe_launch_get_publish_history": (
        "tagmanager_audit",
        "get_publish_history",
        {"platform": "adobe_launch"},
    ),
    # NEW — Consent Mode v2 / GDPR / CCPA compliance audit.
    # Both GTM and Adobe Launch are supported via the same `audit_consent_mode`
    # action inside `tagmanager_audit`, routed by platform.
    "gtm_audit_consent_mode": (
        "tagmanager_audit",
        "audit_consent_mode",
        {"platform": "gtm"},
    ),
    "adobe_audit_consent_mode": (
        "tagmanager_audit",
        "audit_consent_mode",
        {"platform": "adobe_launch"},
    ),
    # ── Paid marketing ─────────────────────────────────────────────────
    "marketing_audit_budget_utilization": ("marketing_audit", "audit_budget_utilization"),
    "marketing_audit_quality_scores": ("marketing_audit", "audit_quality_scores"),
    # ── Warehouse ──────────────────────────────────────────────────────
    "warehouse_audit_dataset": ("warehouse_audit", "audit_dataset"),
    "warehouse_audit_schema": ("warehouse_audit", "audit_schema"),
    "warehouse_find_stale_tables": ("warehouse_audit", "find_stale_tables"),
    "warehouse_check_table_health": ("warehouse_audit", "check_table_health"),
    "warehouse_check_empty_tables": ("warehouse_audit", "check_empty_tables"),
    "warehouse_check_clustering_health": ("warehouse_audit", "check_clustering_health"),
    # ── SEO / Search Console ───────────────────────────────────────────
    "seo_top_movers": ("search_console_audit", "top_movers"),
    "seo_striking_distance": ("search_console_audit", "striking_distance"),
    "seo_ctr_outliers": ("search_console_audit", "ctr_outliers"),
    "seo_sitemap_health": ("search_console_audit", "sitemap_health"),
    "seo_gsc_ga4_cross_reference": ("search_console_audit", "gsc_ga4_cross_reference"),
}

# run_analysis: cross-connector computed insights.
#   • cross_platform_report — the existing blended reporter
#   • blended_performance / channel_comparison / top_campaigns —
#     sub-actions of cross_platform_report exposed as top-level actions for
#     discoverability.
#   • revenue_attribution — NEW; handler lives inside cross_platform_report.
#     If the handler isn't registered yet the legacy tool returns a clean
#     "unknown action" error so the failure mode is obvious.
ANALYSIS_ROUTES: dict[str, tuple[str, str | None]] = {
    # Legacy pass-through — user sets `action` inside params if desired.
    "cross_platform_report": ("cross_platform_report", None),
    # Pre-wired sub-actions for nicer discoverability.
    "blended_performance": ("cross_platform_report", "blended_performance"),
    "channel_comparison": ("cross_platform_report", "channel_comparison"),
    "top_campaigns": ("cross_platform_report", "top_campaigns"),
    # Revenue attribution (spend + GA4 touches + warehouse revenue).
    "revenue_attribution": ("cross_platform_report", "revenue_attribution"),
}


# ---------------------------------------------------------------------------
# Rich docstrings
# ---------------------------------------------------------------------------

ANALYTICS_READ_DOC = """
Read product / web analytics data across GA4, Amplitude, Adobe Analytics.

Pass `platform` (ga4 | amplitude | adobe_analytics) inside `params`
when multiple platforms are connected.

For audits / anomaly checks / tracking regressions → use `run_audit`.
For cross-platform blends / attribution → use `run_analysis`.

Actions (pass via `action`, required params inside `params`):

  REPORTING
    run_report        — Run a report. params: platform, property_id (ga4) or
                        project_id (amplitude), start_date, end_date,
                        dimensions[], metrics[], dimension_filter, limit.
    compare_date_ranges — Run a date-range comparison report (GA4 only).
    get_realtime      — Real-time event stream.
    query_events      — Event query (Amplitude).
    get_active_users  — DAU/WAU/MAU.
    get_retention     — Retention analysis.
    get_funnel        — Funnel conversion.
    get_revenue       — Revenue metrics.

  CATALOG / METADATA
    list_properties, get_property, list_audiences, list_custom_dimensions,
    list_custom_metrics, list_data_streams, list_conversion_events,
    get_conversion_events, list_cohorts, list_events, get_event_detail,
    get_event_properties, get_user_properties, list_report_suites,
    list_companies, get_dimensions, get_metrics, get_segments,
    get_calculated_metrics

Return shape: {rows/data/items: [...], ...} or {error, error_type, message}.
"""

ANALYTICS_WRITE_DOC = """
Mutate product/web analytics configuration. Requires analytics_write scope.

Actions:
  create_audience            — params: platform, property_id, name, filter_clauses
  create_custom_dimension    — params: platform, property_id, parameter_name, display_name, scope
  create_custom_metric       — params: platform, property_id, parameter_name, display_name, unit, scope
  mark_event_as_conversion   — params: platform, property_id, event_name
  create_calculated_metric   — params: platform, report_suite_id, name, formula
  create_segment             — params: platform, report_suite_id, name, definition
  update_segment             — params: platform, segment_id, updates
  delete_segment             — params: platform, segment_id
  delete_calculated_metric   — params: platform, metric_id
"""

TAGMANAGER_READ_DOC = """
Read tag manager catalog (GTM, Adobe Launch).

Pass platform (gtm | adobe_launch) in params when both are connected.
For audits (container health, consent mode, taxonomy, publish history, etc.)
→ use `run_audit` (e.g. gtm_audit_container, adobe_launch_get_publish_history).

Actions:
  GTM
    list_accounts        — Enumerate GTM accounts (no args required)
    list_containers      — params: account_id
    list_workspaces      — params: account_id, container_id
    list_tags            — params: account_id, container_id, workspace_id
    list_triggers        — params: account_id, container_id, workspace_id
    list_variables       — params: account_id, container_id, workspace_id
    get_tag_detail       — params: account_id, container_id, workspace_id, tag_id
    get_container_summary— params: account_id, container_id
  ADOBE LAUNCH
    list_properties, get_property, list_rules, get_rule,
    list_data_elements, list_extensions, list_environments,
    list_libraries, list_builds
"""

TAGMANAGER_WRITE_DOC = """
Mutate tag manager (GTM, Adobe Launch). Requires tagmanager_write / publish scope.

Actions:
  GTM
    propose_change     — Dry-run a proposed change (safe, no mutation). params: spec dict
    create_workspace   — params: account_id, container_id, name
    create_tag         — params: account_id, container_id, workspace_id, name, type,
                         firing_trigger_ids[], parameters[]
    update_tag         — params: account_id, container_id, workspace_id, tag_id, updates
    delete_tag         — params: account_id, container_id, workspace_id, tag_id
    create_trigger     — params: account_id, container_id, workspace_id, name, type, filters[]
    create_variable    — params: account_id, container_id, workspace_id, name, type, parameters[]
    publish_container  — params: account_id, container_id, workspace_id, name (requires publish scope)

  ADOBE LAUNCH
    create_property           — config: {name, company_id, platform?, domains?}
    create_rule               — config: {property_id, name}
    create_data_element       — config: {property_id, name, delegate_descriptor_id, settings?}
    create_library            — config: {property_id, name, environment_id}
    add_resources_to_library  — config: {library_id, resources[]}
    build_library             — config: {library_id}
    transition_library        — config: {library_id, action}  (action: submit|approve|reject|develop)
"""

MARKETING_READ_DOC = """
Read paid-marketing performance across Google Ads, Meta, TikTok, Snap.

Pass platform (google | meta | tiktok | snap) in params. (Google Ads uses
"google", not "google_ads".)
For audits (budget utilization, quality scores, connection health) → use
`run_audit`. For blended cross-channel reports / attribution → use `run_analysis`.

Actions:
  list_accounts              — Enumerate ad accounts
  get_campaign_performance   — params: platform, account_id, start_date, end_date, metrics[]
  get_ad_group_performance / get_adgroup_performance  — Google Ads ad-group view
  get_adset_performance      — Meta adset-level
  get_adsquad_performance    — Snap adsquad-level
  get_keyword_performance    — Google Ads keyword-level
  get_conversion_actions     — List conversion actions
"""

MARKETING_WRITE_DOC = """
Mutate paid marketing campaigns. Requires marketing_write scope.

Actions:
  update_campaign_budget   — params: platform, account_id, campaign_id, new_budget
  update_campaign_status   — params: platform, account_id, campaign_id, status (PAUSED|ENABLED)
  create_campaign          — params: platform, account_id, spec dict
"""

WAREHOUSE_READ_DOC = """
Read warehouse schema + metadata across BigQuery, Redshift, Snowflake.

Pass engine (bigquery | redshift | snowflake) in params.
For data-quality / stale-table / clustering audits → use `run_audit`.
To actually execute SQL → use `warehouse_query`.

Actions:
  list_connections    — Which warehouses are connected
  list_datasets       — BigQuery datasets
  list_databases      — Redshift/Snowflake databases
  list_schemas        — Redshift/Snowflake schemas
  list_warehouses     — Snowflake compute warehouses
  list_tables         — Tables in a dataset_id/schema
  get_table_schema    — Column list + types for a table_id
  preview_table       — First N rows (cheap sample). params: engine, dataset_id, table_id
  get_warehouse_usage — Compute spend / storage stats
"""

WAREHOUSE_QUERY_DOC = """
Execute SQL against a connected warehouse (BigQuery, Redshift, Snowflake).
Separate from warehouse_read because SQL execution is billable / expensive
and the AI should reason about cost distinctly from catalog reads.

Params:
  engine        — bigquery | redshift | snowflake (required)
  query         — SQL string (required for run_query / dry_run / explain_query)
  dataset_id    — used by preview_table
  table_id      — used by preview_table
  max_results   — Row cap (default 1000, max 5000)
  connection_id — Specific warehouse connection if user has multiple

Actions: run_query (default), preview_table, dry_run (BigQuery),
         explain_query (Redshift/Snowflake).
"""

SEO_READ_DOC = """
Read organic-search data across Google Search Console and Bing Webmaster Tools.
Use action names prefixed with `gsc_` or `bing_` to target a specific platform.
Bare action names (list_sites, search_analytics, etc.) default to Google Search Console
for backwards-compatibility.

For audits (top_movers, striking_distance, CTR outliers, sitemap health,
GSC↔GA4 cross-reference) → use `run_audit` with the `seo_*` action prefix.

GOOGLE SEARCH CONSOLE actions (prefix `gsc_` or use bare names):
  list_sites / gsc_list_sites
                   — Enumerate verified GSC properties
  search_analytics / gsc_search_analytics
                   — Impressions/clicks/CTR/position. params: site_url,
                     start_date, end_date, dimensions[] (query|page|country|
                     device|date|searchAppearance), search_type, row_limit,
                     start_row, dimension_filter_groups, aggregation_type,
                     data_state
  list_sitemaps / gsc_list_sitemaps
                   — params: site_url
  get_sitemap / gsc_get_sitemap
                   — params: site_url, feedpath
  inspect_url / gsc_inspect_url
                   — URL Inspection API. params: site_url, inspection_url,
                     language_code

BING WEBMASTER TOOLS actions (prefix `bing_`):
  bing_list_sites  — Enumerate verified Bing Webmaster sites
  bing_get_query_stats
                   — Keyword/query performance. params: site_url,
                     start_date, end_date (YYYY-MM-DD), search_type,
                     page (default 0), page_size (default 100)
  bing_get_crawl_stats
                   — Crawl statistics. params: site_url
  bing_get_index_coverage
                   — Index coverage data. params: site_url
  bing_get_link_counts
                   — Inbound link counts. params: site_url
"""

SEO_WRITE_DOC = """
Mutate organic-search configuration. Requires the full Search Console
(webmasters) scope.

Actions:
  submit_sitemap — params: site_url, feedpath
  delete_sitemap — params: site_url, feedpath
"""

DASHBOARD_READ_DOC = """
Read saved dashboards.

Actions:
  list — List all dashboards in the active project. No params.
  get  — Fetch one dashboard by id. params: dashboard_id
"""

DEPLOY_KNOWLEDGE_DOC = """
Write to the knowledge base — save or deploy templates.

Actions:
  deploy_template — Deploy a curated template into a new live dashboard.
                    params: template_id, dashboard_name, overrides dict
  save_template   — Save an existing dashboard as a reusable template.
                    params: dashboard_id, template_name, description, is_shared
"""

DEPLOY_KNOWLEDGE_ROUTES: dict[str, tuple[str, str | None]] = {
    "deploy_template": ("template_deploy", None),
    "save_template": ("template_save", None),
}

KNOWLEDGE_DOC = """
Read and execute the project's knowledge base — KPI definitions, business
context, and the curated template library. Always call this early in a
session so your answers match the client's terminology and metrics.

Actions:
  list_kpis   — Concise KPI catalog (slug, name, aliases, status,
                short description). Use this for discovery.
                params: status (approved | draft | deprecated | all,
                        default "approved")
  get_kpi     — Full spec for one KPI (definition, expression, bound
                inputs, unit, direction, expected range).
                params: slug (case-insensitive)
  compute_kpi — Execute the KPI's formula against its bound sources and
                return the scalar value. Prefer this over composing
                formulas from raw analytics calls.
                params: slug, date_range_start ("30daysAgo" or
                        "YYYY-MM-DD"), date_range_end ("today" or
                        "YYYY-MM-DD")
  context     — Business context markdown doc. No params.
  templates   — Curated dashboard/report template library.
                params: category (optional), show_all (bool, optional)

Typical flow: list_kpis → get_kpi(slug) for definition → compute_kpi(slug)
for the current value.
"""

TRACKING_PLAN_DOC = """
Create, refine, and maintain a tracking plan — the project's Solution Design
Reference (SDR): a markdown doc describing the event taxonomy, destinations,
and tracking contract for the product.

PROJECT SCOPING: every action runs against the active project. The active
project persists across turns once set_active_project succeeds, so in normal
use you don't pass it. BUT if you call set_active_project and a tracking_plan
action in the SAME turn (parallel tool calls), pass project_id in params here —
the active-project selection from a sibling call in the same batch is not
guaranteed to be visible yet. Passing project_id is always race-free.

In v2 the server gathers high-fidelity facts and YOU (the model) do the
synthesis. Typical first-time flow:
  1. generate (no intake_answers) → returns 6 business-intake questions. Ask
     the user conversationally, one or two at a time.
  2. generate (with intake_answers) → scans connected sources and returns
     structured facts + a parse-valid `markdown_skeleton` + an
     `instructions_for_claude` synthesis playbook. NOTHING is written yet.
  3. Synthesize the full SDR markdown by editing the skeleton per the playbook.
  4. save → persist your markdown. Then optionally refine.
Incremental flow when a new connector is added: refresh_sources → review
deltas with the user → refine(action='apply_source_delta').

Actions:
  generate — Gather data for an SDR (does not write the doc).
             params:
               project_id (optional, defaults to active project)
               name (optional, defaults to "<project> SDR")
               intake_answers (dict of the 6 keys: business_model,
                   primary_kpis, conversion_definition, key_journeys,
                   privacy_consent, ownership_complexity [+ anything_else]).
                   Omit to receive the questions to ask first.
               sources (optional list, e.g. ["ga4","gtm","google_ads"])
               business_type_hint (ecommerce | saas | lead_gen | media |
                                   app | marketplace)
               phase ("auto" | "interview" | "scan"), regenerate (bool)
             returns: intake, scans, industry_template, connected_sources
                   (incl. connected_but_unsupported), **findings** + **readiness**
                   (server-computed cross-reference diagnosis), markdown_skeleton,
                   and instructions_for_claude (the synthesis playbook).

  diagnose — Re-scan connectors and return a cross-referenced diagnosis
             (findings + readiness) without writing. Use for "why aren't my
             conversions firing?". Findings are platform-agnostic (works for
             GA4, Adobe, Amplitude, warehouse — whatever fills each role).
             params: sdr_id (optional), connector_filter (optional list)

  save     — Persist a model-authored SDR markdown draft.
             params:
               markdown (required — the full SDR document you synthesized)
               intake_snapshot (the `intake` object from generate)
               source_snapshot (the `scans` object from generate)
               sdr_id (optional — update an existing draft), name (optional)

  refresh_sources — Re-scan connectors and return structured deltas (added /
             missing / destination / parameter changes) without writing.
             params: sdr_id (required), connector_filter (optional list),
                     reuse_intake (bool, default True)

  capture_intake — Validate / persist the 6 intake answers on their own.
             params: intake_answers (required), sdr_id (optional)

  get_intake — Re-surface the persisted intake answers for an SDR.
             params: sdr_id (required)

  list_sources — Report supported, currently-scannable, and
             connected-but-unsupported sources for the project / SDR.
             params: sdr_id (optional)

  refine   — Conversationally edit sections of an existing SDR through a
             resumable state machine.
             params:
               sdr_id (required)
               action (resume | goto_section | submit_answer |
                       accept_proposed | reject_proposed | skip_section |
                       show_status | apply_source_delta | finalize |
                       start_new_draft)
                 NOTE: this `action` is the refinement state-machine action
                 and lives INSIDE params. The dispatcher-level `action`
                 above must be "refine".
               section (e.g. "event_catalog.purchase" — for goto_section)
               user_input (for submit_answer)
               source_delta (the refresh_sources payload — for apply_source_delta)
               changelog_note (required for finalize on versions >= 1.1)
"""

AUTOMATION_READ_DOC = """
Read the project's automation library — curated, scheduled monitor
recipes (daily digests, anomaly detection, budget pacing, exec summaries,
tag-health checks, launch monitors).

Actions:
  browse — Browse the library OR fetch a single automation with its
           rendered prompt preview.
           params:
             slug (optional — omit for the list; pass to fetch one)
             theme (daily_digest | anomaly | pacing | exec_summary |
                    tag_health | launch_monitor)
             channel_label (optional — folds into preview)
             variables (dict, optional — folds into preview)
             show_all (bool, default False — include automations whose
                       required platforms aren't connected)

After picking an automation, call automation_write(action="install", ...)
to record the install and receive the arguments Cowork's
create_scheduled_task tool needs.
"""

AUTOMATION_WRITE_DOC = """
Mutate the automation library. Requires the appropriate scope for the
active project.

Actions:
  install — Install an automation into the active project. Records the
            install and returns the rendered prompt, cron, and scheduled
            task arguments.
            params:
              slug (required)
              channel_label, variables (dict), cron_expression, task_name
  save    — Save a new or updated automation recipe into the project
            library. Admin-level; template-author tooling.
            params:
              title, description, theme, prompt_template,
              required_platforms (list[str]), default_cron,
              default_schedule_label, channel_hints (list[str]),
              cooldown_hours (int, default 0), icon
"""

SESSION_CONTEXT_DOC = """
One-stop context read: connected platforms, active project, and (optionally)
a detailed doc for a specific tool. Call this first in any new session — it
replaces the old get_connection_status + get_active_project + tool_help tools.

Params:
  tool_name (optional) — If provided, returns the detailed reference doc for
                         that tool INSTEAD of the session summary. Use this
                         when you want deeper docs for a specific tool.

No params = full session context:
  • user_email, active_project (name, slug, plan, role)
  • connected_platforms[] and disconnected_platforms[] (with connect_urls)
  • list of available tools (with one-line descriptions)

With tool_name:
  • tool_name, detailed doc (actions + params + examples)
"""

GENERIC_READ_DOC = """
Generic escape-hatch READ tool for capabilities that don't fit the core
buckets (analytics/tagmanager/marketing/warehouse/seo/dashboard). Use only
when no other read tool applies. Today this is a no-op stub that returns
a capability-not-available error; future features will wire actions here.

Params:
  capability — e.g. 'notifications', 'audit_log', 'webhooks' (future)
  action     — capability-specific action name
  args       — action-specific parameters
"""

GENERIC_WRITE_DOC = """
Generic escape-hatch WRITE tool (see generic_tool_read). Stub today.

Params:
  capability, action, args
"""

RUN_AUDIT_DOC = """
Run a canonical audit / health check across any connected platform. All audit
and anomaly-detection work lives here — keeping it out of the domain read
tools so simple reads stay lean. Action names are platform-prefixed so they
are globally unambiguous.

Call `get_session_context(tool_name='run_audit')` for the full action
reference including required params.

Actions (grouped):

  PRODUCT ANALYTICS (GA4 + Amplitude + Adobe Analytics)
    ga4_audit_data_streams, ga4_audit_conversion_events,
    ga4_audit_custom_definitions, ga4_audit_ecommerce,
    ga4_schema_validator, ga4_check_data_anomalies,
    amplitude_check_taxonomy_health, amplitude_check_event_volume_anomalies,
    adobe_audit_report_suite, adobe_check_data_quality

  TAG MANAGER (GTM + Adobe Launch)
    gtm_audit_container, gtm_explain_tag, gtm_simulate_event,
    gtm_dependency_map, gtm_check_ga4_implementation,
    gtm_find_tracking_regression, gtm_diagnose_conversion_discrepancy,
    gtm_generate_audit_report, gtm_suggest_improvements, gtm_benchmark_health,
    gtm_audit_consent_mode    — GTM Consent Mode v2 / GDPR / CCPA audit
    adobe_launch_audit_property, adobe_launch_get_publish_history,
    adobe_audit_consent_mode  — Adobe Launch consent / CMP heuristic audit

  PAID MARKETING (Google Ads + Meta + TikTok + Snap)
    marketing_audit_budget_utilization, marketing_audit_quality_scores,
    marketing_connection_health

  WAREHOUSE (BigQuery + Redshift + Snowflake)
    Note: most warehouse audits are engine-specific. Pass `engine` in params.
    warehouse_audit_dataset           (bigquery)
    warehouse_audit_schema            (redshift | snowflake)
    warehouse_find_stale_tables       (all engines)
    warehouse_check_table_health      (redshift)
    warehouse_check_data_quality      (all engines)
    warehouse_check_empty_tables      (bigquery)
    warehouse_check_clustering_health (snowflake)

  SEO (Google Search Console)
    seo_top_movers, seo_striking_distance, seo_ctr_outliers,
    seo_sitemap_health, seo_gsc_ga4_cross_reference

Return shape: findings[] with severity + recommendation, or
{error, error_type, message}.
"""

RUN_ANALYSIS_DOC = """
Run a cross-connector composite analysis — blended reporting, attribution
modeling, incrementality, etc. These are computed insights that join data
from more than one platform, so they get their own tool (distinct from the
domain reads) with a longer default timeout.

Actions:
  cross_platform_report   — Generic blended report. params: start_date,
                            end_date, platforms[] (ga4|google_ads|meta|tiktok|snap),
                            dimensions[], metrics[], granularity.
                            If `action` is set inside params, it wins.
  blended_performance     — Totals + per-platform breakdown + blended KPIs
                            (CTR, CPC, CPA, ROAS).
  channel_comparison      — Side-by-side comparison table with spend_share_pct.
  top_campaigns           — Top N campaigns sorted by spend|roas|conversions|
                            clicks|cpa|impressions|revenue.
  revenue_attribution     — Multi-touch attribution model. Joins ad spend
                            (Ads/Meta/TikTok/Snap) + GA4 touch sequence +
                            warehouse revenue. params: start_date, end_date,
                            model (first_touch|last_touch|linear|time_decay|
                            position_based), attribution_window_days,
                            conversion_event, channels[] (optional),
                            order_id_column (warehouse join key).

For single-platform reports → use `analytics_read` or `marketing_read`.
For audits → use `run_audit`.
"""


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _make_dispatcher(routes: dict, surface_name: str):
    """Build an async dispatcher closure for the given routing table."""

    async def dispatcher(action: str, params: dict | None = None) -> dict:
        from app.tools.registry import _tool_manager_ref

        tm = _tool_manager_ref["mgr"]
        params = params or {}
        route = routes.get(action)
        if route is None:
            return {
                "error": True,
                "error_type": "unknown_action",
                "message": f"Unknown action '{action}' for {surface_name}.",
                "available_actions": sorted(routes.keys()),
            }
        # Routes may be 2-tuple (tool, action) or 3-tuple
        # (tool, action, extra_kwargs: dict) — the 3rd slot lets us pre-
        # inject kwargs like platform="adobe_launch".
        if len(route) == 3:
            legacy_tool_name, legacy_action, extra = route
        else:
            legacy_tool_name, legacy_action = route
            extra = None
        legacy_tool = tm._legacy_tools.get(legacy_tool_name)  # type: ignore[attr-defined]
        if legacy_tool is None:
            return {
                "error": True,
                "error_type": "server_error",
                "message": f"Internal: legacy tool '{legacy_tool_name}' not found.",
            }
        # Build args payload the legacy tool expects
        call_args: dict = dict(params)
        if legacy_action is not None:
            call_args["action"] = legacy_action
        if extra:
            # Caller-supplied params win over route defaults
            for k, v in extra.items():
                call_args.setdefault(k, v)
        try:
            return await legacy_tool.run(call_args)
        except Exception as exc:
            logger.exception(f"{surface_name} dispatch failed for action={action}")
            return {
                "error": True,
                "error_type": "server_error",
                "message": f"{surface_name}({action}) failed: {exc}",
            }

    dispatcher.__name__ = surface_name
    return dispatcher


def rewire_unified_surface(mcp_server) -> None:
    """
    Rewire the MCP tool manager to expose only the unified 18-tool surface.

    Must run AFTER every sub-module has registered its fine-grained tools.
    Legacy tool objects are preserved in ``tool_manager._legacy_tools`` so
    the unified dispatchers can still call them.
    """
    tm = mcp_server._tool_manager

    # Import config/state bits we reuse inside get_session_context
    import app.app_state as state
    from app.config import settings
    from app.tools.tool_docs import get_doc, list_docs

    # ── Preserve legacy tool objects for the dispatcher ────────────────────
    legacy_names = {
        # analytics
        "analytics_read",
        "analytics_audit",
        "analytics_write",
        "cross_platform_report",
        # tagmanager
        "tagmanager_read",
        "tagmanager_audit",
        "tagmanager_write",
        # marketing
        "marketing_read",
        "marketing_audit",
        "marketing_write",
        # warehouse
        "warehouse_read",
        "warehouse_audit",
        "warehouse_query",
        # search console + bing webmaster
        "search_console_read",
        "search_console_audit",
        "search_console_write",
        "bing_webmaster_read",
        # dashboards — reads are dispatched via dashboard_read; card-native
        # deploy tools (dashboard_deploy_batch, dashboard_manage_scopes,
        # dashboard_rotate_token) are NOT absorbed
        # by a dispatcher.
        "dashboard_list",
        "dashboard_get",
        # templates + knowledge
        "template_list",
        "template_deploy",
        "template_save",
        "get_business_context",
        "list_kpis",
        "get_kpi",
        "compute_kpi",
        # SDR / tracking plan
        "generate_sdr",
        "save_sdr",
        "refresh_sdr_sources",
        "capture_sdr_intake",
        "get_sdr_intake",
        "list_sdr_sources",
        "diagnose_sdr",
        "refine_sdr",
        # automation
        "automation_browse",
        "automation_action",
    }
    tm._legacy_tools = {n: t for n, t in tm._tools.items() if n in legacy_names}  # type: ignore[attr-defined]
    # Also stash a reference to tm for the dispatcher closures
    from app.tools import registry as _reg

    _reg._tool_manager_ref["mgr"] = tm

    # ── Build unified tools ────────────────────────────────────────────────
    def _add(name: str, doc: str, routes: dict):
        fn = _make_dispatcher(routes, name)
        fn.__doc__ = doc
        tm.add_tool(fn, name=name)

    # Drop legacy names from _tools; we'll re-add only the unified ones
    for n in legacy_names:
        tm._tools.pop(n, None)

    # Core capability pairs
    _add("analytics_read", ANALYTICS_READ_DOC, ANALYTICS_READ_ROUTES)
    _add("analytics_write", ANALYTICS_WRITE_DOC, ANALYTICS_WRITE_ROUTES)
    _add("tagmanager_read", TAGMANAGER_READ_DOC, TAGMANAGER_READ_ROUTES)
    _add("tagmanager_write", TAGMANAGER_WRITE_DOC, TAGMANAGER_WRITE_ROUTES)
    _add("marketing_read", MARKETING_READ_DOC, MARKETING_READ_ROUTES)
    _add("marketing_write", MARKETING_WRITE_DOC, MARKETING_WRITE_ROUTES)
    _add("warehouse_read", WAREHOUSE_READ_DOC, WAREHOUSE_READ_ROUTES)
    _add("seo_read", SEO_READ_DOC, SEO_READ_ROUTES)
    _add("seo_write", SEO_WRITE_DOC, SEO_WRITE_ROUTES)
    _add("dashboard_read", DASHBOARD_READ_DOC, DASHBOARD_READ_ROUTES)
    _add("get_knowledge", KNOWLEDGE_DOC, KNOWLEDGE_ROUTES)
    _add("deploy_knowledge", DEPLOY_KNOWLEDGE_DOC, DEPLOY_KNOWLEDGE_ROUTES)
    _add("tracking_plan", TRACKING_PLAN_DOC, TRACKING_PLAN_ROUTES)
    _add("automation_read", AUTOMATION_READ_DOC, AUTOMATION_READ_ROUTES)
    _add("automation_write", AUTOMATION_WRITE_DOC, AUTOMATION_WRITE_ROUTES)

    # Heavy / composite surface — audits + cross-connector analyses.
    # Kept separate from the domain reads so simple reads stay lean and
    # Claude can reason about cheap vs. expensive ops at selection time.
    _add("run_audit", RUN_AUDIT_DOC, AUDIT_ROUTES)
    _add("run_analysis", RUN_ANALYSIS_DOC, ANALYSIS_ROUTES)

    # warehouse_query is a direct rename of the legacy warehouse_query (SQL exec)
    _wq_legacy = tm._legacy_tools.get("warehouse_query")  # type: ignore[attr-defined]
    if _wq_legacy is not None:

        async def warehouse_query(action: str = "run_query", params: dict | None = None) -> dict:
            call_args = dict(params or {})
            call_args["action"] = action
            return await _wq_legacy.run(call_args)

        warehouse_query.__doc__ = WAREHOUSE_QUERY_DOC
        tm.add_tool(warehouse_query, name="warehouse_query")

    # ── get_session_context (replaces get_connection_status + tool_help + get_active_project) ─
    async def get_session_context(tool_name: str | None = None) -> dict:
        # Tool-specific doc mode
        if tool_name:
            doc = get_doc(tool_name)
            available = sorted(tm._tools.keys())
            if doc is None:
                return {
                    "error": True,
                    "error_type": "not_found",
                    "message": f"No detailed doc for '{tool_name}'.",
                    "available_tools": available,
                    "available_docs": list_docs(),
                }
            return {"tool_name": tool_name, "doc": doc}

        # Full session context mode
        u = state.current_user_ctx.get()
        base = settings.APP_BASE_URL
        if not u:
            return {
                "error": True,
                "error_type": "unauthenticated",
                "message": "No active MCP session found.",
                "action_required": f"Visit {base} to sign in.",
            }

        p = state.current_project_ctx.get()
        if not p:
            if len(u.projects) == 0:
                return {
                    "error": True,
                    "error_type": "no_projects",
                    "message": "You don't belong to any projects yet.",
                    "action_required": f"Visit {base}/projects to create one.",
                }
            return {
                "error": True,
                "error_type": "no_active_project",
                "message": "No project is active. Use set_active_project to select one.",
                "available_projects": [
                    {"name": pr.project_name, "slug": pr.project_slug} for pr in u.projects
                ],
            }

        connected = []
        not_connected = []
        platforms = [
            ("GA4 / Product Analytics", p.has_ga4, f"{base}/connect"),
            ("GTM / Tag Manager", p.has_gtm, f"{base}/connect"),
            ("Google Ads", p.has_ads, f"{base}/connect"),
            ("Google Search Console", getattr(p, "has_gsc", False), f"{base}/connect"),
            ("BigQuery", p.has_bq, f"{base}/connect/bigquery"),
            ("Meta Ads", p.has_meta, f"{base}/connect/meta"),
            ("TikTok Ads", p.has_tiktok, f"{base}/connect/tiktok"),
            ("Snapchat Ads", p.has_snap, f"{base}/connect/snap"),
            ("LinkedIn Ads", getattr(p, "has_linkedin", False), f"{base}/connect/linkedin"),
            ("Pinterest Ads", getattr(p, "has_pinterest", False), f"{base}/connect/pinterest"),
            ("X Ads", getattr(p, "has_x", False), f"{base}/connect/x"),
            ("Reddit Ads", getattr(p, "has_reddit", False), f"{base}/connect/reddit"),
            ("Bing Webmaster Tools", getattr(p, "has_bing", False), f"{base}/connect/bing"),
            ("Amplitude", p.has_amplitude, f"{base}/connect/amplitude"),
            ("Adobe Analytics", p.has_adobe_analytics, f"{base}/connect/adobe"),
            ("Adobe Launch", p.has_adobe_launch, f"{base}/connect/adobe"),
            ("Redshift", p.has_redshift, f"{base}/connect/redshift"),
            ("Snowflake", p.has_snowflake, f"{base}/connect/snowflake"),
        ]
        for name, is_c, url in platforms:
            (connected if is_c else not_connected).append(
                name if is_c else {"platform": name, "connect_url": url}
            )

        # Brief tool map
        tool_summary = sorted(tm._tools.keys())
        return {
            "user_email": u.email,
            "active_project": {
                "project_id": p.project_id,
                "name": p.project_name,
                "slug": p.project_slug,
                "your_role": p.role,
            },
            "connected_platforms": connected,
            "disconnected_platforms": not_connected,
            "available_tools": tool_summary,
            "hint": "Pass tool_name=<name> to get detailed action docs for any tool.",
        }

    get_session_context.__doc__ = SESSION_CONTEXT_DOC
    tm.add_tool(get_session_context, name="get_session_context")

    # ── generic_tool_read / generic_tool_write stubs ───────────────────────
    async def generic_tool_read(capability: str, action: str | None = None, args: dict | None = None) -> dict:
        return {
            "error": True,
            "error_type": "not_implemented",
            "message": (
                f"generic_tool_read is a placeholder for future capabilities. "
                f"No handler registered for capability='{capability}'."
            ),
        }

    generic_tool_read.__doc__ = GENERIC_READ_DOC
    tm.add_tool(generic_tool_read, name="generic_tool_read")

    async def generic_tool_write(
        capability: str, action: str | None = None, args: dict | None = None
    ) -> dict:
        return {
            "error": True,
            "error_type": "not_implemented",
            "message": (
                f"generic_tool_write is a placeholder for future capabilities. "
                f"No handler registered for capability='{capability}'."
            ),
        }

    generic_tool_write.__doc__ = GENERIC_WRITE_DOC
    tm.add_tool(generic_tool_write, name="generic_tool_write")

    logger.info(f"Unified tool surface active — {len(tm._tools)} tools: {sorted(tm._tools.keys())}")
