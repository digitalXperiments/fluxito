# Tracking Plan Revamp — Plan 1D: Exports, Downstream Repoint & Cutover

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish Phase 1: generate markdown/xlsx artifacts from the structured plan, repoint the audit + live-tag-testing consumers to read the published structured snapshot **without changing their signatures**, then retire all markdown-era `sdr_*` code.

**Architecture:** New `plan_to_markdown` / `plan_to_xlsx` generators consume `plan_to_dict` output. `sdr_audit_helpers.py` and `sdr_context.py` keep their module paths + public signatures but rewrite their internals to read the latest `tp_versions.snapshot` and map it back to the legacy return shapes — so `analytics_tools`, `tagmanager_tools`, and `live_tag_test_tools` need **no changes**. Then we delete the parser, refinement state machine, bootstrap, old routes/templates/specs, and the `sdr_*` tables (migration `055`).

**Tech Stack:** Python 3.12, openpyxl, SQLAlchemy async, Alembic, pytest.

**Prerequisite:** Plans 1A + 1B + 1C merged.

**Critical ordering:** Do Tasks 1–3 (exports + repoint helpers) and prove them green **before** Task 4 (delete). The rewritten helpers must work against the new backend before the old backend is removed.

---

## Confirmed contracts to preserve (from the codebase)

**`app/tools/sdr_audit_helpers.py`** (callers: `analytics_tools.py:632`, `tagmanager_tools.py:417`):
- `async def get_sdr_expected_events(project_id) -> dict | None` → `{sdr_version, sdr_id, events:[event], event_index:{name:event}}` or `None`.
- `async def get_sdr_expected_for_event(project_id, event_name) -> dict | None`.
- `def compare_event_to_sdr(live_event, expected_event) -> list[dict]` — **pure**, unchanged.
- `def build_audit_sdr_summary(expected, live_event_names) -> dict` — **pure**, unchanged.
- Each `event` dict shape (legacy): `{name, status, parameters:[{name,type,required,source,example,validation_rule}], destinations:[{platform,platform_account_id,dest_event_name,mapping}], ...}`.

**`app/tag_testing/live_test/sdr_context.py`** (callers: `live_tag_test_tools.py:177,238`):
- `async def get_sdr_context_for_url(project_id, url=None) -> dict` → `{project_id, url, events:[{event_name, description, trigger_config, destinations:[str], parameters:[{name,type,required,description,example_value}]}], total, error}`.

Because we keep these signatures, **`analytics_tools.py`, `tagmanager_tools.py`, and `live_tag_test_tools.py` are NOT modified.**

---

## File Structure

**Create:**
- `app/services/tracking_plan/exports.py` — `plan_to_markdown`, `plan_to_xlsx`.
- `app/db/migrations/versions/055_drop_sdr_tables.py` — drop the 6 `sdr_*` tables.
- `tests/services/tracking_plan/test_exports.py`
- `tests/tools/test_audit_repoint.py`

**Rewrite (keep path + signatures, change internals):**
- `app/tools/sdr_audit_helpers.py`
- `app/tag_testing/live_test/sdr_context.py`

**Modify:**
- `app/tools/tracking_plan_tools.py` — add `export_markdown` action.
- `app/api/tracking_plan_routes.py` — add export endpoints.

**Delete (cutover):** `app/tools/sdr_parser.py`, `app/tools/sdr_tools.py`, `app/tools/sdr_templates.py`, `app/tools/sdr_excel_export.py`, `app/tools/sdr_bootstrap/` (whole dir), `app/api/sdr_routes.py`, `app/models/sdr.py`, `app/templates/sdr_home.html`, `sdr_edit.html`, `sdr_versions.html`, `sdr_version_detail.html`, `sdr_diff.html`, `tests/test_sdr_*.py` (all SDR-era test files).

**Edit to remove references (cutover):** `app/main.py`, `app/tools/registry.py`, `app/tools/unified.py`, `app/tools/specs/data/tracking_plan.json`, `app/templates/base.html`.

---

### Task 1: Markdown + xlsx exports from `plan_to_dict`

**Files:**
- Create: `app/services/tracking_plan/exports.py`
- Modify: `app/services/tracking_plan/__init__.py`
- Test: `tests/services/tracking_plan/test_exports.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/tracking_plan/test_exports.py
import pytest

from app.services.tracking_plan import (
    attach_property,
    create_destination,
    create_event,
    create_property,
    create_source,
    get_main_branch,
    get_or_create_plan,
    plan_to_dict,
    set_event_destination,
    set_event_sources,
)
from app.services.tracking_plan.exports import plan_to_markdown, plan_to_xlsx
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _sample_plan_dict(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id, name="P")
    branch = await get_main_branch(session, plan)
    ev = await create_event(session, branch, name="purchase", purpose="completes checkout")
    prop = await create_property(session, branch, name="value", data_type="float")
    await attach_property(session, ev.id, prop.id, required=True, example="9.99")
    src = await create_source(session, branch, name="web", platform_type="web")
    dest = await create_destination(session, branch, name="GA4", platform="ga4")
    await set_event_sources(session, branch, ev.id, [{"source_id": src.id, "implementation_status": "implemented"}])
    await set_event_destination(session, branch, ev.id, dest.id, dest_event_name="purchase")
    return await plan_to_dict(session, plan, branch)


@pytest.mark.anyio
async def test_plan_to_markdown(db_session_factory):
    async with db_session_factory() as session:
        data = await _sample_plan_dict(session)
        md = plan_to_markdown(data)
        assert "# P" in md
        assert "## purchase" in md
        assert "value" in md
        assert "9.99" in md
        assert "GA4" in md


@pytest.mark.anyio
async def test_plan_to_xlsx_is_valid_workbook(db_session_factory):
    import io

    from openpyxl import load_workbook

    async with db_session_factory() as session:
        data = await _sample_plan_dict(session)
        raw = plan_to_xlsx(data)
        wb = load_workbook(io.BytesIO(raw))
        assert "Events" in wb.sheetnames
        assert "Properties" in wb.sheetnames
        # Events sheet has a header row + the purchase row
        ws = wb["Events"]
        names = [c.value for c in ws["A"]]
        assert "purchase" in names
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_exports.py -v`
Expected: FAIL (`app.services.tracking_plan.exports` missing).

- [ ] **Step 3: Implement exports**

```python
# app/services/tracking_plan/exports.py
"""Generate human artifacts (markdown, xlsx) from a plan_to_dict() dict.

These are OUTPUTS only — the relational tables remain the source of truth."""

import io

from openpyxl import Workbook


def plan_to_markdown(plan: dict) -> str:
    """Render a readable Markdown tracking-plan doc from a plan_to_dict() dict."""
    lines: list[str] = []
    lines.append(f"# {plan['plan']['name']}")
    if plan["plan"].get("description"):
        lines.append("")
        lines.append(plan["plan"]["description"])
    lines.append("")

    lines.append("## Events")
    lines.append("")
    for ev in plan["events"]:
        lines.append(f"## {ev['name']}")
        if ev.get("display_name"):
            lines.append(f"*{ev['display_name']}*")
        if ev.get("category"):
            lines.append(f"- **Category:** {ev['category']}")
        if ev.get("purpose"):
            lines.append(f"- **Purpose:** {ev['purpose']}")
        if ev.get("trigger_type"):
            lines.append(f"- **Trigger:** {ev['trigger_type']}")
        if ev.get("tags"):
            lines.append(f"- **Tags:** {', '.join(ev['tags'])}")
        if ev["sources"]:
            srcs = ", ".join(f"{s['name']} ({s['implementation_status']})" for s in ev["sources"])
            lines.append(f"- **Sources:** {srcs}")
        lines.append("")
        if ev["properties"]:
            lines.append("| Property | Type | Required | Example |")
            lines.append("| --- | --- | --- | --- |")
            for p in ev["properties"]:
                lines.append(
                    f"| {p['name']} | {p['data_type']} | {'yes' if p['required'] else 'no'} | {p.get('example') or ''} |"
                )
            lines.append("")
        if ev["destinations"]:
            lines.append("**Destinations:**")
            for d in ev["destinations"]:
                lines.append(f"- {d['destination']}: `{d.get('dest_event_name') or ev['name']}`")
            lines.append("")

    if plan["properties"]["user"]:
        lines.append("## User Properties")
        lines.append("")
        lines.append("| Name | Type |")
        lines.append("| --- | --- |")
        for p in plan["properties"]["user"]:
            lines.append(f"| {p['name']} | {p['data_type']} |")
        lines.append("")

    if plan["sources"]:
        lines.append("## Sources → Destinations")
        lines.append("")
        for s in plan["sources"]:
            routed = ", ".join(s["destinations"]) or "—"
            lines.append(f"- **{s['name']}** ({s.get('platform_type') or 'n/a'}) → {routed}")
        lines.append("")

    if plan["metrics"]:
        lines.append("## Metrics")
        lines.append("")
        for m in plan["metrics"]:
            lines.append(f"- **{m['name']}** ({m['type']}) — {m.get('event') or 'n/a'}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def plan_to_xlsx(plan: dict) -> bytes:
    """Render a multi-sheet workbook from a plan_to_dict() dict."""
    wb = Workbook()

    ws = wb.active
    ws.title = "Events"
    ws.append(["name", "display_name", "category", "purpose", "trigger_type", "sources", "destinations"])
    for ev in plan["events"]:
        ws.append([
            ev["name"],
            ev.get("display_name") or "",
            ev.get("category") or "",
            ev.get("purpose") or "",
            ev.get("trigger_type") or "",
            "; ".join(f"{s['name']}:{s['implementation_status']}" for s in ev["sources"]),
            "; ".join(d["destination"] for d in ev["destinations"]),
        ])

    wp = wb.create_sheet("Properties")
    wp.append(["event", "property", "data_type", "required", "example"])
    for ev in plan["events"]:
        for p in ev["properties"]:
            wp.append([ev["name"], p["name"], p["data_type"], "yes" if p["required"] else "no", p.get("example") or ""])
    for p in plan["properties"]["user"]:
        wp.append(["(user)", p["name"], p["data_type"], "", ""])

    wd = wb.create_sheet("Destinations")
    wd.append(["name", "platform", "account_id"])
    for d in plan["destinations"]:
        wd.append([d["name"], d["platform"], d.get("platform_account_id") or ""])

    ws_src = wb.create_sheet("Sources")
    ws_src.append(["name", "platform_type", "routes_to"])
    for s in plan["sources"]:
        ws_src.append([s["name"], s.get("platform_type") or "", "; ".join(s["destinations"])])

    wm = wb.create_sheet("Metrics")
    wm.append(["name", "type", "event", "description"])
    for m in plan["metrics"]:
        wm.append([m["name"], m["type"], m.get("event") or "", m.get("description") or ""])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 4: Re-export in `__init__.py`**

Add to `app/services/tracking_plan/__init__.py`:
```python
from .exports import plan_to_markdown, plan_to_xlsx
```
Add `"plan_to_markdown", "plan_to_xlsx",` to `__all__`.

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/services/tracking_plan/test_exports.py -v`
Expected: PASS.

- [ ] **Step 6: Wire export action + HTTP endpoints**

In `app/tools/tracking_plan_tools.py` `run_action`, add (just before the `publish` block):
```python
        if action == "export_markdown":
            from app.services.tracking_plan import plan_to_markdown

            data = await plan_to_dict(session, ctx.plan, branch)
            return _ok(format="markdown", content=plan_to_markdown(data))
```
And add `"export_markdown": ("tracking_plan_v2", "export_markdown"),` to `TRACKING_PLAN_ROUTES` in `app/tools/unified.py`, plus a spec entry in `tracking_plan.json`.

In `app/api/tracking_plan_routes.py`, add two endpoints:
```python
from fastapi.responses import PlainTextResponse, Response
from app.services.tracking_plan import plan_to_markdown, plan_to_xlsx


@router.get("/api/projects/{project_id}/tracking-plan/export.md")
async def api_export_md(project_id: str, request: Request):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        branch = await get_main_branch(db, plan)
        data = await plan_to_dict(db, plan, branch)
        await db.commit()
        return PlainTextResponse(plan_to_markdown(data))


@router.get("/api/projects/{project_id}/tracking-plan/export.xlsx")
async def api_export_xlsx(project_id: str, request: Request):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        branch = await get_main_branch(db, plan)
        data = await plan_to_dict(db, plan, branch)
        await db.commit()
        return Response(
            plan_to_xlsx(data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="tracking-plan.xlsx"'},
        )
```

- [ ] **Step 7: Format, lint, commit**

```bash
ruff format app/services/tracking_plan/exports.py app/tools/tracking_plan_tools.py app/api/tracking_plan_routes.py app/tools/unified.py tests/services/tracking_plan/test_exports.py
ruff check app/services/tracking_plan/ app/tools/tracking_plan_tools.py app/api/tracking_plan_routes.py
git add app/services/tracking_plan/exports.py app/services/tracking_plan/__init__.py app/tools/tracking_plan_tools.py app/api/tracking_plan_routes.py app/tools/unified.py app/tools/specs/data/tracking_plan.json tests/services/tracking_plan/test_exports.py
git commit -m "feat(tracking-plan): markdown + xlsx exports"
```

---

### Task 2: Repoint the audit helpers to the published snapshot

**Files:**
- Rewrite: `app/tools/sdr_audit_helpers.py`
- Test: `tests/tools/test_audit_repoint.py`

Keep `compare_event_to_sdr` and `build_audit_sdr_summary` exactly as they are (pure functions). Replace the two loaders so they read `latest_snapshot_for_project` and map the snapshot's event shape into the legacy expected-event shape.

- [ ] **Step 1: Write the failing contract test**

```python
# tests/tools/test_audit_repoint.py
import pytest

import app.app_state as app_state
from app.services.tracking_plan import (
    attach_property,
    create_destination,
    create_event,
    create_property,
    create_source,
    get_main_branch,
    get_or_create_plan,
    publish_branch,
    set_event_destination,
    set_event_sources,
)
from app.tools.sdr_audit_helpers import build_audit_sdr_summary, get_sdr_expected_events
from tests.services.tracking_plan.test_models import _make_project_and_user


@pytest.mark.anyio
async def test_audit_reads_published_snapshot(db_session_factory, monkeypatch):
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)
        ev = await create_event(session, branch, name="purchase")
        prop = await create_property(session, branch, name="value", data_type="float")
        await attach_property(session, ev.id, prop.id, required=True, example="9.99")
        src = await create_source(session, branch, name="web")
        dest = await create_destination(session, branch, name="GA4", platform="ga4")
        await set_event_sources(session, branch, ev.id, [{"source_id": src.id, "implementation_status": "implemented"}])
        await set_event_destination(session, branch, ev.id, dest.id, dest_event_name="purchase")
        await publish_branch(session, plan, branch, user_id=user_id, changelog="v1")
        await session.commit()

    expected = await get_sdr_expected_events(project_id)
    assert expected is not None
    assert expected["sdr_version"] == "1.0"
    ev_dict = expected["event_index"]["purchase"]
    # Legacy shape preserved
    assert ev_dict["parameters"][0]["name"] == "value"
    assert ev_dict["parameters"][0]["required"] is True
    assert ev_dict["parameters"][0]["type"] == "float"
    assert ev_dict["destinations"][0]["platform"] == "ga4"
    assert ev_dict["status"] == "implemented"

    summary = build_audit_sdr_summary(expected, live_event_names=["purchase", "page_view"])
    assert "purchase" in summary["matched"]
    assert "page_view" in summary["unexpected_live"]


@pytest.mark.anyio
async def test_audit_returns_none_without_publish(db_session_factory, monkeypatch):
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        await session.commit()
    assert await get_sdr_expected_events(project_id) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/tools/test_audit_repoint.py -v`
Expected: FAIL (old helper still queries `app.models.sdr`).

- [ ] **Step 3: Rewrite `sdr_audit_helpers.py`**

Replace the imports + the two loader functions. Keep `compare_event_to_sdr` and `build_audit_sdr_summary` byte-for-byte as they currently are (re-read the existing file and preserve them). New top + loaders:

```python
# app/tools/sdr_audit_helpers.py  (top of file)
"""Audit consumer adapter — maps the published structured tracking-plan
snapshot into the legacy expected-event shape used by the audit tools.

Public functions keep their signatures so analytics_tools / tagmanager_tools
are unaffected."""

from typing import Any

import app.app_state as app_state
from app.services.tracking_plan import latest_snapshot_for_project

_STATUS_RANK = {"deprecated": 0, "planned": 1, "implemented": 2, "verified": 3}


def _rollup_status(sources: list[dict]) -> str:
    """Collapse per-source statuses into one event status (highest wins)."""
    if not sources:
        return "planned"
    statuses = [s.get("implementation_status", "planned") for s in sources]
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 1))


def _snapshot_event_to_legacy(ev: dict, dest_platform_by_name: dict[str, str]) -> dict:
    """Map a snapshot event dict into the legacy expected-event shape."""
    return {
        "name": ev["name"],
        "status": _rollup_status(ev.get("sources", [])),
        "purpose": ev.get("purpose"),
        "trigger_type": ev.get("trigger_type"),
        "trigger_config": ev.get("trigger_config"),
        "parameters": [
            {
                "name": p["name"],
                "type": p.get("data_type"),
                "required": p.get("required", False),
                "source": None,
                "example": p.get("example"),
                "validation_rule": None,
            }
            for p in ev.get("properties", [])
        ],
        "destinations": [
            {
                "platform": dest_platform_by_name.get(d["destination"], d["destination"]),
                "platform_account_id": None,
                "dest_event_name": d.get("dest_event_name"),
                "mapping": d.get("property_mappings"),
            }
            for d in ev.get("destinations", [])
        ],
    }


async def get_sdr_expected_events(project_id: Any) -> dict | None:
    async with app_state.db_session_factory() as db:
        snapshot = await latest_snapshot_for_project(db, project_id)
    if snapshot is None:
        return None
    dest_platform_by_name = {d["name"]: d["platform"] for d in snapshot.get("destinations", [])}
    events = [_snapshot_event_to_legacy(ev, dest_platform_by_name) for ev in snapshot.get("events", [])]
    return {
        "sdr_version": snapshot.get("plan", {}).get("current_version_id") and snapshot["plan"].get("version")
        or _version_from_snapshot(snapshot),
        "sdr_id": snapshot.get("plan", {}).get("id"),
        "events": events,
        "event_index": {e["name"]: e for e in events},
    }


def _version_from_snapshot(snapshot: dict) -> str:
    # plan_to_dict does not embed the version number; the publish layer stores it
    # on the TPVersion row. latest_snapshot_for_project returns only the snapshot,
    # so expose the version via a top-level key written at publish time.
    return snapshot.get("__version__", "")


async def get_sdr_expected_for_event(project_id: Any, event_name: str) -> dict | None:
    expected = await get_sdr_expected_events(project_id)
    if not expected:
        return None
    return expected["event_index"].get(event_name)
```

> **Important fix-up:** `plan_to_dict` does not include the version number, so the test expects `sdr_version == "1.0"`. Make this clean by having `publish_branch` (Plan 1B) stamp the number into the snapshot before saving. Edit `app/services/tracking_plan/publish.py` `publish_branch` to set `snapshot["__version__"] = version_number` **before** constructing `TPVersion(snapshot=snapshot, ...)`. Then simplify `get_sdr_expected_events` to `"sdr_version": snapshot.get("__version__", "")`. Re-run Plan 1B's `test_publish` (still passes — it only checks `events`). Update the line in this task accordingly:

```python
    return {
        "sdr_version": snapshot.get("__version__", ""),
        "sdr_id": snapshot.get("plan", {}).get("id"),
        "events": events,
        "event_index": {e["name"]: e for e in events},
    }
```
(Delete the `_version_from_snapshot` helper and the convoluted `sdr_version` expression above; use the simple form.)

- [ ] **Step 4: Apply the publish stamp**

In `app/services/tracking_plan/publish.py`, inside `publish_branch`, after `snapshot = await plan_to_dict(...)` and after computing `version_number`, add:
```python
    version_number = _next_version_number(latest)
    snapshot["__version__"] = version_number
```
and pass `version_number=version_number` to `TPVersion(...)`.

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/tools/test_audit_repoint.py tests/services/tracking_plan/test_publish.py -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
ruff format app/tools/sdr_audit_helpers.py app/services/tracking_plan/publish.py tests/tools/test_audit_repoint.py
ruff check app/tools/sdr_audit_helpers.py app/services/tracking_plan/publish.py
git add app/tools/sdr_audit_helpers.py app/services/tracking_plan/publish.py tests/tools/test_audit_repoint.py
git commit -m "refactor(audit): read tracking-plan snapshot, preserve legacy shape"
```

---

### Task 3: Repoint live-tag-testing context

**Files:**
- Rewrite: `app/tag_testing/live_test/sdr_context.py`
- Test: add to `tests/tools/test_audit_repoint.py`

- [ ] **Step 1: Write the failing test (append to `test_audit_repoint.py`)**

```python
@pytest.mark.anyio
async def test_sdr_context_for_url(db_session_factory, monkeypatch):
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)
    from app.tag_testing.live_test.sdr_context import get_sdr_context_for_url

    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)
        ev = await create_event(
            session, branch, name="purchase", description="checkout", trigger_config={"url_pattern": "/checkout"}
        )
        prop = await create_property(session, branch, name="value", data_type="float")
        await attach_property(session, ev.id, prop.id, required=True, example="9.99")
        dest = await create_destination(session, branch, name="GA4", platform="ga4")
        await set_event_destination(session, branch, ev.id, dest.id)
        await publish_branch(session, plan, branch, user_id=user_id)
        await session.commit()

    ctx = await get_sdr_context_for_url(str(project_id), "https://x.com/checkout")
    assert ctx["total"] == 1
    e = ctx["events"][0]
    assert e["event_name"] == "purchase"
    assert e["parameters"][0]["name"] == "value"
    assert e["destinations"] == ["ga4"]

    # URL that doesn't match the pattern excludes the event
    ctx2 = await get_sdr_context_for_url(str(project_id), "https://x.com/home")
    assert ctx2["total"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/tools/test_audit_repoint.py::test_sdr_context_for_url -v`
Expected: FAIL (old code queries `sdr_events`).

- [ ] **Step 3: Rewrite `sdr_context.py`**

```python
# app/tag_testing/live_test/sdr_context.py
"""Live-tag-testing SDR context — reads the published structured tracking-plan
snapshot, filtered by URL pattern. Signature preserved for live_tag_test_tools."""

import re

import app.app_state as app_state
from app.services.tracking_plan import latest_snapshot_for_project


async def get_sdr_context_for_url(project_id: str, url: str | None = None) -> dict:
    try:
        async with app_state.db_session_factory() as db:
            snapshot = await latest_snapshot_for_project(db, project_id)
    except Exception as exc:  # pragma: no cover - defensive
        return {"project_id": project_id, "url": url, "events": [], "total": 0, "error": str(exc)}

    if snapshot is None:
        return {"project_id": project_id, "url": url, "events": [], "total": 0, "error": None}

    dest_platform_by_name = {d["name"]: d["platform"] for d in snapshot.get("destinations", [])}
    out_events = []
    for ev in snapshot.get("events", []):
        trigger_config = ev.get("trigger_config") or {}
        pattern = trigger_config.get("url_pattern")
        if pattern and url and not re.search(pattern, url, re.I):
            continue
        out_events.append(
            {
                "event_name": ev["name"],
                "description": ev.get("description") or "",
                "trigger_config": trigger_config,
                "destinations": [dest_platform_by_name.get(d["destination"], d["destination"]) for d in ev.get("destinations", [])],
                "parameters": [
                    {
                        "name": p["name"],
                        "type": p.get("data_type"),
                        "required": p.get("required", False),
                        "description": p.get("override_description") or "",
                        "example_value": p.get("example") or "",
                    }
                    for p in ev.get("properties", [])
                ],
            }
        )

    return {"project_id": project_id, "url": url, "events": out_events, "total": len(out_events), "error": None}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/tools/test_audit_repoint.py -v`
Expected: PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
ruff format app/tag_testing/live_test/sdr_context.py tests/tools/test_audit_repoint.py
ruff check app/tag_testing/live_test/sdr_context.py
git add app/tag_testing/live_test/sdr_context.py tests/tools/test_audit_repoint.py
git commit -m "refactor(live-test): read tracking-plan snapshot for URL context"
```

---

### Task 4: Cutover — delete the markdown-era SDR system

Do this only after Tasks 1–3 are green. Work in small commits and run the suite after each deletion group.

**Files:** see the delete/modify lists at the top.

- [ ] **Step 1: Remove tool + route registrations**

- `app/tools/registry.py`: delete `from app.tools.sdr_tools import register_sdr_tools` and the `register_sdr_tools(mcp_server)` call (keep `register_tracking_plan_tools`).
- `app/main.py`: delete `from app.api.sdr_routes import router as sdr_router` and `app.include_router(sdr_router)`.
- `app/tools/unified.py`: in `TRACKING_PLAN_ROUTES`, delete the **old** entries (`generate`, `save`, `refresh_sources`, `capture_intake`, `get_intake`, `list_sources`, `diagnose`, `refine`). Keep all the v2 entries from Plan 1B.
- `app/tools/specs/data/tracking_plan.json`: delete the old action objects (`generate`, `save`, `refine`, etc.); keep the v2 actions.

- [ ] **Step 2: Remove the nav link to the old SDR page**

In `app/templates/base.html`, delete the "Solution Design" nav link (the one pointing at `/solution-design`). Keep the "Tracking Plan" link added in Plan 1C.

- [ ] **Step 3: Delete the source files**

```bash
git rm app/tools/sdr_parser.py app/tools/sdr_tools.py app/tools/sdr_templates.py app/tools/sdr_excel_export.py
git rm -r app/tools/sdr_bootstrap
git rm app/api/sdr_routes.py
git rm app/models/sdr.py
git rm app/templates/sdr_home.html app/templates/sdr_edit.html app/templates/sdr_versions.html app/templates/sdr_version_detail.html app/templates/sdr_diff.html
```

- [ ] **Step 4: Delete the old SDR tests**

```bash
git rm tests/test_sdr_*.py
```
(Confirm the glob with `ls tests/test_sdr_*.py` first; delete exactly those. Do NOT delete `tests/services/tracking_plan/` or `tests/tools/test_audit_repoint.py`.)

- [ ] **Step 5: Find and fix any dangling imports**

Run:
```bash
grep -rn "app.models.sdr\|sdr_parser\|sdr_tools\|sdr_templates\|sdr_excel_export\|sdr_bootstrap\|register_sdr_tools\|sdr_routes" app/ tests/
```
Expected: **no matches** except `sdr_audit_helpers.py` and `sdr_context.py` (which we kept and rewrote) and their callers (`analytics_tools.py`, `tagmanager_tools.py`, `live_tag_test_tools.py` — these import the preserved functions and need no change). If anything else matches, fix it (remove the import / usage).

- [ ] **Step 6: Import smoke test**

Run: `python -c "import app.main"`
Expected: no ImportError.

- [ ] **Step 7: Commit the cutover**

```bash
ruff format app/tools/registry.py app/main.py app/tools/unified.py app/templates/base.html
ruff check app/tools/ app/api/ app/main.py
git add -A
git commit -m "refactor(tracking-plan): retire markdown-era SDR system"
```

---

### Task 5: Migration to drop the `sdr_*` tables

**Files:**
- Create: `app/db/migrations/versions/055_drop_sdr_tables.py`

- [ ] **Step 1: Write the migration**

```python
# app/db/migrations/versions/055_drop_sdr_tables.py
"""055 — Drop the retired markdown-era sdr_* tables.

The structured tracking plan (tp_*, migration 054) is now the source of truth.

Revision ID: 055_drop_sdr_tables
Revises: 054_tracking_plan_schema
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "055_drop_sdr_tables"
down_revision = "054_tracking_plan_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in (
        "sdr_refinement_state",
        "sdr_destinations",
        "sdr_parameters",
        "sdr_events",
        "sdr_intakes",
        "sdr_versions",
        "sdrs",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def downgrade() -> None:
    # Irreversible: the markdown-era schema is retired. Recreate from 028/041/043
    # history only if you truly need to roll back (not supported here).
    raise NotImplementedError("055_drop_sdr_tables is a one-way cutover migration")
```

> Confirm the exact list of `sdr_*` tables to drop with `python -m alembic upgrade head` against a scratch DB first, or `\dt sdr_*` in psql. `DROP TABLE IF EXISTS ... CASCADE` is safe even if a name is absent.

- [ ] **Step 2: Apply and verify**

```bash
python -m alembic upgrade head
```
Expected: success. `\dt sdr_*` shows no tables; `\dt tp_*` shows the 13 tracking-plan tables.

- [ ] **Step 3: Commit**

```bash
git add app/db/migrations/versions/055_drop_sdr_tables.py
git commit -m "feat(tracking-plan): drop retired sdr_* tables (migration 055)"
```

---

### Task 6: Full verification (tox gate)

**Files:** none.

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -q`
Expected: green. Investigate any failure referencing removed SDR code (a missed caller) and fix.

- [ ] **Step 2: Lint + format**

Run: `tox -e lint` (or `ruff check app tests && ruff format --check app tests`).
Expected: clean.

- [ ] **Step 3: Typecheck**

Run: `tox -e typecheck`.
Expected: clean (fix typing on the new `app/services/tracking_plan/`, `app/tools/tracking_plan_tools.py`, `app/api/tracking_plan_routes.py` if they're in the pinned set).

- [ ] **Step 4: Full `tox` (the push gate)**

Run: `tox`.
Expected: all three environments green. This is the project's hard gate before any push to a branch.

- [ ] **Step 5: Final commit (if anything changed)**

```bash
git add -A
git commit -m "chore(tracking-plan): phase 1 green — exports, repoint, cutover complete"
```

---

## Self-Review (against spec §9–§11)

- **Markdown + xlsx export from the structured plan** (spec §10 artifacts) → Task 1. ✅
- **Audit repoint, signatures preserved** (`get_sdr_expected_events`, `get_sdr_expected_for_event`, `compare_event_to_sdr`, `build_audit_sdr_summary`) (spec §10) → Task 2; pure functions untouched, loaders read the snapshot. ✅
- **Live tag testing repoint, signature preserved** (`get_sdr_context_for_url`) (spec §10) → Task 3. ✅
- **`analytics_tools` / `tagmanager_tools` / `live_tag_test_tools` unchanged** — confirmed by preserving the helper module paths + signatures; Task 4 Step 5 grep verifies no other coupling. ✅
- **Cutover deletions** (parser, refinement state machine, bootstrap, old routes/templates/specs/registrations) (spec §11) → Task 4. ✅
- **Drop `sdr_*` tables via a new migration** (spec §11) → Task 5. ✅
- **`tox` green gate** (project policy) → Task 6. ✅
- **Version number in the audit contract** — resolved by stamping `snapshot["__version__"]` at publish time (Task 2 Step 4), keeping the legacy `sdr_version` key meaningful. ✅
- **Placeholder scan:** no TODOs; the "preserve `compare_event_to_sdr`/`build_audit_sdr_summary` as-is" instruction is concrete (re-read + keep). The one self-correcting note (simplify the `sdr_version` expression) is resolved inline in Task 2 Step 3. ✅
- **Name consistency:** snapshot keys used here (`events[].properties[].data_type/required/example`, `events[].destinations[].destination/dest_event_name/property_mappings`, `destinations[].name/platform`, `plan.id`) match `plan_to_dict` (Plan 1A Task 10). ✅
```
