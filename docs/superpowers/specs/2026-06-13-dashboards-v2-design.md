# Dashboards v2 — Filters, Compare, Caching & Restyle

**Date:** 2026-06-13
**Branch:** `task/dashboard-filters-0fe1a1`
**Status:** Design approved — pending implementation plan

## Summary

A multi-part upgrade to the Fluxito dashboarding system, delivered on one branch in
four reviewable phases (each green via `tox` before the next):

1. **Foundations** — shared date-label formatter (fixes the "202401" bug) + "Looker
   Clean" chart/color restyle.
2. **Filter revamp** — dashboard-level filter declarations supporting six widget
   types, a type-aware per-platform translation layer, deploy-time validation, an
   extended `dashboard_deploy_batch` schema, and a new `dashboard_update` MCP tool.
3. **Compare mode** — two-date-range comparison rendered across scorecards, line,
   bar, and table cards, plus "biggest movers" callouts and cross-filtering.
4. **Caching + freshness + shareable URLs** — 24h default cache TTL with a
   per-dashboard override, a visible freshness banner, unified public/live caching,
   a staleness-bug fix, and URL-serialized filter state.

The design is intentionally backward-compatible: existing dashboards keep working,
and old per-card filter wiring is synthesized into the new model on read.

## Goals

- Revamp the filter UI and support multiple filter types (point 1).
- Generate sensible default date filters when none are specified, always including a
  custom start/end range; auto-suggest other filters and confirm with the user before
  deploying (points 2 + 3).
- Make filters actually work against the analytics/marketing/warehouse tools (point 4).
- Make caching real and visible: 24h cache on first load with a "cached at <timestamp>"
  banner (point 5).
- Add date-range comparison to every chart/card type (point 6).
- Render dates in a human-readable format everywhere (point 7).
- Selected extra features (point 8): shareable filtered-view URLs, auto "biggest
  movers" callouts, a `dashboard_update` MCP tool, and cross-filtering.

## Non-Goals

- Real-time/streaming dashboards. Caching remains the model; "live" means
  re-query-on-demand, not push.
- A visual drag-and-drop dashboard builder. Dashboards are still built via MCP tools.
- Arbitrary user-authored SQL in filters. Filters translate to constrained,
  validated query fragments only.

---

## Current State (as explored)

- **Filter types today:** only date-range preset chips + single-select dimension
  dropdowns. No multi-select, text, numeric range, toggle, or custom-range picker.
- **Wiring:** per-card `filter_hooks` (dot-path map from UI key → card param path) in
  `app/dashboards/filter_hooks.py:apply_overrides`; warehouse cards use `{placeholder}`
  substitution; `filter_presets` stored per-dashboard (migration 053). Dimension
  filters are reverse-engineered by scanning every card in `dashboard_routes.py`.
- **Default range:** hardcoded `30daysAgo → today` in `live_view.html`.
- **Caching:** live dashboards cache in Redis (`dashdata:v1:{slug}:{digest}`) with a
  **1h** TTL keyed on `dashboard.updated_at` + filters; **no visible banner** (only a
  relative "last refreshed Xm ago" ticker). Public dashboards serve a frozen DB
  `result_cache`. Staleness bug: a card-level refresh doesn't bust the dashboard cache
  because the key keys on `dashboard.updated_at` only.
- **Charts:** ECharts 5.5.0; card types `scorecard, bar, line, pie, table, audit, list`.
- **"202401" bug:** `fmtDateLabel()` and `_fmtYYYYMMDD()` only match 8-char
  `YYYYMMDD`, so GA4's 6-char `yearMonth` falls through unformatted. The two
  implementations are duplicated and have diverged.
- **Compare:** none in dashboards. `app/connectors/ga4.py` has an unused
  `compare_date_ranges()` method, not wired to cards.

---

## Phase 1 — Foundations: date formatting + restyle

### Shared date-label formatter

Create a single shared JS helper (`app/templates/partials/fmt.html`, included by both
the public and live templates) exposing `formatDateLabel(value)`. Both
`card_charts.html` and `card_renderer_js.html` call it — eliminating the duplicated,
divergent formatters that caused the "202401" bug.

Recognized input → output:

| Input | Output |
|---|---|
| `YYYYMM` (e.g. `202401`) | `Jan 2024` |
| `YYYY-MM` | `Jan 2024` |
| `YYYYMMDD` (e.g. `20240105`) | `Jan 5, 2024` |
| `YYYY-MM-DD` | `Jan 5, 2024` |
| `YYYYQn` / `YYYY-Qn` | `Q1 2024` |
| ISO week `YYYYWnn` | `Wk 03 '24` |
| `YYYY` | `2024` |
| anything else | unchanged (pass-through) |

Applied to chart x-axis labels, table cells in date/month columns, scorecard
sparkline tooltips, and chart tooltips. Pure function — unit-testable in isolation
(extract to a tiny standalone JS module or test via a thin harness; if testing JS is
impractical in this stack, mirror the logic in a Python helper used for PDF/export
rendering and test that).

### "Looker Clean" restyle

A single source of truth for the data-viz theme (CSS variables + an ECharts theme
object), applied across `card_charts.html`, `card_renderer_js.html`, and template CSS:

- **Palette (categorical, consistent across all cards):** `#4285F4`, `#34A853`,
  `#FBBC04`, `#EA4335`, `#A142F4`, `#24C1E0`, `#FF6D01`. Category → color is stable
  within a dashboard so the same series is the same color everywhere.
- **Semantic deltas:** up `#188038` (green), down `#D93025` (red), neutral `#5F6368`.
- **Chart treatment:** horizontal-only hairline gridlines (`#ECEFF1`), no chart
  border, small top-left legend, 2px rounded bar tops, area fills at ~10% opacity,
  Google Sans/Roboto/system fallback.
- **Number formatting:** compact notation (48.2K, 1.3M, 2.1B) with unit-aware
  rendering (number/currency/percent) honoring each card's `chart_config.unit`.
- **Cards:** flat white surface, 1px `#E0E0E0` border, subtle shadow, generous
  padding.

No data-model changes in this phase. Lowest risk, immediate visible win.

---

## Phase 2 — Filter revamp

### Data model: dashboard-level filter declarations

Add a `filters` JSONB column to the dashboard (new migration). Each entry declares one
filter widget:

```jsonc
{
  "key": "channel",
  "label": "Channel",
  "type": "multi_select",          // date_range | single_select | multi_select | search | number_range | toggle
  "options": {                      // for select/multi_select only
    "source": "static",             // static | warehouse
    "values": ["Organic", "Paid", "Direct"]
    // warehouse: {"source":"warehouse","card":"<card_key>","column":"channel"}
  },
  "default": [],                    // type-appropriate default
  "toggle": {                       // toggle type only — the override applied when ON
    "applies": {"new_vs_returning": "new"}
  },
  "ui": {"order": 2}
}
```

This separates **what controls to show** (dashboard-level `filters`) from **how each
card binds them** (per-card `filter_hooks`, unchanged). Cards that don't bind a key
ignore it.

**Backward compatibility:** on read, if `filters` is empty but cards carry
`filter_hooks`/`filter_options`, synthesize an equivalent `filters` array (date_range +
single_selects) so existing dashboards render identically.

### Type-aware translation layer (the core of point 4)

A value alone can't filter a query — each type must become real per-platform syntax.
New `app/dashboards/filter_translators.py` with one translator per platform, invoked
in the data path after `apply_overrides` resolves which cards consume which keys.

| Type | GA4 | Warehouse SQL | Marketing |
|---|---|---|---|
| `single_select` | `dimensionFilter` (exact match) | `= {ph}` | API param |
| `multi_select` | `inListFilter` | `IN ({ph_list})` | repeated param |
| `search` | `stringFilter` CONTAINS | `ILIKE %{ph}%` | unsupported → rejected at deploy |
| `number_range` | `metricFilter` (between) | `BETWEEN {lo} AND {hi}` | unsupported → rejected |
| `toggle` | predefined `dimensionFilter` | predefined fragment | predefined param |
| `date_range` | `date_ranges` | `{start}` / `{end}` placeholders | date params |

- `apply_overrides` is extended to carry the filter **type** alongside the value so the
  translator can emit the right structure. Multi-value (list) overrides are supported
  end-to-end.
- SQL list/`IN` substitution is done with proper parameterization/escaping — never raw
  string interpolation of user values.
- Translation is a set of pure functions (value + type + hook target → query
  fragment), heavily unit-tested per platform.

### Deploy-time validation (filters never silently no-op)

At deploy/update, validate each declared filter:

- The wired dimension/metric **exists** in the target GA4 property / warehouse table /
  marketing schema (probe the connector / `INFORMATION_SCHEMA`).
- The **type is supported** for that platform (e.g. `number_range`/`search` rejected
  for marketing connectors that can't express them).
- `options.source: warehouse` references a real card + column.

Invalid filters are rejected at deploy with a clear, actionable error rather than
silently dropped.

### UI: the six widget types

A new `partials/filter_bar.html` renders the dashboard's `filters` array. Per type:

- **date_range** — preset chips (7/30/90/YTD + any `filter_presets`) **plus a custom
  start/end calendar** (always present).
- **single_select** — dropdown with an "All" option (today's behavior, restyled).
- **multi_select** — dropdown with checkboxes; selected values shown as removable
  chips.
- **search** — debounced text input (contains-match).
- **number_range** — min/max numeric inputs.
- **toggle** — on/off switch applying its predefined override.
- Dynamic (`warehouse`) options are lazily fetched via the existing filter-options
  endpoint. A "Clear all" resets to defaults.

Client filter state is a single object serialized to the URL (see Phase 4) and sent to
the data endpoint on change (debounced), reusing the existing reload path.

### MCP surface

- **`dashboard_deploy_batch`** — extended to accept the dashboard-level `filters`
  array. When omitted, the tool **auto-suggests** filters by inspecting card
  dimensions/metrics (e.g. a `single_select` Country when cards group by country) and
  always includes the default date presets + a custom range. The Fluxito skill
  instructs the assistant to present the suggested filters and confirm/tweak with the
  user before deploying.
- **`dashboard_update`** (new) — patch an existing dashboard: add/remove/update
  filters, update cards, change `cache_ttl_seconds` — without a full redeploy. Reuses
  the same validation as deploy. Directly serves "add the filters accordingly."

---

## Phase 3 — Compare mode

### Selection

Compare toggle in the filter bar. When on: presets **Previous period** /
**Previous year** plus a **custom second-range calendar**. Comparison range is part of
the serialized filter state and the cache key.

### Mechanism

Execute each card **twice** (current + comparison range) and merge server-side. This is
platform-agnostic and works uniformly for GA4, warehouse, and marketing cards. (GA4's
native two-range `compare_date_ranges()` is a later optimization, not load-bearing.)

### Normalized compare-snap (computed in `app/dashboards/snapshot.py`)

The template renders only; all comparison math is server-side and pure-function tested:

- **scorecard metric** → `{current, previous, delta_abs, delta_pct}` → big value +
  green/red delta badge + "vs N last period".
- **time series (line)** → two series **aligned by relative index** (day 1 vs day 1);
  comparison drawn as a faded dashed line; tooltip shows both real dates.
- **table** → rows matched by dimension value → `current · previous · Δ%` columns;
  rows present in only one period show "—" on the missing side.
- **bar** → grouped bars (current solid + previous light) per category.
- **pie / list / audit** → comparison not rendered as overlay; scorecard-style totals
  may show a delta where meaningful, otherwise compare is a no-op for these types
  (stated explicitly so behavior is predictable).

### Biggest-movers callouts

When compare is on, compute the top movers by absolute Δ% across scorecard metrics
(and notable table rows) and render a one-line insight banner: *"Direct ▲63%,
Paid ▼8% vs last period."* Computed from the compare-snap; no extra queries.

### Cross-filtering

Click a bar or table row to set the corresponding dimension's filter to that value and
reload — only for dimensions that have a declared filter. ECharts click events and
table row clicks write into the shared filter state. Visually indicates the active
drill (and respects "Clear all").

---

## Phase 4 — Caching, freshness & shareable URLs

### 24h cache with per-dashboard override

- New `cache_ttl_seconds` column on the dashboard (default `86400`) + migration. The
  data path uses it instead of the hardcoded 1h.
- **Each unique (slug, card-content-hash, filter combo, compare state, version)**
  caches independently. First access of a combo queries live and caches for the TTL.

### Staleness-bug fix

The current key folds in only `dashboard.updated_at`. New key folds in a **content
hash of card specs** + **every filter value + compare state**, so a card refresh or
filter change correctly busts the cache.

### Freshness banner (visible on every dashboard)

> 🕓 Cached data from Jun 13, 2026 · 9:14 AM — refreshes daily · **Refresh now**

Built from the existing `generated_at` in the response; on a cache hit it reflects the
original fetch time (honest age). **Refresh now** (owners only) bypasses + re-caches.

### Unified public/live caching

Public dashboards currently serve a frozen DB snapshot with no banner. New behavior:
first public access hydrates-and-caches for the TTL (scope-respecting) and shows the
same banner — so caching is real and visible on both paths. Public refresh is not
owner-gated the same way; public users see cached data only (no manual refresh), but
the banner communicates age.

### Shareable filtered-view URLs

The full filter state — date range, compare ranges, every dimension filter, toggles —
is serialized into URL query params and restored on load. PDF/Share captures exactly
what's on screen. Builds on the existing `?start`/`?end` params.

---

## Baked-in polish (serves point 4, all phases)

- **Per-card CSV export** for tables.
- **Loading skeletons + per-card error/retry** states instead of silent blanks; a
  failed card shows what failed and why, with a retry.
- **Deploy-time filter validation** against the live data source (described in Phase 2).

---

## Data flow (end to end)

```
Filter UI (filter_bar.html)
  → serialized filter state (URL + request)
  → data endpoint (dashboard_routes / dashboard_query_routes)
      → cache lookup  [key: slug + card-hash + filters + compare + version, TTL=cache_ttl_seconds]
      → on miss: for each card
          → apply_overrides(spec, filters)            # resolves hooks, carries type
          → filter_translators.translate(...)         # per-platform query syntax
          → execute via MCP tool registry (×2 if compare)
          → normalize_snap(...) / compare-snap merge  # readable dates, deltas, series
      → cache set, generated_at stamped
  → template render (Looker Clean theme, shared formatDateLabel, compare rendering,
    biggest-movers banner, freshness banner, cross-filter handlers)
```

## Testing strategy

- **Date formatter** — table-driven tests over every input format (the regression that
  caused "202401").
- **Filter translators** — per type × per platform, including multi-value, escaping,
  and unsupported-combo rejection.
- **`apply_overrides`** — type-carrying overrides, list values, dot-path/array targets.
- **Compare merge** — delta math, relative-index alignment, table row-matching with
  asymmetric rows.
- **Cache key** — busts on card change / filter change / compare change; stable
  otherwise.
- **Deploy validation** — rejects nonexistent dimensions and unsupported type/platform
  combos.
- **MCP tools** — `dashboard_deploy_batch` schema extension + `dashboard_update`
  patch semantics.

## Risks & mitigations

- **Translation correctness across platforms** is the riskiest area → isolate in pure
  functions with exhaustive tests; reject (don't silently drop) anything unsupported.
- **Doubled upstream calls in compare** → mitigated by the 24h cache (each compare
  combo cached); GA4 native two-range call available later if quota pressure appears.
- **Public caching + quotas** → first public hit triggers a live query; rate-limit and
  cache per combo; consider a deploy-time warm-up of the default view.
- **Backward compatibility** → synthesize `filters` from legacy hooks on read; migrations
  default-safe (`filters '[]'`, `cache_ttl_seconds 86400`).

## Open questions (deferred to planning)

- Exact per-marketing-connector filter capabilities (which support `search`/`number_range`).
- Whether the JS date formatter is unit-tested directly or mirrored in Python for PDF
  rendering parity.
- Whether public "Refresh now" should ever be allowed (default: no).
