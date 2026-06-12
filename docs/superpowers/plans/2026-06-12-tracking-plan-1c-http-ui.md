# Tracking Plan Revamp — Plan 1C: HTTP API + Editing UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the team a browser UI to fully manage the structured tracking plan, backed by a JSON API. The API reuses the tested `run_action` core (Plan 1B) for all writes via one `POST .../action` endpoint, plus dedicated GETs for the plan and versions.

**Architecture:** A new FastAPI router (`app/api/tracking_plan_routes.py`) resolves the active project + user + role, builds the same `(session, branch, ctx)` the MCP wrapper builds, and calls `run_action`. The page (`/tracking-plan`) is server-served Jinja that boots a vanilla-JS client which fetches the plan JSON and renders editable tabs (Events, Properties, Sources, Destinations, Metrics, Versions). This matches the codebase's vanilla-JS convention (no framework).

**Tech Stack:** FastAPI, Jinja2 (`render()` helper from `app/templating.py`), vanilla JS + `fetch`, `app/static/css/app.css`. Auth via `_resolve_user_ctx` / `ensure_active_project` (same as `sdr_routes.py`).

**Prerequisite:** Plans 1A + 1B merged.

**Scope:** Full structured editing for events (incl. attached properties, source scoping with per-source status, destination mappings), the property library, sources (+ routing), destinations, metrics, categories, plus validate + publish + version history. Branch switching, comments, and diff visualization are later phases.

---

## Conventions & confirmed patterns

- Router: `router = APIRouter()`, included in `app/main.py` via `app.include_router(...)` (near `sdr_router`).
- Auth/project: `_resolve_user_ctx(request)` → user_ctx (`.user_id`, `.email`) or None; `ensure_active_project(request, user_id)` → project_id str or None; `_load_user_view(user_ctx)` → dict for templates; `set_active_project_cookie(response, pid)`.
- Role: query `ProjectMember` (`project_id`, `user_id`, `is_active==True`) → `.role`; admin roles are `ROLE_OWNER`/`ROLE_ADMIN` (imported in `sdr_routes.py`).
- Render: `from app.templating import render`; `render(request, "tpl.html", {...})`.
- DB: `app_state.db_session_factory()` async context manager.
- Reuse: `from app.tools.tracking_plan_tools import run_action, _Ctx`.

---

## File Structure

**Create:**
- `app/api/tracking_plan_routes.py` — router: page route, read APIs, the `action` write API, versions APIs.
- `app/templates/tracking_plan.html` — the editing UI (inline CSS + JS).
- `tests/api/test_tracking_plan_routes.py` — API tests via `httpx.AsyncClient`.

**Modify:**
- `app/main.py` — import + `include_router` the new router.
- `app/templates/base.html` — add a "Tracking Plan" nav link (next to "Solution Design").

---

### Task 1: API router — reads, the action endpoint, versions

**Files:**
- Create: `app/api/tracking_plan_routes.py`
- Modify: `app/main.py`
- Test: `tests/api/test_tracking_plan_routes.py`

- [ ] **Step 1: Look at `sdr_routes.py` lines 1-120**

Read `app/api/sdr_routes.py` top (imports, `_resolve_user_ctx`/`ensure_active_project`/`_require_user_and_project`/`_require_project_admin`, `ROLE_OWNER`/`ROLE_ADMIN` imports) so the new router imports match exactly. Mirror those imports.

- [ ] **Step 2: Write the failing test**

```python
# tests/api/test_tracking_plan_routes.py
import pytest

# These tests exercise the router logic against the test DB. They monkeypatch
# auth + active-project resolution so we don't need a real session cookie.


@pytest.fixture
async def client(db_engine, db_session_factory, monkeypatch):
    import app.app_state as app_state
    from httpx import ASGITransport, AsyncClient

    # Point the app's session factory at the test engine
    monkeypatch.setattr(app_state, "db_session_factory", db_session_factory)

    # Seed a project + user, stub auth to return them
    from app.models.project import Project
    from app.models.role import MemberRole
    from app.models.user import User
    import uuid

    async with db_session_factory() as s:
        user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com", name="T")
        s.add(user)
        await s.flush()
        project = Project(name="P", owner_id=user.id)
        s.add(project)
        await s.flush()
        # Make the user an owner member (adjust model/fields to match the repo)
        s.add(MemberRole(project_id=project.id, user_id=user.id, role="owner", is_active=True))
        await s.commit()
        uid, pid = str(user.id), str(project.id)

    import app.api.tracking_plan_routes as tpr

    async def fake_resolve_ctx(request):
        from types import SimpleNamespace

        return SimpleNamespace(user_id=uid, email="t@example.com")

    async def fake_ensure_project(request, user_id):
        return pid

    monkeypatch.setattr(tpr, "_resolve_user_ctx", fake_resolve_ctx)
    monkeypatch.setattr(tpr, "ensure_active_project", fake_ensure_project)

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c._pid = pid  # type: ignore[attr-defined]
        yield c


@pytest.mark.anyio
async def test_get_plan_and_create_event(client):
    pid = client._pid
    # Empty plan auto-creates
    r = await client.get(f"/api/projects/{pid}/tracking-plan")
    assert r.status_code == 200
    assert r.json()["events"] == []

    # Create an event via the action endpoint
    r = await client.post(
        f"/api/projects/{pid}/tracking-plan/action",
        json={"action": "create_event", "params": {"name": "purchase"}},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = await client.get(f"/api/projects/{pid}/tracking-plan")
    assert [e["name"] for e in r.json()["events"]] == ["purchase"]


@pytest.mark.anyio
async def test_publish_creates_version(client):
    pid = client._pid
    await client.post(
        f"/api/projects/{pid}/tracking-plan/action",
        json={"action": "create_event", "params": {"name": "purchase"}},
    )
    r = await client.post(
        f"/api/projects/{pid}/tracking-plan/action", json={"action": "publish", "params": {"changelog": "v1"}}
    )
    assert r.json()["ok"] is True
    versions = (await client.get(f"/api/projects/{pid}/tracking-plan/versions")).json()
    assert versions["versions"][0]["version_number"] == "1.0"
```

> **Implementer note:** adjust the `Project`/`User`/member-role seeding to match the real models (the membership model is `MemberRole` or `ProjectMember` — check `app/models/role.py` and `app/models/project.py`; reuse whatever `_require_project_admin` in `sdr_routes.py` queries). The `anyio_backend`/`db_engine`/`db_session_factory` fixtures come from `tests/conftest.py`.

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/api/test_tracking_plan_routes.py -v`
Expected: FAIL (router module missing / 404).

- [ ] **Step 4: Implement the router**

```python
# app/api/tracking_plan_routes.py
"""HTTP API + page for the structured tracking plan.

Writes reuse the tested run_action core (app/tools/tracking_plan_tools.py); the
route layer only resolves auth + the active project/branch and commits."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import desc, select

import app.app_state as app_state
from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx
from app.api.project_routes import ensure_active_project, set_active_project_cookie
from app.models.role import MemberRole  # adjust if membership model differs
from app.models.tracking_plan import TPVersion
from app.services.tracking_plan import (
    get_main_branch,
    get_or_create_plan,
    plan_to_dict,
    validate_plan,
)
from app.templating import render
from app.tools.tracking_plan_tools import _Ctx, run_action

router = APIRouter()

_ADMIN_ROLES = ("owner", "admin")


class ActionPayload(BaseModel):
    action: str
    params: dict = {}


async def _resolve(request: Request) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Return (user_uuid, project_uuid, role). Raises HTTP 401/400/403."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user_uuid = uuid.UUID(user_ctx.user_id)
    pid_str = await ensure_active_project(request, user_ctx.user_id)
    if not pid_str:
        raise HTTPException(status_code=400, detail="No active project")
    project_uuid = uuid.UUID(pid_str)
    async with app_state.db_session_factory() as db:
        member = (
            await db.execute(
                select(MemberRole).where(
                    MemberRole.project_id == project_uuid,
                    MemberRole.user_id == user_uuid,
                    MemberRole.is_active == True,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(status_code=403, detail="Not a project member")
        return user_uuid, project_uuid, member.role


def _check_param_pid(param_pid: str, active: uuid.UUID) -> None:
    try:
        if uuid.UUID(param_pid) != active:
            raise HTTPException(status_code=403, detail="Project mismatch")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id")


# ----------------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------------
@router.get("/tracking-plan")
async def tracking_plan_page(request: Request):
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/tracking-plan", status_code=302)
    pid_str = await ensure_active_project(request, user_ctx.user_id)
    if not pid_str:
        return RedirectResponse("/projects", status_code=302)
    user_view = await _load_user_view(user_ctx)
    response = render(
        request,
        "tracking_plan.html",
        {"user": user_view, "active": "tracking_plan", "project_id": pid_str},
    )
    set_active_project_cookie(response, pid_str)
    return response


# ----------------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------------
@router.get("/api/projects/{project_id}/tracking-plan")
async def api_get_plan(project_id: str, request: Request):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        branch = await get_main_branch(db, plan)
        data = await plan_to_dict(db, plan, branch)
        await db.commit()  # persist auto-created plan/branch
        return JSONResponse(data)


@router.get("/api/projects/{project_id}/tracking-plan/validate")
async def api_validate(project_id: str, request: Request):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        branch = await get_main_branch(db, plan)
        report = await validate_plan(db, plan, branch)
        await db.commit()
        return JSONResponse(report)


@router.get("/api/projects/{project_id}/tracking-plan/versions")
async def api_versions(project_id: str, request: Request):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        rows = (
            await db.execute(
                select(TPVersion).where(TPVersion.plan_id == plan.id).order_by(desc(TPVersion.published_at))
            )
        ).scalars().all()
        await db.commit()
        return JSONResponse(
            {
                "versions": [
                    {
                        "id": str(v.id),
                        "version_number": v.version_number,
                        "changelog": v.changelog,
                        "published_at": v.published_at.isoformat() if v.published_at else None,
                    }
                    for v in rows
                ]
            }
        )


@router.get("/api/projects/{project_id}/tracking-plan/versions/{version_id}")
async def api_version_snapshot(project_id: str, version_id: str, request: Request):
    _user, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        v = await db.get(TPVersion, uuid.UUID(version_id))
        if v is None or v.plan_id is None:
            raise HTTPException(status_code=404, detail="Version not found")
        return JSONResponse({"version_number": v.version_number, "snapshot": v.snapshot})


# ----------------------------------------------------------------------------
# Writes — single action endpoint reusing run_action
# ----------------------------------------------------------------------------
@router.post("/api/projects/{project_id}/tracking-plan/action")
async def api_action(project_id: str, payload: ActionPayload, request: Request):
    user_uuid, proj_id, role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        branch = await get_main_branch(db, plan)
        ctx = _Ctx(role=role, user_id=str(user_uuid), project_id=str(proj_id), plan=plan)
        result = await run_action(db, branch, ctx, payload.action, payload.params)
        if not result.get("error"):
            await db.commit()
        else:
            await db.rollback()
        status = 200 if not result.get("error") else _status_for(result["error_type"])
        return JSONResponse(result, status_code=status)


def _status_for(error_type: str) -> int:
    return {
        "validation_failed": 422,
        "conflict": 409,
        "not_found": 404,
        "permission_denied": 403,
        "missing_param": 400,
        "unknown_action": 400,
    }.get(error_type, 400)
```

- [ ] **Step 5: Register the router in `app/main.py`**

Near the existing `from app.api.sdr_routes import router as sdr_router` (line ~421) add:
```python
from app.api.tracking_plan_routes import router as tracking_plan_router
```
Near `app.include_router(sdr_router)` (line ~442) add:
```python
app.include_router(tracking_plan_router)
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/api/test_tracking_plan_routes.py -v`
Expected: PASS. (Create `tests/api/__init__.py` if missing.)

- [ ] **Step 7: Format, lint, commit**

```bash
ruff format app/api/tracking_plan_routes.py app/main.py tests/api/test_tracking_plan_routes.py
ruff check app/api/tracking_plan_routes.py
git add app/api/tracking_plan_routes.py app/main.py tests/api/
git commit -m "feat(tracking-plan): HTTP API (plan/validate/versions + action endpoint)"
```

---

### Task 2: The editing UI page

**Files:**
- Create: `app/templates/tracking_plan.html`
- Test: page smoke test (added to `tests/api/test_tracking_plan_routes.py`)

The page boots a small vanilla-JS app: it reads `window.__TP_PROJECT_ID__`, fetches the plan, and renders tabbed, editable tables. A generic `tpAction(action, params)` posts to the action endpoint and re-renders.

- [ ] **Step 1: Write the template**

```html
{# app/templates/tracking_plan.html #}
{% extends "base.html" %}
{% block title %}Tracking Plan — Fluxito{% endblock %}
{% set active = 'tracking_plan' %}

{% block head_extra %}
<style>
  .tp-wrap { padding: 1.5rem; max-width: 1200px; margin: 0 auto; }
  .tp-head { display:flex; align-items:center; justify-content:space-between; gap:1rem; margin-bottom:1rem; }
  .tp-tabs { display:flex; gap:.25rem; border-bottom:1px solid var(--border, #2a2a2a); margin-bottom:1rem; flex-wrap:wrap; }
  .tp-tab { padding:.5rem .9rem; cursor:pointer; border:none; background:none; color:inherit; opacity:.65; }
  .tp-tab.is-active { opacity:1; border-bottom:2px solid var(--accent, #4a9eff); }
  .tp-panel { display:none; } .tp-panel.is-active { display:block; }
  .tp-table { width:100%; border-collapse:collapse; font-size:.9rem; }
  .tp-table th, .tp-table td { text-align:left; padding:.5rem; border-bottom:1px solid var(--border,#2a2a2a); vertical-align:top; }
  .tp-row-actions button { margin-right:.4rem; }
  .tp-banner { padding:.6rem .9rem; border-radius:6px; margin-bottom:1rem; font-size:.85rem; }
  .tp-banner.warn { background:#3a2a00; } .tp-banner.ok { background:#0a2a14; }
  .tp-form { display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; margin:.75rem 0; }
  .tp-form input, .tp-form select { padding:.4rem; }
  .tp-pill { font-size:.7rem; padding:.1rem .4rem; border-radius:99px; background:#222; }
  dialog.tp-modal { width:min(680px,92vw); border:1px solid var(--border,#333); border-radius:10px; background:var(--bg,#111); color:inherit; }
  .tp-muted { opacity:.6; }
</style>
{% endblock %}

{% block main %}
<div class="tp-wrap">
  <div class="tp-head">
    <h1 style="margin:0;">Tracking Plan</h1>
    <div>
      <button class="btn" onclick="tpValidate()">Validate</button>
      <button class="btn btn-primary" onclick="tpPublish()">Publish</button>
    </div>
  </div>
  <div id="tp-banner"></div>

  <div class="tp-tabs">
    <button class="tp-tab is-active" data-tab="events" onclick="tpTab('events')">Events</button>
    <button class="tp-tab" data-tab="properties" onclick="tpTab('properties')">Properties</button>
    <button class="tp-tab" data-tab="sources" onclick="tpTab('sources')">Sources</button>
    <button class="tp-tab" data-tab="destinations" onclick="tpTab('destinations')">Destinations</button>
    <button class="tp-tab" data-tab="metrics" onclick="tpTab('metrics')">Metrics</button>
    <button class="tp-tab" data-tab="versions" onclick="tpTab('versions')">Versions</button>
  </div>

  <div class="tp-panel is-active" data-panel="events"><div id="tp-events"></div></div>
  <div class="tp-panel" data-panel="properties"><div id="tp-properties"></div></div>
  <div class="tp-panel" data-panel="sources"><div id="tp-sources"></div></div>
  <div class="tp-panel" data-panel="destinations"><div id="tp-destinations"></div></div>
  <div class="tp-panel" data-panel="metrics"><div id="tp-metrics"></div></div>
  <div class="tp-panel" data-panel="versions"><div id="tp-versions"></div></div>
</div>

<dialog class="tp-modal" id="tp-event-modal"><div id="tp-event-detail"></div>
  <div style="padding:.75rem; text-align:right;"><button class="btn" onclick="document.getElementById('tp-event-modal').close()">Close</button></div>
</dialog>

<script>
window.__TP_PROJECT_ID__ = "{{ project_id }}";
</script>
<script>
(function () {
  const PID = window.__TP_PROJECT_ID__;
  const BASE = `/api/projects/${PID}/tracking-plan`;
  let PLAN = null;

  function esc(s){ return (s==null?'':String(s)).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
  function banner(msg, kind){ const b=document.getElementById('tp-banner'); b.innerHTML = msg ? `<div class="tp-banner ${kind||'warn'}">${esc(msg)}</div>` : ''; }

  async function getPlan(){ const r = await fetch(BASE); PLAN = await r.json(); render(); }

  async function tpAction(action, params){
    const r = await fetch(`${BASE}/action`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action, params})});
    const j = await r.json();
    if (j.error){ banner(`${j.error_type}: ${j.message}`, 'warn'); return null; }
    banner('', 'ok');
    await getPlan();
    return j;
  }
  window.tpAction = tpAction;

  window.tpTab = function(name){
    document.querySelectorAll('.tp-tab').forEach(t=>t.classList.toggle('is-active', t.dataset.tab===name));
    document.querySelectorAll('.tp-panel').forEach(p=>p.classList.toggle('is-active', p.dataset.panel===name));
    if (name==='versions') loadVersions();
  };

  window.tpValidate = async function(){
    const r = await fetch(`${BASE}/validate`); const v = await r.json();
    const warns = v.findings.filter(f=>f.severity==='warning').length;
    banner(`Validation: ${v.findings.length} findings (${warns} warnings). ${v.is_publishable?'Publishable.':'Resolve warnings before publish.'}`, warns?'warn':'ok');
  };

  window.tpPublish = async function(){
    const note = prompt('Changelog for this version:'); if (note===null) return;
    const j = await tpAction('publish', {changelog: note});
    if (j) banner(`Published version ${j.version_number}.`, 'ok');
  };

  function table(cols, rows, rowFn){
    return `<table class="tp-table"><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(rowFn).join('')}</tbody></table>`;
  }

  // ---- Events ----
  function renderEvents(){
    const opts = PLAN.categories.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
    const rows = table(['Name','Category','Sources','Destinations','Props',''], PLAN.events, e=>`
      <tr>
        <td><a href="#" onclick="tpOpenEvent('${e.id}');return false;">${esc(e.name)}</a><div class="tp-muted">${esc(e.purpose||'')}</div></td>
        <td>${esc(e.category||'')}</td>
        <td>${e.sources.map(s=>`<span class="tp-pill">${esc(s.name)}:${esc(s.implementation_status)}</span>`).join(' ')||'<span class="tp-muted">—</span>'}</td>
        <td>${e.destinations.map(d=>esc(d.destination)).join(', ')||'<span class="tp-muted">—</span>'}</td>
        <td>${e.properties.length}</td>
        <td class="tp-row-actions"><button class="btn" onclick="tpDeleteEvent('${e.id}','${esc(e.name)}')">Delete</button></td>
      </tr>`);
    document.getElementById('tp-events').innerHTML = `
      <div class="tp-form">
        <input id="tp-new-event" placeholder="event name"/>
        <select id="tp-new-event-cat"><option value="">(no category)</option>${opts}</select>
        <button class="btn btn-primary" onclick="tpCreateEvent()">Add event</button>
        <input id="tp-new-cat" placeholder="new category"/>
        <button class="btn" onclick="tpCreateCategory()">Add category</button>
      </div>${rows}`;
  }
  window.tpCreateEvent = function(){
    const name = document.getElementById('tp-new-event').value.trim(); if(!name) return;
    const cat = document.getElementById('tp-new-event-cat').value || undefined;
    tpAction('create_event', {name, category_id: cat});
  };
  window.tpDeleteEvent = function(id,name){ if(confirm(`Delete event "${name}"?`)) tpAction('delete_event', {event_id:id}); };
  window.tpCreateCategory = function(){ const name=document.getElementById('tp-new-cat').value.trim(); if(name) tpAction('create_category',{name}); };

  // ---- Event detail (attach props, scope sources, map destinations) ----
  window.tpOpenEvent = function(id){
    const e = PLAN.events.find(x=>x.id===id); if(!e) return;
    const libProps = PLAN.properties.event.map(p=>`<option value="${p.id}">${esc(p.name)} (${esc(p.data_type)})</option>`).join('');
    const srcOpts = PLAN.sources.map(s=>`<label style="margin-right:.6rem;"><input type="checkbox" value="${s.id}" data-src> ${esc(s.name)}</label>`).join('');
    const destOpts = PLAN.destinations.map(d=>`<option value="${d.id}">${esc(d.name)}</option>`).join('');
    document.getElementById('tp-event-detail').innerHTML = `
      <div style="padding:1rem;">
        <h2>${esc(e.name)}</h2>
        <h4>Properties</h4>
        ${table(['Name','Type','Required','Example',''], e.properties, p=>`
          <tr><td>${esc(p.name)}</td><td>${esc(p.data_type)}</td><td>${p.required?'yes':'no'}</td><td>${esc(p.example||'')}</td>
          <td><button class="btn" onclick="tpDetach('${id}','${esc(p.name)}')">Remove</button></td></tr>`)}
        <div class="tp-form">
          <select id="tp-attach-prop">${libProps}</select>
          <label><input type="checkbox" id="tp-attach-req"> required</label>
          <input id="tp-attach-ex" placeholder="example"/>
          <button class="btn" onclick="tpAttach('${id}')">Attach property</button>
        </div>
        <h4>Sources & status</h4>
        <div class="tp-form">${srcOpts}
          <select id="tp-src-status"><option>planned</option><option>implemented</option><option>verified</option><option>deprecated</option></select>
          <button class="btn" onclick="tpScopeSources('${id}')">Set sources</button>
        </div>
        <h4>Destination mappings</h4>
        ${table(['Destination','Dest event name',''], e.destinations, d=>`
          <tr><td>${esc(d.destination)}</td><td>${esc(d.dest_event_name||'')}</td>
          <td><button class="btn" onclick="tpUnmap('${id}','${esc(d.destination)}')">Remove</button></td></tr>`)}
        <div class="tp-form">
          <select id="tp-map-dest">${destOpts}</select>
          <input id="tp-map-name" placeholder="dest event name"/>
          <button class="btn" onclick="tpMap('${id}')">Map destination</button>
        </div>
      </div>`;
    document.getElementById('tp-event-modal').showModal();
  };
  window.tpAttach = function(eid){
    const pid=document.getElementById('tp-attach-prop').value; if(!pid) return;
    tpAction('attach_property', {event_id:eid, property_id:pid, required:document.getElementById('tp-attach-req').checked, example:document.getElementById('tp-attach-ex').value||null}).then(()=>tpOpenEvent(eid));
  };
  window.tpDetach = function(eid,pname){ const p=PLAN.properties.event.find(x=>x.name===pname); if(p) tpAction('detach_property',{event_id:eid,property_id:p.id}).then(()=>tpOpenEvent(eid)); };
  window.tpScopeSources = function(eid){
    const chosen=[...document.querySelectorAll('[data-src]:checked')].map(c=>c.value);
    const status=document.getElementById('tp-src-status').value;
    tpAction('set_event_sources', {event_id:eid, sources: chosen.map(s=>({source_id:s, implementation_status:status}))}).then(()=>tpOpenEvent(eid));
  };
  window.tpMap = function(eid){ const did=document.getElementById('tp-map-dest').value; if(did) tpAction('set_event_destination',{event_id:eid,destination_id:did,dest_event_name:document.getElementById('tp-map-name').value||null}).then(()=>tpOpenEvent(eid)); };
  window.tpUnmap = function(eid,dname){ const d=PLAN.destinations.find(x=>x.name===dname); if(d) tpAction('remove_event_destination',{event_id:eid,destination_id:d.id}).then(()=>tpOpenEvent(eid)); };

  // ---- Properties ----
  function renderProperties(){
    const rows = table(['Name','Kind','Type','PII',''], [...PLAN.properties.event, ...PLAN.properties.user], p=>`
      <tr><td>${esc(p.name)}</td><td>${esc(p.kind)}</td><td>${esc(p.data_type)}</td><td>${p.is_pii?'yes':''}</td>
      <td><button class="btn" onclick="tpDeleteProp('${p.id}','${esc(p.name)}')">Delete</button></td></tr>`);
    document.getElementById('tp-properties').innerHTML = `
      <div class="tp-form">
        <input id="tp-prop-name" placeholder="property name"/>
        <select id="tp-prop-type"><option>string</option><option>int</option><option>float</option><option>boolean</option><option>object</option><option>array</option></select>
        <select id="tp-prop-kind"><option>event</option><option>user</option><option>group</option><option>system</option></select>
        <button class="btn btn-primary" onclick="tpCreateProp()">Add property</button>
      </div>${rows}`;
  }
  window.tpCreateProp = function(){
    const name=document.getElementById('tp-prop-name').value.trim(); if(!name) return;
    tpAction('create_property', {name, data_type:document.getElementById('tp-prop-type').value, kind:document.getElementById('tp-prop-kind').value});
  };
  window.tpDeleteProp = function(id,name){ if(confirm(`Delete property "${name}"?`)) tpAction('delete_property',{property_id:id}); };

  // ---- Sources (+ routing) ----
  function renderSources(){
    const destOpts = PLAN.destinations.map(d=>`<option value="${d.id}">${esc(d.name)}</option>`).join('');
    const rows = table(['Name','Type','Routes to',''], PLAN.sources, s=>`
      <tr><td>${esc(s.name)}</td><td>${esc(s.platform_type||'')}</td><td>${s.destinations.map(esc).join(', ')||'<span class="tp-muted">—</span>'}</td>
      <td class="tp-row-actions">
        <select id="route-${s.id}">${destOpts}</select>
        <button class="btn" onclick="tpRoute('${s.id}')">Route</button>
        <button class="btn" onclick="tpDeleteSource('${s.id}','${esc(s.name)}')">Delete</button>
      </td></tr>`);
    document.getElementById('tp-sources').innerHTML = `
      <div class="tp-form"><input id="tp-src-name" placeholder="source name"/>
        <input id="tp-src-type" placeholder="platform_type (web/ios/...)"/>
        <button class="btn btn-primary" onclick="tpCreateSource()">Add source</button></div>${rows}`;
  }
  window.tpCreateSource = function(){ const name=document.getElementById('tp-src-name').value.trim(); if(name) tpAction('create_source',{name, platform_type:document.getElementById('tp-src-type').value||null}); };
  window.tpDeleteSource = function(id,name){ if(confirm(`Delete source "${name}"?`)) tpAction('delete_source',{source_id:id}); };
  window.tpRoute = function(sid){ const did=document.getElementById('route-'+sid).value; if(did) tpAction('connect_source_destination',{source_id:sid,destination_id:did}); };

  // ---- Destinations ----
  function renderDestinations(){
    const rows = table(['Name','Platform','Account',''], PLAN.destinations, d=>`
      <tr><td>${esc(d.name)}</td><td>${esc(d.platform)}</td><td>${esc(d.platform_account_id||'')}</td>
      <td><button class="btn" onclick="tpDeleteDest('${d.id}','${esc(d.name)}')">Delete</button></td></tr>`);
    document.getElementById('tp-destinations').innerHTML = `
      <div class="tp-form"><input id="tp-dest-name" placeholder="destination name"/>
        <input id="tp-dest-platform" placeholder="platform (ga4/amplitude/...)"/>
        <input id="tp-dest-acct" placeholder="account id"/>
        <button class="btn btn-primary" onclick="tpCreateDest()">Add destination</button></div>${rows}`;
  }
  window.tpCreateDest = function(){
    const name=document.getElementById('tp-dest-name').value.trim(); const platform=document.getElementById('tp-dest-platform').value.trim();
    if(name&&platform) tpAction('create_destination',{name, platform, platform_account_id:document.getElementById('tp-dest-acct').value||null});
  };
  window.tpDeleteDest = function(id,name){ if(confirm(`Delete destination "${name}"?`)) tpAction('delete_destination',{destination_id:id}); };

  // ---- Metrics ----
  function renderMetrics(){
    const evOpts = PLAN.events.map(e=>`<option value="${e.id}">${esc(e.name)}</option>`).join('');
    const rows = table(['Name','Type','Event',''], PLAN.metrics, m=>`
      <tr><td>${esc(m.name)}</td><td>${esc(m.type)}</td><td>${esc(m.event||'')}</td>
      <td><button class="btn" onclick="tpDeleteMetric('${m.id}','${esc(m.name)}')">Delete</button></td></tr>`);
    document.getElementById('tp-metrics').innerHTML = `
      <div class="tp-form"><input id="tp-metric-name" placeholder="metric name"/>
        <select id="tp-metric-type"><option>count</option><option>sum</option><option>unique</option><option>average</option><option>ratio</option></select>
        <select id="tp-metric-event"><option value="">(no event)</option>${evOpts}</select>
        <button class="btn btn-primary" onclick="tpCreateMetric()">Add metric</button></div>${rows}`;
  }
  window.tpCreateMetric = function(){
    const name=document.getElementById('tp-metric-name').value.trim(); if(!name) return;
    tpAction('create_metric',{name, type:document.getElementById('tp-metric-type').value, event_id:document.getElementById('tp-metric-event').value||null});
  };
  window.tpDeleteMetric = function(id,name){ if(confirm(`Delete metric "${name}"?`)) tpAction('delete_metric',{metric_id:id}); };

  // ---- Versions ----
  async function loadVersions(){
    const r = await fetch(`${BASE}/versions`); const v = await r.json();
    document.getElementById('tp-versions').innerHTML = table(['Version','Changelog','Published'], v.versions, x=>`
      <tr><td>${esc(x.version_number)}</td><td>${esc(x.changelog||'')}</td><td>${esc(x.published_at||'')}</td></tr>`)
      || '<p class="tp-muted">No versions published yet.</p>';
  }

  function render(){
    renderEvents(); renderProperties(); renderSources(); renderDestinations(); renderMetrics();
  }

  getPlan();
})();
</script>
{% endblock %}
```

> If `base.html` doesn't define `.btn`/`.btn-primary`, either add minimal styles to the `head_extra` block or reuse whatever button classes `app.css` provides (check an existing page like `integrations` first).

- [ ] **Step 2: Add a page smoke test**

Append to `tests/api/test_tracking_plan_routes.py`:

```python
@pytest.mark.anyio
async def test_page_renders(client):
    r = await client.get("/tracking-plan")
    assert r.status_code == 200
    assert "Tracking Plan" in r.text
    assert "window.__TP_PROJECT_ID__" in r.text
```

- [ ] **Step 3: Run to verify pass**

Run: `python -m pytest tests/api/test_tracking_plan_routes.py -v`
Expected: PASS.

- [ ] **Step 4: Manual check (optional but recommended)**

Start the app (`./start.sh` or the project's run command), sign in, select a project, visit `/tracking-plan`. Add an event, a property, attach it, add a source/destination, route them, scope the event, map a destination, validate, publish. Confirm each persists on reload.

- [ ] **Step 5: Format, commit**

```bash
git add app/templates/tracking_plan.html tests/api/test_tracking_plan_routes.py
git commit -m "feat(tracking-plan): structured editing UI"
```

---

### Task 3: Navigation link

**Files:**
- Modify: `app/templates/base.html`

- [ ] **Step 1: Add the nav item**

In `base.html`, find the "Knowledge" nav group containing the "Solution Design" link (the SDR page). Add a sibling link to the tracking plan, mirroring the existing link markup exactly (same classes, `active` check pattern):

```html
<a href="/tracking-plan" class="nav-link {{ 'is-active' if active == 'tracking_plan' else '' }}">Tracking Plan</a>
```

(Match the surrounding links' exact classes/structure — copy a neighbor and change the href/label/active key.)

- [ ] **Step 2: Smoke-check**

Run: `python -m pytest tests/api/test_tracking_plan_routes.py -v` (page still renders), and visually confirm the nav link appears + highlights on `/tracking-plan`.

- [ ] **Step 3: Format, commit**

```bash
git add app/templates/base.html
git commit -m "feat(tracking-plan): nav link"
```

---

## Self-Review (against spec §8)

- **Plan overview** (events grouped, status badges, validate banner) → Task 2 Events tab + validate. ✅ (Category grouping is shown via the Category column; richer grouping is cosmetic.)
- **Event editor** (identity, attached properties, source scoping + per-source status, destination mappings) → Task 2 event modal. ✅
- **Property library** (create/edit, types) → Task 2 Properties tab. (Constraint editors for enum/regex/min-max are a thin follow-up — `constraints` is editable via MCP today; the UI exposes name/type/kind. Flag as a UI polish item.) ✅/⚠️
- **Sources (+ routing)**, **Destinations**, **Metrics**, **Versions** → Task 2 tabs. ✅
- **Publish (admin)** → action endpoint enforces role via `run_action`; UI Publish button. ✅
- **All UI calls the same service** as MCP (via `run_action`) → Task 1 action endpoint. ✅
- **Placeholder scan:** the two implementer notes (match membership-model fields; match button classes) are concrete "match the real thing" instructions, not code TODOs. ✅
- **Name consistency:** JS action names + param keys match `run_action`'s field pickers (`set_event_sources` takes `sources:[{source_id, implementation_status}]`; `set_event_destination` takes `destination_id`/`dest_event_name`; etc.). ✅
