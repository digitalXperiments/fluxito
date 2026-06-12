# SDR / Tracking Plan Revamp — Design

**Date:** 2026-06-12
**Branch:** `task/sdr-tracking-plan-revamp-f88283`
**Status:** Approved (design); Phase 1 ready for implementation planning

---

## 1. Problem & Goal

The current **SDR** (Solution Design Reference) treats a single `markdown_content`
blob as the source of truth. AI generates that markdown; `sdr_parser.py` (~1k lines)
re-parses it into projection tables (`sdr_events` / `sdr_parameters` /
`sdr_destinations`) on **every save**. Everything downstream (audit tools, live tag
testing, Excel export) already consumes those *structured* tables — so the structure
exists, but it sits behind a fragile "AI writes prose → regex parses prose" seam.

**Goal:** invert the architecture. A tight, well-defined **relational schema becomes
the sole source of truth**, modeled on [Avo.app](https://avo.app): events, a reusable
**property library**, user properties, first-class **sources** and **destinations**
with routing, and per-event **mapping rules**. AI **assists** humans in filling this
structure via MCP tools rather than emitting a markdown document. The team manages and
(later) comments on the plan through a structured UI. Markdown becomes a *generated
export artifact*, never an input.

**Guiding principle:** *Not AI-first — structure-first, AI-assisted.* The schema is
tight enough that the AI cannot produce vague junk: every write is validated against
typed entities and constraints, and nothing reaches downstream consumers until a human
publishes.

---

## 2. Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Source of truth & legacy markdown | Relational DB is the **sole** source of truth. Markdown + parser kept **only** as a generated export/import artifact. **Greenfield** — no production SDR data to migrate. |
| 2 | Property modeling | **Reusable, project-level property library (Avo-style).** Events attach to properties many-to-many with per-attachment overrides. Covers event *and* user properties. |
| 3 | Sources / destinations | **Full Avo topology.** First-class Sources + Destinations, source→destination routing, events scoped to sources, per (event × destination) mapping rules. |
| 4 | Change-control model | **Target = branch + review (full Avo).** Schema is branch-ready from day one. **Phase 1 builds the structured core + AI CRUD on a single `main` branch**; the branch *workflow* is deferred to Phase 2. |
| 5 | Schema extras (all included) | Event **categories & tags**; **rich property constraints** (enum/regex/min-max + object/array/nested types); event-based **metrics**; **per-source implementation status**. |
| 6 | Phase-1 UI depth | **Full structured editing** in the browser — same operations the AI performs via MCP. |
| 7 | Representation architecture | **Approach 1:** normalized branch-scoped live tables + JSONB published-version snapshots + (future) copy-on-write branching. One canonical `plan_to_dict` serializer feeds markdown export, Excel export, MCP reads, and version snapshots. |
| 8 | MCP tool shape | Keep a **single self-describing `tracking_plan` meta-tool** with an `action` param (not a `_read`/`_write` split), extending `app/tools/specs/data/tracking_plan.json`. |

---

## 3. Architecture Overview

```
                          ┌─────────────────────────────┐
                          │   tracking_plan_service      │  ← single owner of all
                          │  (CRUD · validate ·          │     reads/writes/validation/
                          │   plan_to_dict · publish)    │     serialization
                          └──────────────┬──────────────┘
                       ┌─────────────────┼──────────────────┐
                       │                 │                  │
              MCP `tracking_plan`   HTTP/UI endpoints   downstream readers
              (action dispatcher)   (full editing UI)   (audit, tag-testing)
                       │                 │                  │
                       └────────► normalized tp_* tables ◄──┘
                                  (branch-scoped, main)
                                          │
                                  publish → tp_versions (JSONB snapshot)
                                          │
                          plan_to_dict ──►├─► markdown export
                                          ├─► xlsx export
                                          └─► version snapshot / MCP read
```

**Keystone:** a single shared **service layer** owns all reads, mutations, validation,
serialization, and publish logic. The MCP tool and the HTTP/UI endpoints are thin
adapters over it, which guarantees the AI and the team's UI can never drift — same
CRUD, same validation, same serializer.

---

## 4. Data Model (relational schema)

Greenfield clean tables with a `tp_` prefix (tracking plan), cleanly separated from the
retired markdown-era `sdr_*` tables. **Every content row carries `branch_id`.** Phase 1
only ever uses the auto-created `main` branch, but the column exists so Phase 2
branching needs no migration. One plan per project (preserves today's one-SDR-per-project
constraint).

### 4.1 Backbone

- **`tp_plans`** — one per project.
  `id, project_id (UNIQUE), name, description, default_branch_id (FK), current_version_id (FK, null), intake_answers (JSONB, optional business context for AI), created_at/by, updated_at`.
  Replaces `sdrs`; **no `markdown_content`**.
- **`tp_branches`** —
  `id, plan_id (FK), name, is_main (bool), base_branch_id (FK, null), base_version_id (FK, null), status('active'|'merged'|'abandoned'), created_by, created_at, merged_at`.
  Phase 1: exactly one row per plan (`is_main=true`, name `main`).
- **`tp_versions`** — immutable published snapshots.
  `id, plan_id (FK), branch_id (FK), version_number (text), snapshot (JSONB — full plan_to_dict output), changelog, published_by (FK users), published_at`.

### 4.2 Events & organization

- **`tp_events`** —
  `id, plan_id, branch_id, name, display_name, description, category_id (FK→tp_categories, null), tags (text[]), trigger_type, trigger_config (JSONB), purpose, owner_business, owner_technical, consent_required (text[]), created_*, updated_*`.
  UNIQUE `(branch_id, name)`.
- **`tp_categories`** —
  `id, plan_id, branch_id, name, description, color`. UNIQUE `(branch_id, name)`.

### 4.3 Property library (reusable catalog)

- **`tp_properties`** —
  `id, plan_id, branch_id, name, kind('event'|'user'|'group'|'system'), data_type('string'|'int'|'float'|'boolean'|'object'|'array'), description, constraints (JSONB: allowed/enum values, regex, min/max, format), parent_property_id (self-FK → object/array member nesting), is_pii (bool), created_*`.
  *(Enum is a `constraints.allowed_values` list on a `string`/`int`, not a distinct `data_type`.)*
  UNIQUE `(branch_id, kind, name)`.
  *User properties are simply `kind='user'` rows — plan-level, not event-linked. Object/array members are child rows pointing at their parent via `parent_property_id` (truly relational nesting, not a JSON sub-schema blob).*
- **`tp_event_properties`** — many-to-many link with per-attachment overrides.
  `id, event_id (FK), property_id (FK), required (bool), example, override_description, sort_order`.
  UNIQUE `(event_id, property_id)`.
  *Rename/retype a property once → propagates everywhere; overrides stay local to the attachment.*

### 4.4 Sources, destinations, routing, mapping

- **`tp_sources`** —
  `id, plan_id, branch_id, name, platform_type('web'|'ios'|'android'|'server'|'gtm'|'ga4'|...), description, connector_ref (JSONB/text → binds to an app/connectors instance, e.g. a GA4 property)`.
  UNIQUE `(branch_id, name)`.
- **`tp_destinations`** —
  `id, plan_id, branch_id, name, platform('ga4'|'amplitude'|'mixpanel'|'google_ads'|'meta'|...), platform_account_id, config (JSONB)`.
  UNIQUE `(branch_id, name)`.
- **`tp_source_destinations`** — routing M2M (which source forwards to which destination).
  `id, source_id (FK), destination_id (FK)`. UNIQUE `(source_id, destination_id)`.
- **`tp_event_sources`** — event scoping M2M **+ per-source implementation status**.
  `id, event_id (FK), source_id (FK), implementation_status('planned'|'implemented'|'verified'|'deprecated')`.
  UNIQUE `(event_id, source_id)`.
- **`tp_event_destinations`** — per (event × destination) **mapping rule**.
  `id, event_id (FK), destination_id (FK), dest_event_name, property_mappings (JSONB: src prop → dest name/value transform), enabled (bool), notes`.
  UNIQUE `(event_id, destination_id)`.

### 4.5 Metrics

- **`tp_metrics`** —
  `id, plan_id, branch_id, name, description, type('count'|'sum'|'unique'|...), event_id (FK), property_id (FK, null — for sum/avg over a property), filters (JSONB)`.
  UNIQUE `(branch_id, name)`.

**13 tables total.** Delivers: shared property dictionary, source-scoped events with
per-source status, full source→destination routing with per-event mapping rules,
categories/tags, metrics, and branch+version readiness — all with real FKs and
constraints.

---

## 5. Service Layer

New package `app/services/tracking_plan/` (service + validation + serialization). It is
the **only** module that mutates `tp_*` tables. Responsibilities:

- **CRUD** for every entity: events, properties, event-property attachments,
  categories, sources, destinations, source-destination routing, event-source scoping,
  event-destination mappings, metrics.
- **Validation** at write time, against the typed schema + constraints (see §7).
- **`plan_to_dict(plan, branch)`** — the canonical serializer. Single source feeding:
  MCP reads, the UI, markdown export, Excel export, and version snapshots.
- **`validate(plan, branch)`** — completeness/consistency report (findings list).
- **`publish(plan, branch, user)`** — snapshot `main` → `tp_versions`, bump version,
  set `current_version_id`. **Admin-role enforced.**
- **`scan_source(source)`** — run a connector scan; return discovered events/properties
  as **proposals** (no writes).
- **`plan_to_markdown(plan_dict)` / `plan_to_xlsx(plan_dict)`** — generated artifacts.

Both the MCP tool and HTTP endpoints call this service. No business logic in adapters.

---

## 6. MCP Tool Surface

Keep the single self-describing **`tracking_plan`** meta-tool. Extend
`app/tools/specs/data/tracking_plan.json` so each action documents its params + return
shape for the LLM (leverages the existing self-describing spec engine).

**Reads**
- `get_plan` — full structured plan for `main`, with filter (category/source/destination/status) + summary modes for large plans.
- `get_event` — one event with attached properties, sources, destination mappings.
- `validate` — findings: missing required props, events with no source, events mapped to no destination, unused properties, enum/type violations.

**Writes (validated CRUD on the working draft / `main`)**
- Events: `create_event`, `update_event`, `delete_event`
- Properties: `create_property`, `update_property`, `delete_property`
- Event↔property: `attach_property`, `detach_property` (with overrides)
- Categories: `create_category`, `update_category`, `delete_category`
- Sources: `create_source`, `update_source`, `delete_source`
- Destinations: `create_destination`, `update_destination`, `delete_destination`
- Routing: `connect_source_destination`, `disconnect_source_destination`
- Event scoping/status: `set_event_sources` (attach event to sources + per-source status)
- Mapping rules: `set_event_destination` (event × destination mapping)
- Metrics: `create_metric`, `update_metric`, `delete_metric`
- Inputs accept arrays / a `batch` envelope so the AI applies many changes per call.

**Connector-backed**
- `scan_source` — runs a GA4/GTM/Ads scan for a bound source; returns discovered
  events/properties as **proposals only** (reuses today's `sdr_bootstrap/registry`
  scan logic). The AI or a human then promotes chosen proposals to `create_event` calls.
  This is the heart of "AI assists, doesn't auto-generate."

**Human gate**
- `publish` — snapshot `main` → `tp_versions`; **admin-role enforced** on the calling
  user. AI may draft endlessly; only a human admin publishes.
- `export_markdown` / `export_xlsx` — generated artifacts.

---

## 7. How Vagueness Is Structurally Prevented

1. **Write-time validation** — every create/update is validated against the typed
   schema + constraints: a property must have a `data_type`; an `enum` constraint must
   be a non-empty list; a mapping must reference a property/destination that resolves;
   names are unique per branch.
2. **`validate` action / banner** — live completeness report surfaced to both AI and UI.
3. **Human publish gate** — nothing reaches downstream consumers or a published version
   until a human admin runs `publish`. The AI can prepare, but cannot ship.

---

## 8. HTTP API + Editing UI (full structured editing)

JSON API under `/api/projects/{pid}/tracking-plan/...` mirroring service operations
(events, properties, sources, destinations, routing, mappings, metrics, categories,
validate, publish, versions, export). All endpoints call the shared service.

UI screens (Jinja templates following existing patterns; replace `sdr_home`/`sdr_edit`/
`sdr_versions`/`sdr_version_detail`/`sdr_diff`):

- **Plan overview** — events grouped by category, per-source status badges, search +
  filter (tag/source/destination/status), validation banner.
- **Event editor** — identity (name/display/category/tags), trigger, purpose/owners/
  consent; attached properties (pick from library or create new; set required/example/
  order); source scoping with per-source status toggles; destination mapping editor
  (dest event name + property mappings).
- **Property library** — list/create/edit with constraint editors (enum list, regex,
  min/max, object nesting) + reverse usage ("used by N events").
- **Sources** — list/create, connector binding, source→destination routing matrix,
  "Scan source" button (surfaces proposals).
- **Destinations** — list/create/config.
- **Metrics** — list/create.
- **Versions** — publish (admin), version history, diff between two snapshots
  (rendered JSONB deep-diff).
- **Export** — markdown + xlsx download.

---

## 9. Versioning & Publish

- `publish` serializes the full plan → `tp_versions.snapshot` (JSONB), bumps
  `version_number`, sets `tp_plans.current_version_id`. Admin-gated.
- Versions screen deep-diffs two snapshots → added/changed/removed events, properties,
  and mappings.

---

## 10. Downstream Compatibility (contract boundary)

Keep these signatures **identical** so the audit + tag-testing subsystems are untouched;
only the internals change to read the new backend.

- **Audit** (`app/tools/sdr_audit_helpers.py`): rewrite internals to read the latest
  published version snapshot (deserialize JSONB), but keep the same function names and
  return shapes — `get_sdr_expected_events`, `get_sdr_expected_for_event`,
  `compare_event_to_sdr`, `build_audit_sdr_summary`. `run_audit` and the auditing
  platform do not change. No published version → same empty/heuristic fallback as today.
- **Live tag testing** (`app/tag_testing/live_test/sdr_context.py`):
  `get_sdr_context_for_url` reads the published structured plan, same return shape, same
  `trigger_config.url_pattern` filtering.

These two interfaces are locked with contract tests.

---

## 11. Cutover — What Gets Retired

Greenfield → **delete, not migrate**:

- `sdr_parser.py` parse + projection-rebuild (~1k lines) → **deleted** (replaced by the
  structured generator only).
- `refine_sdr` conversational state machine + `sdr_refinement_state` + ~1k lines of
  section instructions → **retired** (direct CRUD replaces it).
- `generate_sdr` / `save_sdr` markdown actions → replaced by structured actions.
- `app/models/sdr.py` + `sdr_*` tables + old routes/templates → replaced by `tp_*`.
- `sdr_bootstrap/*`: scan logic survives inside `scan_source`; diagnostics logic
  survives inside `validate`; intake shrinks to an optional business-context JSONB on
  `tp_plans`.
- One alembic migration creates `tp_*` and drops `sdr_*` (greenfield → safe to drop).

Net: a large, **negative**-LOC change in the tools layer — which is the point.

---

## 12. Testing Strategy

`tox` (lint + typecheck + test) must be green before any push (project hard gate).

- **Service-layer units** — CRUD + each validation rule + `plan_to_dict` round-trip;
  golden tests for markdown/xlsx export.
- **Migration test** — schema builds; FK/unique constraints enforced.
- **MCP action tests** — each `tracking_plan` action happy + validation-failure path;
  `batch`; `scan_source` proposals; `publish` admin-gate.
- **HTTP/UI smoke tests.**
- **Downstream contract tests** — audit helpers + tag-testing return identical shapes
  against the new backend.
- **mypy** on new pinned modules + `ruff format`.

---

## 13. Phase Roadmap (full picture)

- **Phase 1 (this spec):** structured core, property library, sources/destinations/
  routing/mappings, metrics, categories, per-source status; shared service; MCP CRUD +
  `scan_source` proposals + `validate`; full editing UI; publish/versions; downstream
  repoint; markdown/xlsx export. Single `main` branch (branch-ready schema).
- **Phase 2:** branch workflow — copy-on-write branch creation, branch switcher (UI +
  MCP), diff/review screen, merge-to-main with conflict detection.
- **Phase 3:** collaboration — comment threads on events/properties/mappings, review/
  approval on merges, change-attribution surfacing, notifications.
- **Phase 4:** import (Avo/Segment/markdown/xlsx), codegen/snippets, live
  implementation-status sync from audit results, advanced metrics.

---

## 14. Open Risks / Notes

- **Large deletion + rewrite** of the tools layer (parser, refinement state machine,
  bootstrap synthesis). Net-negative LOC, but a big diff to review.
- **MCP spec accuracy** — `tracking_plan.json` is LLM-facing; action params/returns must
  be precise and well-described or the AI will misuse the tools.
- **`tp_` naming** vs keeping the `sdr_` brand — chose `tp_` for a clean break;
  user-facing terminology ("Solution Design Reference" / "tracking plan") unchanged.
- **One plan per project** preserved (`UNIQUE(project_id)` on `tp_plans`).
