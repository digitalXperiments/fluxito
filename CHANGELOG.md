# Changelog

All notable changes to Fluxito will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Ask Fluxito — native AI assistant.** A ChatGPT-style assistant embedded in the app
  (new `/ask` page). Bring your own AI-provider key; the harness is **vendor-owned (raw
  HTTP, no SDKs)** behind a normalized provider interface: it reasons, asks a clarifying
  question when a request is ambiguous, then calls the Fluxito MCP tools in a loop and
  streams the answer back over SSE. Tool access is **read-only**
  (analytics/tag-manager/marketing/warehouse/SEO reads, dashboards, tracking-plan reads,
  audits, and cross-connector analysis) — the assistant cannot modify anything. In-process
  tool dispatch inherits the caller's RBAC, and every call is recorded to the Activity Log.
  Conversations persist per project/user (migration `064`: `conversations`,
  `chat_messages`, `ai_provider_keys`).
- **Six AI providers.** Anthropic (native) plus **OpenAI, xAI Grok, Google Gemini,
  Mistral, and LM Studio** (the latter five via a configurable-base-URL OpenAI-compatible
  adapter; migration `065` adds a per-key base URL). OpenAI uses `max_completion_tokens`
  for current models. Provider key model defaults are user-selectable, with a "Custom…"
  option for any model id.
- **AI keys management.** A role-scoped **Settings → AI keys** panel: a card per provider
  with connection status, Add/Edit/Remove, **Set as default** (which provider Ask Fluxito
  uses; migration `066`), **Test connection**, a show/hide key field, per-provider
  get-key links, and a model dropdown. Keys are encrypted at rest (Fernet) and scoped to
  your account within the project.
- **Superadmin model catalog.** A **Settings → AI models** tab (superadmin) to add extra
  model names that appear in every user's per-provider model dropdown.
- **Chat experience.** Streaming responses with **Markdown rendering** (tables, code,
  lists), tool-call chips shown inline in arrival order, auto-generated conversation
  titles, and a **run-info rail** summarizing tokens, tool calls, model(s) used, and an
  estimated cost — per model when several are used in one conversation.

### Changed
- **Navigation: simplified sidebar.** The seven feature groups collapse into two short,
  workflow-ordered clusters — **Set up** (Connections, Tracking Plan, Context) and **Work**
  (Dashboards, Automations, Tag Auditing) — plus Home, Ask Fluxito, Tutorials, and a single
  **Settings** entry. "Auditing" is renamed **Tag Auditing**. Every permission gate is
  preserved exactly.
- **One Settings destination.** Profile, project settings, install Integrations, System
  settings, the superadmin Admin console, AI keys, AI models, and the Activity Log are
  unified into a single role-scoped `/settings` with a left tab rail
  (Account · Project · Workspace · Platform) — you only see the tabs your role allows. The
  existing pages are reused as embedded panels (no form rewrites); the old paths
  (`/profile`, `/settings/integrations`, `/settings/system`, `/admin`,
  `/project/{slug}/settings`, `/activity-log`) now redirect into the matching tab, so
  existing links keep working.
- **Context = KPI Library + Business Context.** Merged into one `/context` page with two
  tabs; `/kpi-library` and `/business-context` redirect in.
- **Dashboards = live dashboards + template gallery.** Merged into one `/dashboards` page
  with "Your dashboards" and "Gallery · + New from template" views; `/live-dashboards` and
  `/templates` redirect in.

### Fixed
- **Activity Log now records Ask Fluxito tool calls** — the in-process bridge previously
  bypassed the audit instrumentation, so the assistant's tool calls never appeared.

## [1.1.15] — 2026-06-15

### Added
- **Tracking plan: validation rules engine + Issues screen.** A configurable, industry-standard rules
  engine (`tp_validation_rule`, migration 062) with seeded defaults — name casing, name regex,
  required description/owner, required property, property type-consistency, and PII-must-be-flagged.
  A new **Issues** view groups findings by severity and deep-links to the offending event/property;
  a **Rules** sub-tab toggles each rule and sets its severity. The top-bar **Validate** button opens
  the view. Exposed over MCP via `list_rules` / `update_rule` / `set_rule_enabled`, and `validate`
  now returns rule-based findings. `is_publishable` is true only when there are no error-severity findings.
- **Tracking plan: AI reconcile (`reconcile_preview` + `reconcile_apply`).** MCP agents can now ingest externally-discovered event lists (GA4, GTM, Adobe, dataLayer) and reconcile them against the tracking plan. `reconcile_preview` is a deterministic dry-run that normalizes event names to snake_case (or camelCase/Title), fuzzy-deduplicates against existing events, and returns new/updated/unchanged/conflicts with no writes. `reconcile_apply` applies per-event create/update/skip decisions using existing service primitives, logs one activity row per applied event, and commits atomically.
- **Tracking plan integrations.** (1) A `tracking_plan_coverage` audit type plus a `run_coverage_audit`
  MCP action that saves rule + coverage findings into the auditing platform (`/audits`). (2) A
  **validation gate**: publishing or merging a branch is blocked when error-severity issues exist,
  with the blocking count surfaced. (3) A **Tracking Plan health card** on the project Home (event
  count · open issues · coverage %). (4) A light **metric→dashboard-card link** (`dashboard_card_id`
  on `TPMetric`, migration 063) so defined metrics show whether they're wired to live data, with a
  `metric_not_measured` finding for unmeasured metrics.

### Fixed
- **Tracking plan: the event name is now editable.** The event name in the editor header was
  rendered as a read-only label, so events could not be renamed and the name of a brand-new event
  could not be set. It is now an inline input bound to the buffered draft, with live empty/duplicate
  validation and a casing hint; the master list and selection refresh after a rename. The
  `+ New event` flow now focuses the name field.

## [1.1.14] — 2026-06-14

_These entries accumulated in Unreleased across 1.1.12–1.1.14 and shipped by v1.1.14 (2026-06-14); they were not promoted at the time and are consolidated here._

### Added
- **Public dashboard preset filter bar.** Shared dashboards now show the owner-configured
  date-range preset chips above the card grid. Clicking a preset re-fetches live card data
  for that date range (15 s timeout, 4 concurrent cards, 1-hour Redis cache) and re-renders
  the grid — so recipients can explore different time windows without needing dashboard
  access. Falls back to each card's cached snapshot on individual card failure. The frozen
  default view (no preset selected) is completely unchanged.

### Fixed
- **Scorecards no longer show "No data" when served from cache or on card error/timeout.**
  Fallback and frozen-cache paths now run the same snapshot normalisation as the live path,
  so GA4 results are correctly reshaped into a `metrics` array before rendering.
- **Dashboard share links now restore the correct date range.** The server used to read
  `?start`/`?end` while the client emitted `?date_range_start`/`?date_range_end`; both
  forms are now accepted. Active date-range chips are also synced to the restored range on
  page load so the UI reflects what's actually applied.
- **`date_locked` cards now only lock the date range, not all filters.** Dimension filters
  (country, device type, etc.) were previously also blocked on date-locked cards; they now
  pass through correctly.
- **Dimension filter selects with static options now include an "All" option** so users can
  reset a pre-populated select back to no filter.
- **Filter preset chips no longer silently corrupt date ranges containing commas.**
  Dates are stored in separate `data-range-start`/`data-range-end` attributes instead of a
  single comma-delimited `data-range` attribute.
- **Dimension filter options lazy-load before the initial card load** to avoid a race where
  user interaction during the concurrent fetch overwrote freshly-loaded cards with stale data.
- **`query_token_required` is now enforced on the live `/data` endpoint.** Previously only
  the batch endpoint checked the token; unauthenticated callers could bypass it on public
  dashboards with token-gating enabled.
- **`compare=` and other unknown URL params no longer fragment the Redis cache key.** These
  params are now classified as reserved and excluded from the override dict used for cache
  key computation.
- **Chart resize works on every window resize, not just the first.** The ECharts resize
  listener was registered with `{once: true}` and removed itself after firing once; replaced
  with a stable debounced handler.
- **Concurrent `mountCharts` calls no longer render stale card data.** The retry-on-ECharts-
  load mechanism previously captured the first call's arguments; it now always uses the most
  recent arguments.
- **Date auto-detection no longer false-positives on dimension values starting with month
  names** (e.g. "market", "margin", "march_promo") or on 4-digit store/product codes
  outside the 1970–2100 year range.
- **GA4 date dimension columns (`yearMonth`, `dateHour`, `nthDay`, etc.) are no longer
  selected as the metric value in scorecard fallback mode**, which previously rendered
  garbage compact numbers like "202.4K".
- **Percentage and rate columns in tables are formatted correctly.** Python inferred
  `unit=percent` for columns matching `percent`/`pct` but the JS table renderer's rate
  check didn't match those patterns; both now agree.
- **`autoWide` card layout works for GA4 cards.** The heuristic previously checked the raw
  `card_type` string (`TABLE`, `METRIC`) but GA4 cards carry prefixed types (`GA4_TABLE`,
  `GA4_METRIC`); it now checks the resolved chart type so GA4 tables and scorecards are
  correctly widened.
- **The PDF/SSR scorecard path now shows formatted values** (`1.2M`, `37%`, `4m 33s`)
  rather than the raw number, matching the live JS renderer.
- **Delta pills use a single consistent set of CSS class names.** The JS emitted `pos`/
  `neg`/`neu` while `app.css` defined `.positive`/`.negative`/`.neutral`; classes are now
  aligned and the duplicate inline `<style>` block is removed.
- **Dead sparkline CSS removed from `app.css`.** The sparkline renders SVG `<rect>` elements
  but `app.css` had flex/span rules from an earlier bar-chart implementation that were never
  applied.
- **Pie chart `data-chart-option` attribute removed.** `mountCharts` never read this
  attribute; the dead escaped-JSON was removed from the DOM.
- **Dual-axis threshold corrected to 5× scale divergence** (was 2×, causing premature
  axis splits; the existing `scalesDivergent()` helper already used 5× but was not called).
- **Hub loading overlay now has a 9-second fallback timeout** so a cancelled navigation no
  longer leaves the hub permanently obscured.
- **Non-owner "older format" message replaced** with "Data temporarily unavailable" when a
  card has no cached result, which accurately describes the situation.

### Added
- **Tracking Plan — a relational, industry-standard data-governance workspace** that replaces the
  markdown Solution Design Reference (SDR). The plan is now a structured database rather than a
  generated document: first-class **events**, a reusable project-level **property library**
  (events attach library properties with per-event overrides), **sources** and **destinations**
  with source→destination routing and per event×destination mapping rules, **categories**,
  reusable property **bundles**, and **event metrics**. Properties carry rich constraints (enum,
  min/max, regex, PII and list flags, and nested object members). The whole plan is editable in a
  redesigned, professional master–detail UI built around an **explicit Save** model — edits are
  buffered locally and nothing persists until you click Save, with Discard to revert — alongside a
  Comments/Activity drawer with @mentions, branch-scoped editing with a request-review / approve /
  merge-and-publish workflow, and an immutable version history with snapshot diffing.
- **`tracking_plan` MCP tool** so an AI can author and validate the plan directly: one meta-tool
  with validated CRUD across events, properties, sources, destinations, routing, categories,
  bundles, metrics, branches, validation and publishing — plus convenience actions `get_overview`
  and `create_event_with_properties`.
- **Structured Markdown and Excel exports** of the tracking plan, generated on demand. The
  markdown that was previously the source of truth is now just one of these export artifacts.
- **Mobile-responsive Tracking Plan workspace.** On phones the section nav collapses to a
  horizontal scrollable pill strip and the master–detail editors become a drill-down — tap a
  list row to open the full-screen editor with a back control — with forms, data tables, the
  comments drawer and dialogs all adapted for small screens.

### Changed
- **Audit and Live Tag Test now read the published tracking-plan snapshot** instead of parsing the
  markdown SDR. Analytics, tag-manager and live-tag-test behaviour is unchanged — the same
  information now comes from the structured plan.

### Removed
- **The markdown Solution Design Reference (SDR).** Its parser, MCP actions, templates, Excel
  export, routes, data model and the "Solution Design" navigation entry are gone, replaced by the
  relational Tracking Plan and its exports. A database migration drops the legacy `sdr_*` tables;
  it is one-way, so apply it only once the new code is live.

## [1.1.11] — 2026-06-14

### Added
- **Revamped dashboard filters — five new filter types.** The filter bar now supports
  multi-select dropdowns (with removable chips), free-text search, numeric min/max
  ranges, on/off toggles, and a custom start/end date range, alongside the existing
  single-select dropdowns and date presets. Filters translate to real query syntax for
  each source — GA4 dimension/metric filters, parameterized warehouse SQL (quote-escaped,
  injection-safe), and marketing parameters — and are validated at deploy time so a
  filter can never silently do nothing.
- **Date-range comparison on every card.** Turn on Compare to see two periods at once:
  scorecards show a coloured ▲/▼ delta, tables gain Prev and Δ% columns, line charts
  overlay a faded dashed comparison line, and bar charts pair current vs. previous bars.
  Choose "Previous period", "Previous year", or a custom comparison range. A one-line
  "biggest movers" banner summarizes the largest changes.
- **Click-to-filter (cross-filtering).** Click a bar or point in a chart to filter the
  whole dashboard by that value (when a matching filter exists).
- **Shareable filtered views.** The active filters, date range, and compare state are
  encoded in the URL, so a filtered/compared view can be bookmarked and shared, and the
  PDF export captures exactly what's on screen.
- **Per-card CSV export** for table cards, and a visible **error + Retry** banner when a
  card fails to refresh (instead of silently showing stale data).
- **`dashboard_deploy_batch` now accepts a `filters` spec** and, when you omit it,
  suggests sensible dropdowns inferred from the cards' dimensions to confirm before
  deploying.

### Changed
- **Dashboards now cache live data for 24 hours (was 1 hour) and show when it was last
  pulled.** A freshness banner reads "Cached data from <timestamp> — refreshes daily"
  with a Refresh-now link; the TTL is configurable per dashboard. Each filter and compare
  combination caches independently.
- **Professional "Looker Clean" chart restyle.** A consistent Google-data-viz colour
  palette (the same category is the same colour across every card), hairline gridlines,
  compact number formatting (48.2K / 1.3M), a tidy legend, and softer bars.

## [1.1.10] — 2026-06-14

### Added
- **API rate limits and real usage for every connector.** A new **API Limits** tab in
  Project Settings and an **API limits** section on the Home page list, for each connected
  tool, its published rate limit (from the provider's official docs, with a "reviewed" date)
  next to the **actual number of calls Fluxito has made on your behalf over the last 30
  days** — so you can see at a glance how fast dashboards and MCP calls burn through a quota.
  Each row stays minimal; an info button opens a modal with the full breakdown (every
  documented limit, what happens when you hit it, the usage headers to watch, and a link to
  the official docs). Connected tools are shown first; the full catalog of all 20 supported
  connectors is browsable below to check limits before connecting. Covers GA4, GTM, BigQuery,
  Google Ads, Search Console, Meta, TikTok, Snapchat, LinkedIn, Pinterest, X, Reddit, Apple
  Search Ads, Bing Webmaster, Adobe Analytics, Adobe Launch, Marketo, Amplitude, Redshift and
  Snowflake. Usage counts the upstream calls we make (cache hits don't count); a few
  connectors that don't go through the cache layer yet show "no calls" until instrumented.
  Limit figures are point-in-time — a `python -m app.connectors.rate_limits_drift` helper
  flags entries that are due for re-verification.

## [1.1.9] — 2026-06-13

### Changed
- **Share PDF now looks like the live dashboard — charts and all.** Exports (and the
  scheduled email/Slack report PDFs) are now rendered by a real headless browser that
  loads the actual dashboard, so they include the styled scorecards, the line/bar/pie
  charts, and the data tables you see on screen. Previously the PDF was a stripped-down
  text-and-grid report because the old renderer had no way to draw the JavaScript charts.
- **Roomier dashboard layout.** Added breathing room between the date-range filter bar
  and the first row of cards so the view no longer feels cramped.

### Fixed
- **Share PDF on a public/shared dashboard could fail with an `oklab` colour error, and
  the header sat tucked under the top navigation bar.** The export pipeline no longer
  depends on the brittle in-browser screenshot path, and shared dashboards now leave
  proper space below the fixed nav.
- **Share PDF ignored the date range you picked.** The export now reflects the selected
  range (e.g. "2024 Full Year") instead of always falling back to the default window.

## [1.1.8] — 2026-06-12

### Changed
- **Share PDF now matches the dashboard you see on screen.** The export used to render a
  separate, plainer server-side layout (stacked value boxes, no charts) that looked nothing
  like the live view. It now captures the dashboard exactly as rendered — colored scorecard
  tiles, charts, the current light/dark theme, and the active date range and filters — and
  lays it across multi-page A4. (Scheduled email/Slack report PDFs are unchanged.)
- **Live dashboards cache their data on load, so reopening one no longer re-queries the
  analytics APIs every time.** The first open (or pressing Refresh) pulls from the upstream
  sources and caches the result; reloads then serve from that cache for up to an hour.
  Refresh always forces a fresh query and repopulates the cache, and "last refreshed" now
  reflects when the data was actually pulled.

### Fixed
- **Dashboard scorecards no longer sit jammed against the top edge of their card.**
  Single-metric KPI tiles had almost no breathing room above the title; they now have
  balanced top padding and a clear gap before the metric, in both light and dark mode.

## [1.1.7] — 2026-06-12

### Fixed
- **GA4 scorecards showed the date instead of the metric.** Single-value cards such as
  "Total Sessions" or "Engagement Rate" rendered the same meaningless `20.24M` with a
  tiny `20241003` label — the card was displaying the GA4 date (in `YYYYMMDD` form) as
  the value. Scorecards now show the correct figure: a period total for counts and money,
  an average for rates and durations, with the sparkline plotting the real metric trend.
  The same fix makes these cards render in PDF and scheduled email/Slack reports, which
  previously showed "No metrics returned."

## [1.1.6] — 2026-06-12

### Added
- **"Check for updates" button in the admin Updates tab.** Super-admins can now
  force an on-demand version check instead of waiting on the passive 6-hour cache.
  The button re-polls GitHub immediately, refreshes the displayed version status,
  and — when a newer release exists — reveals and scrolls to the "Update now"
  action. A short per-instance cooldown protects GitHub's rate limit.

### Changed
- **The MCP server now describes itself, so any client can use it without guessing.**
  Every tool's description, its served JSON schema, and its error messages are now
  generated from one source of truth, so they can no longer drift from the code.
  Each tool lists its actions (and which platform each action is valid for), a new
  `describe` action returns a machine-readable spec of any action's parameters, and a
  missing parameter comes back with the full required/optional list plus a runnable
  example — in a single round-trip. Common parameter traps are spelled out in the tool
  text itself (e.g. the budget field is `daily_budget_usd`; Adobe Launch reuses GTM
  parameter names with different meanings). A build check fails if any advertised
  action lacks a spec or a working handler.
- **Rebuilt the Fluxito skill.** Refreshed into a whole-platform operating guide with
  audit and dashboard workflows, on top of the existing tracking-plan (SDR) depth. The
  per-parameter reference now lives in the server, so the skill focuses on method and
  judgment and complements the self-describing tools.

### Fixed
- **Dashboards reject incomplete cards at deploy time.** A card missing its `action`
  (or a required parameter) used to deploy "successfully" and then return no data on
  refresh. The deploy now fails fast with a clear message naming the missing field, and
  the paid-social card validation (Meta/TikTok/Snap) — previously skipped due to a
  platform-name mismatch — is enforced again.
- **Removed analytics and audit actions that were advertised but never ran.** Several
  GA4 actions with no handler, and an entire set of ~21 `run_audit` actions (tag rule
  book, live tag test, audit history) that returned an internal error, now either work
  correctly or no longer appear — so clients only see actions that succeed.
- **Audit score history** no longer errors — fixed a SQL bug in the score-trend query.
- **Cross-platform revenue attribution** now includes warehouse revenue again, instead
  of silently dropping it.
- **Warehouse SQL safety** no longer false-rejects valid read-only queries (e.g. a
  column named `updated_at`) and now blocks additional write statements such as MERGE
  and COPY.

### Security
- **Audit, tag-rule-book, and live-tag tools now follow role permissions consistently.**
  Their write actions (saving audit results, editing rule books, recording test
  sessions) require write access even when invoked through the audit tool, and the
  direct tools are no longer blocked for users who legitimately have audit access.
- **Secrets are no longer written to the activity log.** Tokens and credentials returned
  by a tool — including a freshly rotated dashboard share token — are redacted from the
  saved audit trail. The live response still shows the value once.

## [1.1.4] — 2026-06-11

### Added
- **Tag Auditing platform.** Run tag/tracking audits and review the results in a
  dedicated UI — an audits hub plus per-run detail pages — with the findings saved
  per project. New `run_audit` and `save_audit_result` MCP tools let an AI execute an
  audit and persist a structured, scored result.
- **Live Tag Testing with multi-platform rule books.** Capture a page's network
  traffic and validate the tags that actually fired against per-platform rule books,
  via the new `live_tag_test` flow. Ships rule books for 20+ platforms — GA4
  (standard, ecommerce, config), Google Ads (conversion & remarketing), Meta Pixel,
  TikTok, Snap, Pinterest, LinkedIn Insight, Microsoft UET, Twitter/X, Criteo,
  Floodlight, Hotjar, Mixpanel, Segment, Amplitude, and Adobe Analytics — plus a
  `tag_rulebook` tool for inspecting and validating against them.
- **Dashboard filter presets.** Save custom date-range chips on a live dashboard so
  viewers can switch between the ranges that matter for that report.

### Changed
- **The MCP server now works reliably across every AI client.** Tool definitions were
  rewritten to a strict, widely-compatible JSON-Schema shape (each tool advertises its
  valid actions as an enum, and optional parameters no longer use nullable unions), and
  the transport was switched to a stateless, single-response mode. Together these fix
  the connection timeouts and malformed tool calls seen with stricter clients such as
  Grok, while remaining fully compatible with Claude, ChatGPT, and Cursor.
- **Live dashboards are faster and far more resilient.** Cards now refresh in parallel
  with a per-card timeout and fall back to the last cached result instead of hanging
  when an upstream source (GA4, BigQuery, …) is slow — one stuck card can no longer
  freeze the whole dashboard. Filter changes are debounced and stale in-flight requests
  are cancelled, so rapidly switching date ranges or dimensions no longer races or
  flickers between results.

### Fixed
- **Stronger project isolation on dashboards.** Managing, viewing, and refreshing a
  dashboard now verify active **project membership** in addition to ownership, so a user
  removed from a project can no longer reach that project's dashboards. (Live refreshes
  already resolved credentials strictly from the dashboard's own project — no data or
  credentials were exposed across projects; this tightens the authorization checks on
  every dashboard route to match.)
- **Dashboard date filters could be silently ignored.** A card's "lock dates" flag, when
  stored as text, was misread as always-on, which suppressed date-range filters on that
  card. The flag is now parsed correctly.

## [1.1.1] — 2026-06-11

### Added
- Support for using Fluxito's MCP server from remote or headless environments (SSH, containers, CI, servers without a browser, etc.).
- **Personal Access Tokens (PATs)** — generate static, long-lived bearer tokens directly from the Profile page on a machine with a browser, then paste them into remote client configs.
- Out-of-band OAuth flow that lets you open an auth URL in a local browser and paste a code back to a remote client to complete authentication.
- New **MCP Access Tokens** section on the Profile page for creating, listing, and revoking tokens for remote use.

### Changed
- MCP clients now support three authentication options: standard browser OAuth, manual code paste (OOB), and static Personal Access Tokens. This makes it practical to connect agents like Claude Code on remote boxes, Hermes, and other headless or config-driven tools.
- Connection documentation updated with clear guidance for remote and headless setups.

## [1.1.0] — 2026-06-04

### Added
- **Role-based access control (RBAC) for projects.** A single role definition now
  governs both what an AI can do over MCP and what a teammate sees in the web UI.
  Owners and admins keep full access; a **member** starts with *zero* access until
  granted one or more roles. Permissions have two axes:
  - **Tools** by domain (analytics, tag manager, marketing, SEO, warehouse,
    dashboards, knowledge, automation, analysis, tracking plan) at **read** / **write**
    granularity.
  - **Connections** per provider (GA4, GTM, Google Ads, Search Console, BigQuery,
    Meta, TikTok, LinkedIn, Pinterest, Snap, X, Reddit, Bing, Apple, Amplitude,
    Adobe Analytics/Launch/Marketo, Redshift, Snowflake).

  A member can hold several roles (permissions are the union). Enforcement is
  defense-in-depth: ungranted tools are hidden from the MCP `tools/list`, every tool
  call is checked again at execution time, connections are filtered per project, and
  the web sidebar/routes are guarded by the same resolver. Roles are managed under
  **Settings → User Roles**, and the whole feature rolls out behind a per-project
  toggle (default **off**) — existing projects are unchanged until an admin enables it.

### Changed
- **Redesigned Project Settings.** Streamlined tabbed layout with a guided, sectioned
  role editor; "Members" is now **Users**, with a compact roles workflow for inviting
  people and assigning roles.
- **Refreshed branding** — new Fluxito logo mark, wordmark, and favicons / app icons.

### Fixed
- **Cross-tenant connection-presence isolation.** Building a project's context could
  report another project's warehouse/credential connection *presence* (BigQuery,
  Adobe, Marketo, Redshift, Snowflake, Amplitude) because those lookups were scoped by
  the wrong table. Each credential lookup is now scoped to its own project (or user).
  No data or credentials were ever exposed — only the on/off presence flags — but the
  flags are now correct and tenant-isolated.
- **RBAC tool hiding now applies to real MCP clients.** The role-based `tools/list`
  filter was only wired to an in-process code path, so connected AI clients still saw
  the full tool list (calls were always blocked at execution by the backstop, so no
  access leaked). The filter is now registered on the MCP protocol handler, so
  ungranted tools are correctly hidden from the list as well.

## [1.0.9] — 2026-06-02

### Added
- **Apple Search Ads** integration — connect via OAuth 2 client credentials, then
  read App Store campaign and ad-group performance and audit conversion/tracking
  setup through the `marketing_read` / `marketing_audit` tools.
- Per-instance OAuth app credentials for Apple Search Ads (admin **Integrations** panel).
- **Adobe Marketo Engage** integration — connect Marketo via its own LaunchPoint
  credentials and REST endpoint (separate from Adobe Analytics/Launch, which use
  Adobe IMS). Once connected, read leads, lists, lead activities (opens, clicks,
  form fills), campaigns, programs, and email/landing-page/form assets; audit API
  usage vs. daily quota and core-field data quality; and create/update leads, add or
  remove leads from lists, and trigger or schedule smart campaigns — all through the
  `marketing_read` / `marketing_audit` / `marketing_write` tools (`marketo_*` actions)
  and `run_audit`. Includes a `/connect/marketo` setup page and a step-by-step
  LaunchPoint setup tutorial.

### Changed
- **Connections page now showcases the tools inside each platform.** Instead of an
  opaque "Google Suite" label, the Google card lists its products as labelled icon
  chips — Analytics, Tag Manager, Ads, Search Console — and the Adobe card lists
  Analytics and Launch (with Adobe Campaign shown as "coming soon"), so it's clear at
  a glance what each connection unlocks. BigQuery remains its own separate card (it is
  Google-branded but uses its own service-account setup).

## [1.0.7] — 2026-06-01

### Added
- **X (Twitter) Ads** integration — connect via OAuth, then read campaign and
  line-item performance, audit conversion-tracking setup, and pause/activate
  campaigns through the `marketing_read` / `marketing_audit` / `marketing_write` tools.
- **Reddit Ads** integration — connect via OAuth, then read campaign and ad-group
  performance, audit pixel/conversion tracking, and update campaign status and daily
  budget through the marketing tools.
- **Bing Webmaster Tools** integration — connect via Microsoft OAuth, then read
  verified sites, search query stats, crawl stats, index coverage, and link counts
  through the `seo_read` tool (`bing_*` actions), alongside Google Search Console.
- Per-instance OAuth app credentials for X, Reddit, and Bing (admin **Integrations** panel).

### Fixed
- **Activity Log** now records MCP tool calls. The instrumentation hook that writes
  each tool call to the audit trail was never actually installed, so the Activity Log
  stayed empty no matter how many tools were used. Wiring it up also re-activates the
  per-tool timeouts, circuit breaker, and per-call active-project resolution that share
  the same hook.

## [1.0.6] — 2026-06-01

### Changed
- Release notes are now taken from the curated `CHANGELOG.md` entry for each version
  instead of being auto-generated from commit messages, and the release pipeline no
  longer commits the changelog back to `main` — the changelog is written by hand
  before each push.

## [1.0.5] — 2026-05-31

**Full Changelog**: https://github.com/digitalXperiments/fluxito/compare/v1.0.4...v1.0.5

## [1.0.4] — 2026-05-31

### Added
- In-product version display and one-click self-update for super-admins, delivered
  via published GHCR images and a privileged `fluxito-updater` sidecar with
  automatic rollback on failed updates.
- Automated push-to-main release pipeline: auto-incrementing patch versions,
  multi-arch (amd64/arm64) GHCR images, and auto-generated GitHub releases.
- `update_checks_enabled` instance setting (disable for air-gapped installs).

### Changed
- Default Docker deployment now pulls the published GHCR image; build-from-source
  moves behind `docker-compose.build.yml`.
- Version is now sourced from a single `VERSION` track file (+ build-time
  `APP_VERSION`), resolving prior drift between `pyproject.toml` and `config.py`.

### Removed
- `deploy/` folder removed from the public repo. Production orchestration (compose, update script, reverse-proxy config) is environment-specific and is kept outside version control. Self-hosting is covered by the root `docker-compose.yml` and the Production notes in the README (including the required nginx `/mcp` no-buffering config).

## [1.0.3] — 2026-05-30

### Added
- Solution Design Reference (SDR) v2: richer audit sections (Executive Summary, Gap Register, Conversion Audit, Consent, Roadmap), viewer tabs, full Excel export of every section, and storing + downloading the original source `.xlsx` (validated, 2MB cap).
- Access control: a super-admin role (first setup account), an admin panel at `/admin` (Users + Access Requests), and a request-access flow gated by the `require_access_approval` instance setting (default off — open signup unchanged for existing self-hosts).
- Per-user MCP rate limiting (super-admin configurable and exempt) with an admin Rate Limits control.
- Whitelabel branding: brand name / wordmark / accent settings, brand-aware chrome and invite emails, and an admin Branding tab.
- Marketing landing page at `/` for logged-out visitors (hero, problem, how-it-works, features, platforms, OSS sections, video slot), brand-aware with an overridable `og:description`.
- Auto-provisioned personal project for users with none; one-time temp credentials for invites and admin password resets (no SMTP required).
- Production deployment under `deploy/` that builds from a local source checkout on the host and deploys by pulling `main` (`deploy/update.sh`).

### Changed
- MCP active-project now resolves per call from Redis (fixes `no_active_project` in batched/parallel tool calls).
- CI is now a pure quality gate (lint, typecheck, test, build smoke); it no longer publishes container images.
- Connections de-duplicated to one card per account.

### Fixed
- SDR User-Properties table rendering; Members-tab native validation bubble overlap; account-takeover vectors in the register / invite / password-reset flows (never set or reset a password on an email that already has one).

### Removed
- GHCR image publishing (`publish-sha` / `publish-demo` jobs) and the `v*` tag trigger; the `demo/` folder is now the production `deploy/` stack.

## [1.0.2] — 2026-05-28

### Added
- DB-backed System Settings screen at `/settings/system` for post-bootstrap runtime settings, including SMTP, rate limits, Sentry, CORS, GCS, and tool flags.
- Public demo infrastructure with automated deployment and MCP access restrictions.
- Multi-arch Docker image builds (AMD64 + ARM64) via QEMU.

### Changed
- `.env.example` is now a minimal bootstrap contract: database, Redis, signing/encryption keys, public URL/MCP redirects, and the special-case Google OAuth app only.
- SMTP and rate-limit reads now prefer DB-backed system settings and fall back to deprecated env/default values.
- CI split into fast SHA-based image builds and stable release-triggered demo image publishing.

### Fixed
- Release workflow now uses `semantic-release version` (was incorrectly using `publish --tag` which never created GitHub Releases).

## [1.0.1] — 2026-05-25

### Changed
- OAuth app credentials are now **DB-only**. The `.env` fallback for per-platform `*_CLIENT_ID` / `*_CLIENT_SECRET` env vars has been removed; configuration happens exclusively via `/settings/integrations`. Existing self-hosters who relied on env vars need to re-enter the credentials in the UI.
- First-admin setup wizard no longer offers "Sign in with Google" — that path required env-based Google credentials. Email/password is the only option for the first admin; Google sign-in becomes available for subsequent users once Google OAuth is configured at `/settings/integrations`.
- README rewritten — tighter flow, added local-tunnel-via-ngrok guide for testing Claude.ai against a local instance, dropped technical sections (curious devs read the code).
- `CONTRIBUTING.md` slimmed — clearer scope for what we accept vs decline.
- All plan/quota limits removed — projects are fully unlimited in the open-source release.

### Added
- `.github/CODEOWNERS` — declares reviewer ownership; pairs with branch protection in repo settings to enforce maintainer approval.

### Removed
- `app/templates/{landing,features,platforms,docs,legal/*}.html` and `app/api/public_routes.py` — moved to `parked/` (gitignored). Marketing pages now live in a separate static-site repo.
- Internal/technical docs: `docs/{ARCHITECTURE,API_REFERENCE,CONNECTORS,DESIGN_SYSTEM,FEATURES,MIGRATIONS,SDR_FEATURE_SPEC,STRATEGY_AND_DECISIONS,TESTING,TOKEN_LIFECYCLE,TOOLS_REFERENCE}.md`. Tutorials and `DEPLOYMENT` / `TROUBLESHOOTING` remain. Devs interested in internals can read the source.
- `scripts/import_env_oauth_apps.py` — obsolete with env-fallback gone.

### Fixed
- `/auth/signout` link in the user menu was 404'ing — now correctly links to `/signout`.
- Anonymous users are now redirected to sign-in from `/activity-log`, `/templates`, and `/live-dashboards` pages.
- Private dashboard query API routes gated behind session auth.
- `/setup` and `/api/` routes exempted from CSRF to fix setup wizard and API calls.
- Alembic multi-head migration repaired (039_repair_platform_indexes).

## [1.0.0] — initial open-source release

### Added
- 15 marketing/analytics platform connectors via MCP: GA4, GTM, Google Ads, Search Console, BigQuery, Snowflake, Redshift, Adobe Analytics, Adobe Launch, Amplitude, Meta Ads, TikTok Ads, Snap Ads, LinkedIn Ads, Pinterest Ads.
- Web UI for self-hosters at `/connect`, `/dashboards`, `/sdr`, `/automations`.
- Per-install OAuth-app credential management at `/settings/integrations` (admin-gated).
- First-run `/setup` wizard for creating the initial admin (email/password or Google sign-in).
- Auto-generated `APP_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` on first boot, written to `.env.local` (gitignored).
- Docker Compose stack with healthchecked Postgres and Redis services.
- One-click deploy templates for Render (`render.yaml`) and Railway (`railway.json`).
- Apache 2.0 license + standard project files (NOTICE, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT).

### Changed (versus the internal predecessor)
- OAuth client credentials moved from `.env` to a database-backed UI; env-based config remains as a sysadmin fallback.
- Single rate-limit tier (was per-plan tiers).

### Removed (versus the internal predecessor)
- Stripe billing integration, plan tiers, quota enforcement.
- Platform-admin panel and the `admin_role` user column.
- Fly.io-specific deployment configuration.

[Unreleased]: https://github.com/digitalXperiments/fluxito/compare/v1.1.11...HEAD
[1.1.11]: https://github.com/digitalXperiments/fluxito/compare/v1.1.10...v1.1.11
[1.1.10]: https://github.com/digitalXperiments/fluxito/compare/v1.1.9...v1.1.10
[1.1.9]: https://github.com/digitalXperiments/fluxito/compare/v1.1.8...v1.1.9
[1.1.8]: https://github.com/digitalXperiments/fluxito/compare/v1.1.7...v1.1.8
[1.1.7]: https://github.com/digitalXperiments/fluxito/compare/v1.1.6...v1.1.7
[1.1.6]: https://github.com/digitalXperiments/fluxito/compare/v1.1.4...v1.1.6
[1.1.4]: https://github.com/digitalXperiments/fluxito/compare/v1.1.1...v1.1.4
[1.1.1]: https://github.com/digitalXperiments/fluxito/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/digitalXperiments/fluxito/compare/v1.0.9...v1.1.0
[1.0.9]: https://github.com/digitalXperiments/fluxito/compare/v1.0.7...v1.0.9
[1.0.7]: https://github.com/digitalXperiments/fluxito/compare/v1.0.6...v1.0.7
[1.0.6]: https://github.com/digitalXperiments/fluxito/compare/v1.0.5...v1.0.6
[1.0.5]: https://github.com/digitalXperiments/fluxito/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/digitalXperiments/fluxito/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/digitalXperiments/fluxito/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/digitalXperiments/fluxito/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/digitalXperiments/fluxito/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/digitalXperiments/fluxito/releases/tag/v1.0.0
