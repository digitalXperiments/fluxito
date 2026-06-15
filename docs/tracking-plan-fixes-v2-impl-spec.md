# Tracking Plan Fixes v2 — Implementation Contract Spec

> The binding contract for all implementation agents. Decisions locked: Issue 1 = **Option B (true shared references, `tp_property_members` link table)**; Issue 4 = **reposition + simplify (drop metric measurement columns)**; Issue 3 = **Option A persistence + warn-only validation (no migration)**; Scope = **Issues 1–5 + in-scope Issue-5 extensions** (PII patterns editor, category scope, structured naming rule, severity gate).
>
> Migration head is **`063_tp_metric_dashboard_link`** (single linear head). All schema changes go in **one** new migration `064` to keep a single head.
>
> HARD GATE: `tox` (lint + typecheck + test) must be green before any push. Run `ruff format` on every touched `.py`. Don't push red.

---

## A. Schema (migration `064` + `app/models/tracking_plan.py` + `app/tools/specs/data/tracking_plan.json`)

### A.1 Property data types — collapse `array`, rename `int`
- New `TPProperty.data_type` CHECK: `data_type IN ('string', 'integer', 'float', 'boolean', 'object')`. **`'array'` is removed; `'int'` becomes `'integer'`.**
- `is_list` (bool) stays and becomes the *only* list modifier. "List of X" = `data_type=X, is_list=true`. "List of objects" = `data_type='object', is_list=true`. **No `element_type` column** — the element type *is* `data_type`.
- Migration data rewrite (in `064` upgrade, before swapping the CHECK):
  - `UPDATE tp_properties SET data_type='integer' WHERE data_type='int';`
  - `UPDATE tp_properties SET data_type='object', is_list=true WHERE data_type='array' AND id IN (SELECT parent_property_id FROM <backfilled members>);` (array-of-object)
  - `UPDATE tp_properties SET data_type='string', is_list=true WHERE data_type='array';` (remaining bare arrays → list of string)
  - Drop old `ck_tp_property_data_type`, add the new 5-type CHECK.

### A.2 `tp_property_members` link table (Option B — shared references)
```python
class TPPropertyMember(Base):
    __tablename__ = "tp_property_members"
    id            : UUID PK
    parent_property_id : FK tp_properties(id) ON DELETE CASCADE  # the object (or list-of-object) prop
    member_property_id : FK tp_properties(id) ON DELETE CASCADE  # a SHARED global prop referenced as a key
    required      : bool  NOT NULL server_default false
    sort_order    : int   NOT NULL server_default 0
    __table_args__ = (UniqueConstraint("parent_property_id", "member_property_id",
                                       name="uq_tp_property_member"),)
```
- **Backfill from the old self-FK** (in `064`, before dropping the column): for every `tp_properties` row with `parent_property_id IS NOT NULL`, insert `tp_property_members(parent_property_id=<that parent>, member_property_id=<self id>, sort_order=0, required=false)`.
- **Then drop `TPProperty.parent_property_id`** (downgrade re-adds it and rebuilds it from the link table). All nesting now flows through the link table.

### A.3 Uniqueness stays flat (this is correct for Option B)
- Keep `UniqueConstraint(branch_id, kind, name)`. In the shared pool there is exactly **one** global `id` property per kind, referenced by many objects — that's the shared-pool model. **Do not** add a partial index. Two objects sharing a member named `id` reference the *same* global property.

### A.4 Metrics — drop measurement columns (Issue 4)
- Drop from `tp_metrics`: `type`, `property_id`, `dashboard_card_id`, `filters`. Drop `ck_tp_metric_type`. **Keep:** `id, plan_id, branch_id, name, description, event_id, created_at` and `uq_tp_metric_name`.
- Downgrade re-adds the columns (nullable / server_default 'count' for type) + the CHECK.

### A.5 Spec JSON (`app/tools/specs/data/tracking_plan.json`)
- Property `data_type` enum → `string/integer/float/boolean/object`; document `is_list` + object `members` (array of `{member_property_id|name, required, sort_order}`). Remove `array` and any `element_type` mention.
- Metric schema: keep `name/description/event_id`; remove `type/property/filters/dashboard_card_id` and the count/sum/etc. enum.

### A.6 Model readers the schema change implies (handled in Phase B, do NOT edit here)
`services/properties.py`, `services/serializer.py`, `services/branches.py`, `services/metrics.py`, `services/validation.py`, `tools/tracking_plan_tools.py`, frontend. Phase A only touches the model, migration, and spec JSON.

---

## B. Backend (depends on A; agents own disjoint files, run in parallel)

### B1 — `services/properties.py` + `services/serializer.py`
**properties.py:**
- Remove `parent_property_id` from `_PROP_FIELDS` and from `create_property`/`update_property` signatures. Nesting no longer rides on the property row.
- Add member ops backed by the link table (used by the action route + UI): `add_member(parent_id, member_property_id, required, sort_order)`, `remove_member(parent_id, member_property_id)`, `reorder_members(parent_id, [ids])`, plus a convenience that **creates a new property and links it** (for the "create new member" path). Reuse existing `create_property`.
- `_validate_property_shape`: members only allowed on `data_type='object'` (objects, incl. `is_list` lists-of-object). Forbid members on scalar props. Guard a max nesting depth (e.g. 6) to stop cycles — a property may not be a (transitive) member of itself.
- `update_property`: when `data_type` changes object→scalar, reject if the property has members (or clearly detach in the same txn) — don't silently orphan.

**serializer.py:**
- Build a `parent_property_id → [TPPropertyMember]` index once from `tp_property_members` (load it in the same query batch as properties).
- `_property_dict`: drop `parent_property_id`; add `members: [ { member_property_id, name, data_type, is_list, required, sort_order, members: [...recursive...] } ]` when the prop is an object. Recurse via the index; **guard depth** to avoid infinite loops on shared cycles (cap depth, dedupe visited per branch).
- `plan_to_dict`: top-level library buckets (`properties.event/user/group/system`) = **all** properties of that kind (the shared pool — members still appear in the library, that's correct by design). The members tree is an *additional* nested view on object props, not a removal from the flat list.
- `_event_dict` attached properties: include the attached library prop's `members` tree so events surface nested structure.
- **Metric serialization (Issue 4):** `_metric_dict` emits only `{id, name, description, event_id, event_name?}`. Remove `type/property/filters/dashboard_card_id`.

### B2 — `services/branches.py` (branch fork)
- The two-pass `parent_property_id` copy (lines ~97–143) must be replaced: pass 1 copies all properties (no self-FK), pass 2 copies `tp_property_members` rows remapping `parent_property_id`/`member_property_id` through `prop_map`. Update `_VOLATILE_KEYS` (drop `parent_property_id`). Ensure metric copy no longer references dropped columns.

### B3 — `services/metrics.py` + `services/exports.py`
- `metrics.py`: `_METRIC_FIELDS = {"name", "description", "event_id"}`. Remove `type/property_id/filters/dashboard_card_id` from create/update; drop the property-existence checks for `property_id`.
- `exports.py`: metric export emits only `name/description/event_id` (event name). Remove dropped fields from any markdown/JSON export (lines ~69-74, 125-128).

### B4 — `services/vendors.py` (NEW) + `services/routing.py` (Issue 3)
- New `vendors.py`: `TP_VENDOR_CATALOG: list[dict]` = `{slug, display_name, category, source}` built by **unioning three read-only registries**:
  - `app/connectors/rate_limits.py` → `CATALOG` (key/name/category)
  - `app/api/google_oauth_routes.py` → `GRANULAR_CONNECTOR_CATALOG`
  - `app/tag_testing/rule_books/manifest.py` → `RULE_BOOK_MANIFEST` / `list_platforms_summary()` (the **audit** platforms)
  - De-dupe on slug; normalize audit slugs to connector slugs (`ga4_ecom→ga4`, `meta_pixel→meta`). Add a **curated tail** (common destinations in none of the three): `moengage, braze, klaviyo, customerio, rudderstack, posthog, heap, iterable, onesignal`. Tag each entry's `source` as `connector|audit|curated`. Also expose grouped **source platform kinds** (`web/ios/android/server/warehouse/gtm/custom`) for the source axis. Provide `get_vendor_catalog()` returning `{destinations:[...], source_platforms:[...]}`. **Do NOT edit the three registry files.**
- `routing.py`: in `create_destination`/`update_destination`, normalize `platform` (trim+lowercase to a slug). **Warn-only**: accept any slug; if not in the catalog, still persist it (custom). Optionally stamp `config={"custom": true}` when off-catalog. For sources, persist `{vendor_slug, custom}` into the existing **`TPSource.connector_ref` JSONB** (no migration). Keep `platform_type` as the source *kind*.

### B5 — `services/rules.py` + `services/validation.py` (Issue 5 + extensions)
- `rules.py`: thread `scope_category_id` through `update_rule` (currently only config/severity). Surface the editable `pii_patterns` config for `pii_must_be_flagged`. Add a **structured naming rule** definition (`event_name_components`: ordered components / allowed separators / casing) to the rule registry, configurable via `config`. Ensure `severity` (warn/error/info) is settable per rule (gate).
- `validation.py`: **remove the `metric_not_measured` rule** (lines ~140-151) — metrics are no longer "measured" entities. Implement execution for the new structured naming rule. Respect `scope_category_id` (apply a rule only to events in that category) and `severity`.

### B6 — `api/tracking_plan_routes.py` (Issues 3 + 5)
- Add `GET /api/projects/{id}/tracking-plan/vendors` → `vendors.get_vendor_catalog()` (cacheable). 
- Extend the rules action(s) so `update_rule` accepts `scope_category_id` + `pii_patterns` + naming-rule config. Add member actions if the action dispatcher needs explicit cases (`add_member/remove_member/reorder_members`) — wire to `properties.py`.

### B7 — `tools/tracking_plan_tools.py` (MCP layer)
- Remove `parent_property_id` from the property create/copy paths (lines ~234, ~1191); switch nested-property creation to the member link (create prop, then `add_member`). Remove dropped metric fields from the metric tool create/update + any plan import/export shape. Keep MCP behavior otherwise identical.

---

## C. Frontend (depends on B contracts; agents own disjoint files, run in parallel)

### Shared CSS class names (owned by C6; other agents just use them)
- `.tp-combo-pop` must escape the card (no clipping). Object member sub-tables: `.tp-members` / `.tp-member-row` (indented per depth via a `--depth` var or nested padding). Rule rows wrap: `.tp-rule-row { flex-wrap:wrap }`, `.tp-rule-controls` wrapping cluster, `.tp-rule-field` light span. Mobile table scroll wrapper: `.tp-table-scroll`.

### C1 — `views/properties.js` + `util/format.js` + `util/constraints.js` (Issue 1 UI)
- `DATA_TYPES = ['string','integer','float','boolean','object']` (drop `array`/`int`); a separate **"List"** toggle drives `is_list`. `typeBadge` (`format.js`) renders `data_type` + `[]` when `is_list`.
- Object props show a **recursive** members tree (a member that is itself an object expands its own members). Each member row reads from the serialized `members[]` tree.
- "Add member" is an **industry-standard combo**: (1) pick an existing library property → `add_member`; (2) create a new property inline (name + type + list toggle) → create + `add_member`. No more create-only `parent_property_id`.
- Library list (`properties.js:122`) **stops filtering out members** — the shared pool shows all props (members included). Remove the `parent_property_id` filter.
- All member mutations go through the new actions (`add_member/remove_member/reorder_members`) via `api.js` doAction.

### C2 — `views/events.js` (Issue 2 combo + Issue 4 metrics panel)
- **Combo (Issue 2):** open-on-focus (browse full `available()` library), keyboard nav (↑/↓/Enter/Esc), disabled "Already added" row on attached-name match, inline **type picker** for newly-created props (don't hard-default to string — store `data_type/is_list` on the `__new` draft so `commitEvent` creates with the chosen type), and re-check `propByName` across **all kinds** before `create_property` to avoid 409s. Add class `tp-prow` to the property `<tr>` for drag feedback.
- **Metrics panel (Issue 4):** render a "Success metrics" panel inside the event detail listing/creating metrics linked to this event (`name + description`, create/edit/delete via doAction `create_metric/update_metric/delete_metric`). This replaces the standalone tab.

### C3 — `views/sources.js` + `api.js` + `state.js` (Issue 3 UI)
- `api.js`: add `vendors()` (GET the catalog). `state.js`: fetch-once cache of the catalog on load.
- `sources.js`: replace the destination free-text `<input>` (`:268-270`) with a **category-grouped `<select>`** of `catalog.destinations` + a trailing **"Custom…"** option that reveals a text input; persist the slug. Keep the source `platform_type` kind select but source it from `catalog.source_platforms`; optionally bind a vendor via `connector_ref`. Update `addDest` default to unset (no hard `ga4`).

### C4 — `views/issues.js` (Issue 5 UI + extensions)
- Use the wrapping rule-row markup (`.tp-rule-row` wrap, `.tp-rule-controls`, `.tp-rule-field`); **flatten** the nested `.tp-rule-config` bug (`:443,465`). Move the meta to its own line; add per-rule **help text** (extend `RULE_LABELS` to `{label, help}`).
- Add the missing editors: **PII `patterns`** tag editor (`pii_must_be_flagged`); **category `<select>`** for "Applies to = category" bound to `plan.categories` (threads `scope_category_id`); **structured naming rule** config UI (components/separators/casing); severity selector per rule.

### C5 — `views/metrics.js` + `shell.js` + `app/templates/tracking_plan.html` (Issue 4 reposition)
- Remove the **Metrics** nav item + route (`shell.js:23,36,44`, `tracking_plan.html:23,27`). Either delete `views/metrics.js` or reduce it to nothing referenced. Metrics now live only in the event detail panel (C2). Ensure no dangling imports/routes.

### C6 — `static/css/tracking_plan.css` (ALL css)
- Issue 2: remove `overflow:hidden` from `.tp-card` (round corners on inner header/last section instead); scope mobile `overflow-x:auto` to a `.tp-table-scroll` wrapper, not `.tp-card-b`; ensure `.tp-combo-pop` has high z-index and is not clipped.
- Issue 5: `.tp-rule-row { flex-wrap:wrap }` at all widths; `.tp-rule-meta { flex:1 1 200px; min-width:0 }`; `.tp-rule-config/.tp-rule-controls { flex:0 1 auto; min-width:0 }`; `.tp-rule-sev-sel` drops to next line on wrap; `.tp-rule-field` light span.
- Issue 1: `.tp-members/.tp-member-row` indentation styles for the recursive member tree.

---

## Verification (Phase D — me, Opus)
1. `alembic upgrade head` then `alembic downgrade -1` round-trips on the new `064`.
2. `tox -e lint,typecheck` then full `tox` (Postgres + Redis up). `ruff format` all touched `.py`.
3. Dogfood with the browse tool: items-as-object nesting (reference existing + create new member), event property combo (focus/keyboard/create-with-type), destination vendor dropdown + custom, Rules tab no overlap + category scope + PII patterns, Metrics gone from nav and present in event detail.
4. CHANGELOG `[Unreleased]` entry (per project policy) — done before any push, not now.
