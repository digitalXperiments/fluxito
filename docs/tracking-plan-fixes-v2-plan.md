# Tracking Plan Fixes v2 — Synthesis Report

> Produced by a 12-agent Opus workflow (map 5 issues in code + deep-research Segment/Amplitude/RudderStack + synthesize). Decision-ready, file-level.

## 1. Executive summary

Five-issue remediation against the relational tracking-plan feature (`app/models/tracking_plan.py`, `app/services/tracking_plan/*`, `app/static/js/tracking_plan/*`, `app/static/css/tracking_plan.css`). Two issues (2 and 5) are predominantly CSS/JS layout bugs that ship fast (S–M, no migration) and unblock the visibly "broken" screens. Issue 3 (vendor lists) is medium effort, high leverage — three canonical vendor registries already exist server-side and just need unioning + exposing (no migration if we reuse `TPSource.connector_ref` JSONB + `TPDestination.platform`). Issue 1 (property nesting/data-types) is the heaviest structural work, needs ≥1 migration; a "true reference" member model is XL. Issue 4 (Metrics) is a product decision — recommendation is **reposition + simplify**, not cut.

---

## 2. Per-issue fix plan

### Issue 1 — Property nesting & data types ("everything is a property")

**Root cause** — three structural mismatches vs the reference model:
- `is_list` (bool, migration 060) and `data_type='array'` (CHECK `tracking_plan.py:224`, migration `056:197`) are two overlapping ways to express list-ness, and **neither stores the element type** — `typeBadge` just appends `[]` (`util/format.js:10-12`).
- The serializer never emits a members tree: `_property_dict` (`serializer.py:116-127`) emits `parent_property_id` as a bare id; `plan_to_dict` (`serializer.py:211-216`) buckets members alongside top-level props; `_event_dict` (`serializer.py:146-157`) emits only `name/data_type/is_list`, so event-attached object properties never surface members.
- Members ARE real `TPProperty` rows (good) but the UI is create-only, one level deep, leaf-only (`properties.js:351-404`), and uniqueness is the flat `(branch_id, kind, name)` so two objects can't each own a child named `id`.

**Target data model (industry-standard).** The decisive rule of shared-pool tracking-plan tools: all properties are shared globally throughout the tracking plan, including the sub-properties of object properties.

```
property = {
  name,
  data_type   ∈ { string, integer, float, boolean, object },   # 5 BASE types — NO 'array'
  is_list     : bool,                                           # orthogonal modifier
  constraints : { allowed_values | regex | format (string),
                  min | max (integer/float),
                  members: [member_ref] (object) },
}
member_ref = { member_property_id, required, sort_order }       # object keys are REFERENCES to global props
```

- **Collapse array/is_list ambiguity:** `data_type` = exactly 5 base types; **drop `'array'`**. "List of X" = `data_type=X, is_list=true`. "List of objects" = `data_type=object, is_list=true`. Removes the need for any separate `element_type` column. *(This corrects the code finding's `element_type` proposal, which assumed `array` stays a type — research says it shouldn't.)*
- **`int → integer`** to match the user-facing label and the reference model's enum.
- **Object members = references to global props.** Two options:
  - **(A) Composition (current self-FK, zero new table):** member is its own `TPProperty` row w/ `parent_property_id`; "reference existing" clones fields (copy semantics). Simplest, NOT the reference model's shared-pool — edits don't propagate.
  - **(B) True reference (best-in-class):** add `tp_property_members(parent_property_id, member_property_id, sort_order, required)` link table — shared library props, no duplication, recursion via object-typed members. Edit-once-reflected-everywhere.
- **Uniqueness (option B):** partial unique index `(branch_id, kind, name) WHERE parent_property_id IS NULL`; member names scoped by the link table.

**Files:** `models/tracking_plan.py`, new migration(s), `services/tracking_plan/properties.py`, `serializer.py`, `views/properties.js`, `util/format.js`, `util/constraints.js`, `tools/specs/data/tracking_plan.json:439-490`.
**Migration:** Yes (1 type-collapse; +1 for option B). **Effort:** L (type-collapse + recursive serializer + recursive UI), option A = L incremental, option B = XL. **Phase:** ship type-collapse + recursive serializer/UI first, then decide A vs B.

### Issue 2 — Event editor property-selection UI

**Root cause (the clipped faint-green row).** Not a data bug. `.tp-card` has `overflow:hidden` (`tracking_plan.css:384-386`); the add-property combo is the **last** child of `.tp-card-b` (`events.js:465`) and its absolutely-positioned popup `.tp-combo-pop { top:100% }` (`tracking_plan.css:537-552`) extends below the card and is clipped. The sliver is the indigo (`--tp-accent:#4f46e5`, reads green-ish) `Create "…"` row. On ≤768px `.tp-card-b` also gets `overflow-x:auto` (`:1017-1018`) which re-clips it.

**Fix:** (1) un-clip — drop `overflow:hidden` on `.tp-card`, round corners on inner elements [S]; (2) scope mobile `overflow-x:auto` to a `.tp-table-scroll` wrapper [M]; (3) combo UX — open-on-focus, keyboard nav, "Already added" disabled row [M]; (4) inline type picker for new props (don't hard-default to string, `events.js:542-550`) [M]; (5) drag feedback class `tp-prow` [S]; (6) commit-collision guard across all kinds [S].
**Files:** `tracking_plan.css`, `views/events.js`. **Migration:** No. **Effort:** S for the un-clip (fixes screenshot); M overall.

### Issue 3 — Sources & Destinations vendor list

**Root cause.** `TPDestination.platform` is free-text Text NOT NULL, no enum (`tracking_plan.py:284`), rendered as a free-text `<input>` (`sources.js:268-270`) — "GA4" and "Moengage" both typed `ga4`. `TPSource.platform_type` is a hardcoded 5-item array (`sources.js:26`) but that's the source *kind*, not the vendor.

**Existing connector/vendor registries — EXACT locations (source from these to stay aligned with the audit):**
- **`app/connectors/rate_limits.py:80-83`** — `CATALOG` frozen `Connector` dataclasses (key/name/category): ga4, gtm, bigquery, google_ads, search_console, meta_ads, tiktok_ads, snap_ads, linkedin_ads, pinterest_ads, x_ads, reddit_ads, apple_ads, bing_webmaster, adobe_analytics, adobe_launch, marketo, amplitude, redshift, snowflake.
- **`app/api/google_oauth_routes.py:72-98`** — `GRANULAR_CONNECTOR_CATALOG` (key,label,has_flags) + `TOTAL_CONNECTOR_COUNT`.
- **`app/tag_testing/rule_books/manifest.py:42-87`** — `RULE_BOOK_MANIFEST` + `PLATFORM_INDEX` + `list_platforms_summary()` — **the audit/tag-testing platforms** (segment, mixpanel, hotjar, criteo, meta_pixel, ga4_*). This is "the platform list used by the audit" the user referenced.

**Sourcing:** new `app/services/tracking_plan/vendors.py` exposing `TP_VENDOR_CATALOG` — `{slug, display_name, category, source}` unioning the three, de-duped/normalized (`ga4_ecom→ga4`, `meta_pixel→meta`), plus a **curated tail** for common destinations in none of them (the user's **Moengage** is in none): moengage, braze, klaviyo, customerio, rudderstack, posthog, heap, iterable, onesignal.
**Axes (research):** source = grouped **platform** enum (web/iOS/Android/server/warehouse/GTM/custom), NOT vendor; destination = grouped **vendor** catalog. All of Segment/RudderStack ship a **"Custom/Other"** escape hatch.

**Fix:** (1) `vendors.py` [M]; (2) `GET …/tracking-plan/vendors` + `api.vendors()` + cache in `state.js` [S]; (3) `destCard()` category-grouped `<select>` + "Custom…" reveal [M]; (4) server normalize/validate in `routing.create_destination/update_destination` [S].
**Persistence (avoid migration):** option A — `TPDestination.platform` stores the slug (exists); sources store `{vendor_slug, custom}` in the unused `TPSource.connector_ref` JSONB.
**Files:** new `vendors.py`, `tracking_plan_routes.py`, `api.js`, `state.js`, `views/sources.js`, `routing.py` (registry files read-only). **Migration:** No (option A). **Effort:** M.

### Issue 4 — Metrics tab decision

**Recommendation: REPOSITION + SIMPLIFY. Do not cut, do not build a metrics engine.**

The leading tool is the only best-in-class tool with a first-class structured Metrics object; others treat metrics as planning guidance (industry tracking-plan references). Metrics earn their place only via **(a) change-impact** ("editing this event affects Metric X") and **(b) completeness** ("metric references an uninstrumented event/prop"). Even leading tools do NOT compute values in-plan. Fluxito's current tab is the documented "noise" failure mode: a measurement-*shaped* schema with no measurement, a cosmetic one-directional `dashboard_card_id` that only silences a self-invented `metric_not_measured` warning (`validation.py:140-151`), and a spec/model enum mismatch (`tracking_plan.json:1093-1099` vs model CHECK `tracking_plan.py:38,380`).

- **Reposition:** remove the standalone tab; render a "Success metrics / Measured by" panel **inside the event detail** (metrics carry `event_id`). Mirror the reference model's bidirectional optional link.
- **Simplify:** keep `name + description + event link`; **drop measurement-shaped `property/filters` + cosmetic `dashboard_card_id` + `metric_not_measured` warning** (or keep `dashboard_card_id` as informational deep-link, not a gate).
- **Do NOT build warehouse computation** (no peer does this in-plan).

**Files:** `views/events.js`, `shell.js:23,36,44`, `tracking_plan.html:23,27`, `views/metrics.js`, `metrics.py`, `validation.py:140-151`, `serializer.py:240-252`, `exports.py:69-74,125-128`, `tools/specs/data/tracking_plan.json:1080-1131`, + migration if columns dropped.
**Migration:** Yes if columns dropped (recommended). **Effort:** M. (Needs user sign-off.)

### Issue 5 — Issues / Rules tab UI

**Root cause (overlap).** Pure flexbox failure. `.tp-rule-row` is single-line flex (`align-items:center; gap:14px`, `tracking_plan.css:1142`) with **no `flex-wrap` until ≤768px**. `.tp-rule-config` has no flex/min-width guard (`:1151-1153`) and holds nowrap labels + fixed-width controls; `.tp-rule-sev-sel` is `margin-left:auto; flex:none`. Compounding: `required_property` produces a **nested `.tp-rule-config` inside `.tp-rule-config`** (`issues.js:443,465`); `event_requires_owner` mixes raw `.tp-checkline` checkboxes into the centered row; `pii_must_be_flagged` carries editable `patterns` (`rules.py:67`) with no UI; "Applies to = category" has no category picker (`scope_category_id` never settable, `update_rule` `rules.py:169` takes only config/severity).

**Fix:** (1) CSS: `.tp-rule-row { flex-wrap:wrap }` at all widths; `.tp-rule-meta { flex:1 1 200px; min-width:0 }`; `.tp-rule-config { flex:0 1 auto; min-width:0 }` [S — fixes screenshot]; (2) flatten nested config, lighter `.tp-rule-field` span [S]; (3) card restructure + per-rule help text [M]; (4) PII patterns editor + category `<select>` + thread `scope_category_id` through `update_rule`/route/`api.js` (column exists, no migration) [M].
**Optional cross-cutting:** shared `.tp-form-row` primitive to retrofit `.tp-rule-row/.tp-filter-row/.tp-destrow/.tp-catalog-row` [L — defer].
**Files:** `tracking_plan.css`, `views/issues.js`, `rules.py`, `tracking_plan_routes.py`, `api.js`. **Migration:** No. **Effort:** S overlap; M full.

---

## 3. What's missing (vs best-in-class) — prioritized

| Gap | Priority | Rationale | Scope |
|---|---|---|---|
| Non-Event call types + traits (Identify/User, Group, Page, Screen, Revenue) | HIGH | Today only "track" events; every peer models identify/group/page | Future |
| Observed-vs-planned drift ("Inspector") | HIGH | Biggest category differentiator; Issues/Rules is a static linter, not a runtime conformance engine | Future |
| Per-attachment scoping of enums/presence + "sometimes sent" | HIGH | Same property behaves differently per event/source; constraints are global today | Future |
| Event Triggers ("when does this fire", text+screenshot) | HIGH | the reference model's defining handoff feature; absent | Future |
| Per-platform code generation (typed SDK snippets) | HIGH | per-platform codegen / Segment Typewriter analog | Future |
| Structured naming-convention rule run in the linter | MED | the reference model only checks at input time; Fluxito can lint it | **In-scope-now (extends Issue 5)** |
| PII/sensitive-data rules (flag + handling metadata) | MED | Genuine gap no incumbent owns in-plan; `is_pii` + `pii_must_be_flagged` exist | **In-scope-now (extends Issue 5)** |
| Severity gate (warn vs error/block at review/merge) | MED | the reference model's "0-issue policy"; severity column exists | In-scope-now (small) |
| Object members as shared global pool (Issue 1 option B) | MED | the shared-pool ideal; option A is a stopgap | In-scope if option B chosen |
| Standards exports (JSON Schema, Snowplow Iglu, vendor-native) | MED | Interop/CI | Future |
| CLI + CI gate (plan-as-code, fail PR on drift) | MED | the reference model/RudderStack/mParticle pattern | Future |
| Per-event lifecycle status (Draft→Verified→Deprecated→Blocked) | MED | Amplitude Data; layers on Versions | Future |
| Destination name mapping/aliasing | MED | Extension of routing once Issue 3 lands | Future |
| Owner/Stakeholder model + notifications | MED | Extends Comments | Future |
| Auto-fix for casing/separator rules | MED | the reference model one-click bulk fix | Future |
| Localization, data-flow diagrams | LOW | Not table stakes anywhere surveyed | Out |

---

## 4. Recommended sequencing

- **Wave 1 (parallel, no migration):** A. Issue 5 CSS overlap + nesting flatten (S) — `tracking_plan.css`, `issues.js`. B. Issue 2 un-clip + mobile overflow + drag feedback (S–M) — `tracking_plan.css`, `events.js`. Share only `tracking_plan.css` (different selector blocks — coordinate merge order).
- **Wave 2 (parallel w/ Wave 1, no migration w/ option A):** C. Issue 3 vendor list — new `vendors.py`, GET endpoint, `sources.js`, `routing.py`, `api.js`, `state.js`.
- **Wave 3 (sequential, migration-bearing):** D. Issue 1 — type-collapse migration → serializer + service + UI (decide A vs B first). E. Issue 4 Metrics — needs sign-off; migration ordered relative to D to avoid two heads.
- **Wave 4 (after Wave 1):** Issue-5 completion — PII patterns editor, category-scope picker + `scope_category_id` threading, structured naming rule in linter. M, no migration.

**Migration/ordering risks:** confirm the single true alembic head before authoring (SDR reconciled to 061; migrations through 063); serialize the two migration-bearing waves into one linear chain; `tox` green is a HARD GATE before push (run `ruff format` on every touched file).

---

## 5. Open questions (decided at the gate)

1. **Metrics** — confirm reposition+simplify + drop `property_id/filters/dashboard_card_id`; is there existing metric data to preserve?
2. **Issue 1 reference model** — A (copy/composition, no new table) vs B (true shared-pool `tp_property_members`, industry-standard, XL)?
3. **Issue 1 migration** — greenfield drop/rewrite of `array`-typed/nested rows safe (prod had no SDR data at PR #14)?
4. **Issue 3** — approve union+curated tail; block-on-unknown vs warn-only; persistence option A vs explicit columns?
5. **Issue 3 sources** — vendor binding on sources, or platform-kind select + optional connector_ref only?
6. **Scope** — confirm only Issues 1–5 (+ in-scope-now Issue-5 extensions) ship now; HIGH "missing" items deferred.
