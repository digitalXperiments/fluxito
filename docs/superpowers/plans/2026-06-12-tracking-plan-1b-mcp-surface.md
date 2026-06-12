# Tracking Plan Revamp — Plan 1B: MCP Surface

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the structured tracking-plan service (Plan 1A) to the AI through the existing `tracking_plan` MCP meta-tool — read/validate/CRUD/publish actions — plus a `publish_branch` service capability. Old markdown actions keep working until Plan 1D's cutover.

**Architecture:** A single new legacy tool `tracking_plan_v2` is registered and wired into the `tracking_plan` unified dispatcher via new action→route entries. The tool resolves the active project + user + `main` branch, then calls a thin, fully-testable `run_action(session, branch, ctx, action, params)` core that delegates to the Plan-1A service functions. Self-describing specs are added to `tracking_plan.json`.

**Tech Stack:** Python 3.12, the project's MCP tool framework (`@mcp_server.tool`, unified dispatcher in `app/tools/unified.py`), the spec engine (`app/tools/specs/`), SQLAlchemy async, pytest.

**Prerequisite:** Plan 1A merged (the `app/services/tracking_plan/` package + `tp_*` tables exist).

**Scope note (deliberate):** `scan_source` (connector-backed event discovery) is **deferred** to a fast-follow — it is the only action coupled to connector internals and to `sdr_bootstrap` code that Plan 1D deletes. The AI builds the plan via validated CRUD; reality-surfacing via connector scan lands once the scan logic is moved out of `sdr_bootstrap`. Flag this to the user.

---

## Conventions

Same as Plan 1A (repo-root commands; `ruff format`/`ruff check` after edits; Postgres+Redis up; tests use `db_session_factory`). Reference patterns confirmed in the codebase:
- Active project: `require_project_ctx()` → `ProjectContext` with `.project_id`, `.role`, `.project_name` (from `app.auth.mcp_session_manager`); `no_active_project_response()` for the None case.
- Active user: `state.current_user_ctx.get()` → object with `.user_id`.
- DB session: `state.db_session_factory()` async context manager (`import app.app_state as state`).
- Admin gate: `_ADMIN_ROLES = frozenset(("owner", "admin"))`; check `ctx.role not in _ADMIN_ROLES`.
- Error shape: `{"error": True, "error_type": "...", "message": "...", "instructions_for_claude": "..."}`.
- Registration entrypoint: `app/tools/registry.py` calls `register_*_tools(mcp_server)` then `rewire_unified_surface(mcp_server)` last.
- Unified routes: `TRACKING_PLAN_ROUTES` in `app/tools/unified.py`; `_make_dispatcher` calls `legacy_tool.run(call_args)` with `call_args["action"]` set from the route's `legacy_action`.

---

## File Structure

**Create:**
- `app/services/tracking_plan/publish.py` — `publish_branch`, `_next_version_number`.
- `app/tools/tracking_plan_tools.py` — `register_tracking_plan_tools(mcp_server)` + `run_action(...)` core + a `_Ctx` struct.
- `tests/services/tracking_plan/test_publish.py`
- `tests/tools/test_tracking_plan_tools.py`

**Modify:**
- `app/services/tracking_plan/__init__.py` — re-export `publish_branch`.
- `app/tools/unified.py` — add `tracking_plan_v2` action routes to `TRACKING_PLAN_ROUTES`.
- `app/tools/registry.py` — call `register_tracking_plan_tools(mcp_server)`.
- `app/tools/specs/data/tracking_plan.json` — add the new action specs.

---

### Task 1: `publish_branch` service capability

**Files:**
- Create: `app/services/tracking_plan/publish.py`
- Modify: `app/services/tracking_plan/__init__.py`
- Test: `tests/services/tracking_plan/test_publish.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/tracking_plan/test_publish.py
import pytest

from app.models.tracking_plan import TPVersion
from app.services.tracking_plan import create_event, get_main_branch, get_or_create_plan
from app.services.tracking_plan.publish import _next_version_number, publish_branch
from tests.services.tracking_plan.test_models import _make_project_and_user


def test_next_version_number():
    assert _next_version_number(None) == "1.0"
    assert _next_version_number("1.0") == "1.1"
    assert _next_version_number("2.7") == "2.8"
    assert _next_version_number("garbage") == "1.0"


@pytest.mark.anyio
async def test_publish_snapshots_and_sets_current(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)
        await create_event(session, branch, name="purchase")

        version = await publish_branch(session, plan, branch, user_id=user_id, changelog="first")
        assert version.version_number == "1.0"
        assert plan.current_version_id == version.id
        assert version.snapshot["events"][0]["name"] == "purchase"

        v2 = await publish_branch(session, plan, branch, user_id=user_id)
        assert v2.version_number == "1.1"
        assert plan.current_version_id == v2.id

        from sqlalchemy import func, select

        n = await session.scalar(select(func.count()).select_from(TPVersion).where(TPVersion.plan_id == plan.id))
        assert n == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/services/tracking_plan/test_publish.py -v`
Expected: FAIL (`app.services.tracking_plan.publish` missing).

- [ ] **Step 3: Implement publish**

```python
# app/services/tracking_plan/publish.py
"""Publish a branch as an immutable version snapshot (JSONB plan_to_dict)."""

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import TPBranch, TPPlan, TPVersion

from .common import coerce_uuid
from .serializer import plan_to_dict


def _next_version_number(latest: str | None) -> str:
    """Minor-bump versioning. '1.0' if none/garbage; else major.(minor+1)."""
    if not latest:
        return "1.0"
    parts = latest.split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return "1.0"
    return f"{major}.{minor + 1}"


async def publish_branch(
    session: AsyncSession, plan: TPPlan, branch: TPBranch, *, user_id: Any, changelog: str | None = None
) -> TPVersion:
    """Snapshot the branch into a new immutable version and point the plan at it."""
    latest = (
        await session.execute(
            select(TPVersion.version_number)
            .where(TPVersion.plan_id == plan.id)
            .order_by(desc(TPVersion.published_at))
        )
    ).scalars().first()

    snapshot = await plan_to_dict(session, plan, branch)
    version = TPVersion(
        plan_id=plan.id,
        branch_id=branch.id,
        version_number=_next_version_number(latest),
        snapshot=snapshot,
        changelog=changelog,
        published_by=coerce_uuid(user_id),
    )
    session.add(version)
    await session.flush()
    plan.current_version_id = version.id
    await session.flush()
    return version


async def latest_snapshot_for_project(session: AsyncSession, project_id: Any) -> dict | None:
    """Return the most-recently-published snapshot dict for a project, or None.
    Used by downstream consumers (audit, tag testing) in Plan 1D."""
    plan = (
        await session.execute(select(TPPlan).where(TPPlan.project_id == coerce_uuid(project_id)))
    ).scalar_one_or_none()
    if plan is None or plan.current_version_id is None:
        return None
    version = await session.get(TPVersion, plan.current_version_id)
    return version.snapshot if version is not None else None
```

- [ ] **Step 4: Re-export in `__init__.py`**

Add to `app/services/tracking_plan/__init__.py`:
```python
from .publish import latest_snapshot_for_project, publish_branch
```
Add `"publish_branch", "latest_snapshot_for_project",` to `__all__`.

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/services/tracking_plan/test_publish.py -v`
Expected: PASS.

- [ ] **Step 6: Format, lint, commit**

```bash
ruff format app/services/tracking_plan/ tests/services/tracking_plan/test_publish.py
ruff check app/services/tracking_plan/
git add app/services/tracking_plan/ tests/services/tracking_plan/test_publish.py
git commit -m "feat(tracking-plan): publish_branch version snapshots"
```

---

### Task 2: The `tracking_plan_v2` tool + `run_action` core

**Files:**
- Create: `app/tools/tracking_plan_tools.py`
- Test: `tests/tools/test_tracking_plan_tools.py`

`run_action` is the testable core: it takes an already-resolved `(session, branch, ctx, action, params)` and returns a result dict, mapping service exceptions to the standard error shape. The registered tool is a thin wrapper that resolves ctx/user/session/branch.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_tracking_plan_tools.py
from types import SimpleNamespace

import pytest

from app.services.tracking_plan import get_main_branch, get_or_create_plan
from app.tools.tracking_plan_tools import run_action
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _ctx_branch(session, role="admin"):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    branch = await get_main_branch(session, plan)
    ctx = SimpleNamespace(role=role, user_id=str(user_id), project_id=str(project_id), plan=plan)
    return ctx, branch


@pytest.mark.anyio
async def test_crud_roundtrip_via_run_action(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        r = await run_action(session, branch, ctx, "create_event", {"name": "purchase", "purpose": "money"})
        assert r["ok"] is True
        event_id = r["id"]

        r = await run_action(session, branch, ctx, "create_property", {"name": "value", "data_type": "float"})
        prop_id = r["id"]

        r = await run_action(session, branch, ctx, "attach_property", {"event_id": event_id, "property_id": prop_id, "required": True})
        assert r["ok"] is True

        plan = await run_action(session, branch, ctx, "get_plan", {})
        assert plan["events"][0]["name"] == "purchase"
        assert plan["events"][0]["properties"][0]["name"] == "value"

        report = await run_action(session, branch, ctx, "validate", {})
        assert "findings" in report


@pytest.mark.anyio
async def test_errors_map_to_error_dicts(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)
        await run_action(session, branch, ctx, "create_event", {"name": "purchase"})
        dup = await run_action(session, branch, ctx, "create_event", {"name": "purchase"})
        assert dup["error"] is True
        assert dup["error_type"] == "conflict"

        bad = await run_action(session, branch, ctx, "create_property", {"name": "x", "data_type": "nope"})
        assert bad["error"] is True
        assert bad["error_type"] == "validation_failed"

        unknown = await run_action(session, branch, ctx, "frobnicate", {})
        assert unknown["error"] is True
        assert unknown["error_type"] == "unknown_action"


@pytest.mark.anyio
async def test_publish_requires_admin(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session, role="member")
        await run_action(session, branch, ctx, "create_event", {"name": "purchase"})
        denied = await run_action(session, branch, ctx, "publish", {})
        assert denied["error"] is True
        assert denied["error_type"] == "permission_denied"

        ctx.role = "owner"
        ok = await run_action(session, branch, ctx, "publish", {"changelog": "v1"})
        assert ok["ok"] is True
        assert ok["version_number"] == "1.0"
```

Create the empty package marker:
```python
# tests/tools/__init__.py
```
(Only if it does not already exist — check with `ls tests/tools/__init__.py` first.)

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/tools/test_tracking_plan_tools.py -v`
Expected: FAIL (`app.tools.tracking_plan_tools` missing).

- [ ] **Step 3: Implement the tool + core**

```python
# app/tools/tracking_plan_tools.py
"""MCP surface for the structured tracking plan.

Registers a single `tracking_plan_v2` legacy tool wired into the `tracking_plan`
unified dispatcher (see TRACKING_PLAN_ROUTES in app/tools/unified.py). The tool
resolves the active project/user/main-branch, then delegates to `run_action`, a
thin testable router over the Plan-1A service functions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import app.app_state as state
from app.auth.mcp_session_manager import no_active_project_response, require_project_ctx
from app.services.tracking_plan import (
    attach_property,
    connect_source_destination,
    create_category,
    create_destination,
    create_event,
    create_metric,
    create_property,
    create_source,
    delete_category,
    delete_destination,
    delete_event,
    delete_metric,
    delete_property,
    delete_source,
    detach_property,
    disconnect_source_destination,
    get_main_branch,
    get_or_create_plan,
    plan_to_dict,
    publish_branch,
    remove_event_destination,
    set_event_destination,
    set_event_sources,
    update_category,
    update_destination,
    update_event,
    update_metric,
    update_property,
    update_source,
    validate_plan,
)
from app.services.tracking_plan.exceptions import (
    ConflictError,
    NotFoundError,
    TrackingPlanError,
    ValidationError,
)

logger = logging.getLogger(__name__)

_ADMIN_ROLES = frozenset(("owner", "admin"))


@dataclass
class _Ctx:
    role: str
    user_id: str
    project_id: str
    plan: Any  # TPPlan


def _ok(**kw: Any) -> dict:
    return {"ok": True, **kw}


def _err(error_type: str, message: str) -> dict:
    return {"error": True, "error_type": error_type, "message": message}


async def run_action(session, branch, ctx: _Ctx, action: str, params: dict) -> dict:
    """Route one structured action to the service. Pure over (session, branch,
    ctx) — the registered tool supplies these. Maps service errors to the
    standard MCP error shape."""
    p = params or {}
    try:
        # ---- reads -------------------------------------------------------
        if action == "get_plan":
            data = await plan_to_dict(session, ctx.plan, branch)
            if p.get("summary"):
                return {
                    "plan": data["plan"],
                    "counts": {
                        "events": len(data["events"]),
                        "event_properties": len(data["properties"]["event"]),
                        "user_properties": len(data["properties"]["user"]),
                        "sources": len(data["sources"]),
                        "destinations": len(data["destinations"]),
                        "metrics": len(data["metrics"]),
                    },
                }
            return data

        if action == "get_event":
            data = await plan_to_dict(session, ctx.plan, branch)
            name = p.get("name")
            eid = str(p["event_id"]) if p.get("event_id") else None
            for ev in data["events"]:
                if (name and ev["name"] == name) or (eid and ev["id"] == eid):
                    return ev
            return _err("not_found", f"event {name or eid} not found")

        if action == "validate":
            return await validate_plan(session, ctx.plan, branch)

        # ---- events ------------------------------------------------------
        if action == "create_event":
            ev = await create_event(session, branch, name=p.get("name", ""), **_event_fields(p))
            return _ok(id=str(ev.id), name=ev.name)
        if action == "update_event":
            ev = await update_event(session, branch, p["event_id"], **_event_fields(p))
            return _ok(id=str(ev.id), name=ev.name)
        if action == "delete_event":
            await delete_event(session, branch, p["event_id"])
            return _ok(deleted=str(p["event_id"]))
        if action == "set_event_sources":
            links = await set_event_sources(session, branch, p["event_id"], p.get("sources", []))
            return _ok(event_id=str(p["event_id"]), source_count=len(links))
        if action == "set_event_destination":
            m = await set_event_destination(
                session, branch, p["event_id"], p["destination_id"], **_event_dest_fields(p)
            )
            return _ok(id=str(m.id))
        if action == "remove_event_destination":
            await remove_event_destination(session, branch, p["event_id"], p["destination_id"])
            return _ok(removed=True)

        # ---- properties --------------------------------------------------
        if action == "create_property":
            pr = await create_property(
                session,
                branch,
                name=p.get("name", ""),
                data_type=p.get("data_type", ""),
                kind=p.get("kind", "event"),
                description=p.get("description"),
                constraints=p.get("constraints"),
                is_pii=bool(p.get("is_pii", False)),
                parent_property_id=p.get("parent_property_id"),
            )
            return _ok(id=str(pr.id), name=pr.name, kind=pr.kind)
        if action == "update_property":
            pr = await update_property(session, branch, p["property_id"], **_property_fields(p))
            return _ok(id=str(pr.id), name=pr.name)
        if action == "delete_property":
            await delete_property(session, branch, p["property_id"])
            return _ok(deleted=str(p["property_id"]))
        if action == "attach_property":
            link = await attach_property(
                session,
                p["event_id"],
                p["property_id"],
                required=bool(p.get("required", False)),
                example=p.get("example"),
                override_description=p.get("override_description"),
                sort_order=int(p.get("sort_order", 0)),
            )
            return _ok(id=str(link.id))
        if action == "detach_property":
            await detach_property(session, p["event_id"], p["property_id"])
            return _ok(detached=True)

        # ---- categories --------------------------------------------------
        if action == "create_category":
            c = await create_category(
                session, branch, name=p.get("name", ""), description=p.get("description"), color=p.get("color")
            )
            return _ok(id=str(c.id), name=c.name)
        if action == "update_category":
            c = await update_category(session, branch, p["category_id"], **_category_fields(p))
            return _ok(id=str(c.id), name=c.name)
        if action == "delete_category":
            await delete_category(session, branch, p["category_id"])
            return _ok(deleted=str(p["category_id"]))

        # ---- sources / destinations / routing ----------------------------
        if action == "create_source":
            s = await create_source(session, branch, name=p.get("name", ""), **_source_fields(p))
            return _ok(id=str(s.id), name=s.name)
        if action == "update_source":
            s = await update_source(session, branch, p["source_id"], **_source_fields(p))
            return _ok(id=str(s.id), name=s.name)
        if action == "delete_source":
            await delete_source(session, branch, p["source_id"])
            return _ok(deleted=str(p["source_id"]))
        if action == "create_destination":
            d = await create_destination(
                session, branch, name=p.get("name", ""), platform=p.get("platform"), **_dest_fields(p)
            )
            return _ok(id=str(d.id), name=d.name)
        if action == "update_destination":
            d = await update_destination(session, branch, p["destination_id"], **_dest_fields(p))
            return _ok(id=str(d.id), name=d.name)
        if action == "delete_destination":
            await delete_destination(session, branch, p["destination_id"])
            return _ok(deleted=str(p["destination_id"]))
        if action == "connect_source_destination":
            r = await connect_source_destination(session, branch, p["source_id"], p["destination_id"])
            return _ok(id=str(r.id))
        if action == "disconnect_source_destination":
            await disconnect_source_destination(session, branch, p["source_id"], p["destination_id"])
            return _ok(disconnected=True)

        # ---- metrics -----------------------------------------------------
        if action == "create_metric":
            m = await create_metric(
                session,
                branch,
                name=p.get("name", ""),
                type=p.get("type", "count"),
                description=p.get("description"),
                event_id=p.get("event_id"),
                property_id=p.get("property_id"),
                filters=p.get("filters"),
            )
            return _ok(id=str(m.id), name=m.name)
        if action == "update_metric":
            m = await update_metric(session, branch, p["metric_id"], **_metric_fields(p))
            return _ok(id=str(m.id), name=m.name)
        if action == "delete_metric":
            await delete_metric(session, branch, p["metric_id"])
            return _ok(deleted=str(p["metric_id"]))

        # ---- publish (human gate) ---------------------------------------
        if action == "publish":
            if ctx.role not in _ADMIN_ROLES:
                return {
                    "error": True,
                    "error_type": "permission_denied",
                    "message": f"Only project admins (owner/admin) can publish. Your role is '{ctx.role}'.",
                    "instructions_for_claude": (
                        "Publishing is the human approval gate. Tell the user only an owner/admin can publish, "
                        "and that the draft is otherwise ready."
                    ),
                }
            version = await publish_branch(
                session, ctx.plan, branch, user_id=ctx.user_id, changelog=p.get("changelog")
            )
            return _ok(version_id=str(version.id), version_number=version.version_number)

        return _err("unknown_action", f"unknown tracking_plan action '{action}'")

    except ConflictError as exc:
        return _err("conflict", str(exc))
    except ValidationError as exc:
        return _err("validation_failed", str(exc))
    except NotFoundError as exc:
        return _err("not_found", str(exc))
    except TrackingPlanError as exc:
        return _err("tracking_plan_error", str(exc))
    except KeyError as exc:
        return _err("missing_param", f"missing required param: {exc}")


# --- per-entity field pickers (only forward keys the caller actually sent) ---
def _pick(p: dict, keys: tuple[str, ...]) -> dict:
    return {k: p[k] for k in keys if k in p}


def _event_fields(p: dict) -> dict:
    return _pick(p, ("name", "display_name", "description", "category_id", "tags", "trigger_type",
                     "trigger_config", "purpose", "owner_business", "owner_technical", "consent_required"))


def _event_dest_fields(p: dict) -> dict:
    return _pick(p, ("dest_event_name", "property_mappings", "enabled", "notes"))


def _property_fields(p: dict) -> dict:
    return _pick(p, ("name", "description", "data_type", "constraints", "is_pii", "parent_property_id"))


def _category_fields(p: dict) -> dict:
    return _pick(p, ("name", "description", "color"))


def _source_fields(p: dict) -> dict:
    return _pick(p, ("name", "platform_type", "description", "connector_ref"))


def _dest_fields(p: dict) -> dict:
    return _pick(p, ("name", "platform", "platform_account_id", "config"))


def _metric_fields(p: dict) -> dict:
    return _pick(p, ("name", "description", "type", "event_id", "property_id", "filters"))


def register_tracking_plan_tools(mcp_server: Any) -> None:
    """Register the structured tracking-plan tool. Wired into the unified
    `tracking_plan` dispatcher via TRACKING_PLAN_ROUTES (Task 4)."""

    @mcp_server.tool("tracking_plan_v2")
    async def tracking_plan_v2(action: str, **params: Any) -> dict:
        """Structured tracking-plan operations (events, properties, sources,
        destinations, mappings, metrics, validate, publish). Internal — invoked
        via tracking_plan(action=..., params=...)."""
        project_ctx = require_project_ctx()
        if not project_ctx:
            return no_active_project_response()
        user_ctx = state.current_user_ctx.get()
        if not user_ctx:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}

        async with state.db_session_factory() as session:
            plan = await get_or_create_plan(
                session, project_id=project_ctx.project_id, user_id=user_ctx.user_id
            )
            branch = await get_main_branch(session, plan)
            ctx = _Ctx(
                role=project_ctx.role,
                user_id=str(user_ctx.user_id),
                project_id=str(project_ctx.project_id),
                plan=plan,
            )
            result = await run_action(session, branch, ctx, action, dict(params))
            if not result.get("error"):
                await session.commit()
            return result
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/tools/test_tracking_plan_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
ruff format app/tools/tracking_plan_tools.py tests/tools/test_tracking_plan_tools.py
ruff check app/tools/tracking_plan_tools.py
git add app/tools/tracking_plan_tools.py tests/tools/test_tracking_plan_tools.py tests/tools/__init__.py
git commit -m "feat(tracking-plan): tracking_plan_v2 MCP tool + run_action core"
```

---

### Task 3: Register the tool

**Files:**
- Modify: `app/tools/registry.py`
- Test: (covered by Task 4's dispatcher test)

- [ ] **Step 1: Add the registration call**

In `app/tools/registry.py`, next to the existing `from app.tools.sdr_tools import register_sdr_tools` / `register_sdr_tools(mcp_server)` (around line 718-720), add:

```python
from app.tools.tracking_plan_tools import register_tracking_plan_tools

register_tracking_plan_tools(mcp_server)
```

Place this BEFORE the final `rewire_unified_surface(mcp_server)` call (so the legacy tool exists when the dispatcher is wired).

- [ ] **Step 2: Smoke-check import**

Run: `python -c "import app.tools.registry"`
Expected: no ImportError.

- [ ] **Step 3: Commit**

```bash
ruff format app/tools/registry.py
git add app/tools/registry.py
git commit -m "feat(tracking-plan): register tracking_plan_v2 tool"
```

---

### Task 4: Wire actions into the `tracking_plan` dispatcher + specs

**Files:**
- Modify: `app/tools/unified.py`
- Modify: `app/tools/specs/data/tracking_plan.json`
- Test: `tests/tools/test_tracking_plan_dispatch.py`

- [ ] **Step 1: Add routes**

In `app/tools/unified.py`, extend `TRACKING_PLAN_ROUTES` (lines ~256-265). Keep the existing markdown actions; append the structured ones, all pointing at the new tool with the action name as `legacy_action` (the dispatcher sets `call_args["action"]`):

```python
    # --- Structured (v2) actions — Plan 1B ---
    "get_plan": ("tracking_plan_v2", "get_plan"),
    "get_event": ("tracking_plan_v2", "get_event"),
    "validate": ("tracking_plan_v2", "validate"),
    "create_event": ("tracking_plan_v2", "create_event"),
    "update_event": ("tracking_plan_v2", "update_event"),
    "delete_event": ("tracking_plan_v2", "delete_event"),
    "set_event_sources": ("tracking_plan_v2", "set_event_sources"),
    "set_event_destination": ("tracking_plan_v2", "set_event_destination"),
    "remove_event_destination": ("tracking_plan_v2", "remove_event_destination"),
    "create_property": ("tracking_plan_v2", "create_property"),
    "update_property": ("tracking_plan_v2", "update_property"),
    "delete_property": ("tracking_plan_v2", "delete_property"),
    "attach_property": ("tracking_plan_v2", "attach_property"),
    "detach_property": ("tracking_plan_v2", "detach_property"),
    "create_category": ("tracking_plan_v2", "create_category"),
    "update_category": ("tracking_plan_v2", "update_category"),
    "delete_category": ("tracking_plan_v2", "delete_category"),
    "create_source": ("tracking_plan_v2", "create_source"),
    "update_source": ("tracking_plan_v2", "update_source"),
    "delete_source": ("tracking_plan_v2", "delete_source"),
    "create_destination": ("tracking_plan_v2", "create_destination"),
    "update_destination": ("tracking_plan_v2", "update_destination"),
    "delete_destination": ("tracking_plan_v2", "delete_destination"),
    "connect_source_destination": ("tracking_plan_v2", "connect_source_destination"),
    "disconnect_source_destination": ("tracking_plan_v2", "disconnect_source_destination"),
    "create_metric": ("tracking_plan_v2", "create_metric"),
    "update_metric": ("tracking_plan_v2", "update_metric"),
    "delete_metric": ("tracking_plan_v2", "delete_metric"),
    "publish": ("tracking_plan_v2", "publish"),
```

> Note: the dispatcher passes `params` straight through and sets `call_args["action"]` = the route's `legacy_action`. Because `tracking_plan_v2(action, **params)` takes `action` as its first kwarg, this lines up exactly.

- [ ] **Step 2: Add specs to `tracking_plan.json`**

Append action objects to the JSON array. Use the same structure as existing entries (`tool`, `action`, `summary`, `params[]`, `returns`). Add at minimum these (representative entries shown — write one per new action, mirroring the param names used in `run_action`):

```json
{
  "tool": "tracking_plan",
  "action": "get_plan",
  "summary": "Return the full structured tracking plan for the active project's main branch (events, property library, sources, destinations, routing, mappings, metrics). Pass summary=true for counts only.",
  "params": [
    { "name": "summary", "type": "boolean", "required": false, "example": false, "doc": "If true, return counts instead of the full plan." }
  ],
  "mutates": false,
  "returns": "{ plan, branch, categories[], events[], properties{event[],user[],group[],system[]}, sources[], destinations[], metrics[] } | { plan, counts }"
},
{
  "tool": "tracking_plan",
  "action": "create_event",
  "summary": "Create an event on the working draft (main branch). Validated against the schema.",
  "params": [
    { "name": "name", "type": "string", "required": true, "example": "purchase", "doc": "Unique event name on the branch." },
    { "name": "display_name", "type": "string", "required": false, "doc": "Human-friendly label." },
    { "name": "description", "type": "string", "required": false },
    { "name": "category_id", "type": "string", "required": false, "doc": "UUID of an existing category." },
    { "name": "tags", "type": "array", "item_type": "string", "required": false },
    { "name": "trigger_type", "type": "string", "required": false },
    { "name": "trigger_config", "type": "object", "required": false },
    { "name": "purpose", "type": "string", "required": false }
  ],
  "mutates": true,
  "reversible": true,
  "returns": "{ ok: true, id, name } | { error, error_type, message }"
},
{
  "tool": "tracking_plan",
  "action": "create_property",
  "summary": "Create a reusable library property (event/user/group/system). Enum constraints go in constraints.allowed_values (non-empty list).",
  "params": [
    { "name": "name", "type": "string", "required": true, "example": "currency" },
    { "name": "data_type", "type": "string", "required": true, "enum": ["string","int","float","boolean","object","array"] },
    { "name": "kind", "type": "string", "required": false, "enum": ["event","user","group","system"], "example": "event" },
    { "name": "description", "type": "string", "required": false },
    { "name": "constraints", "type": "object", "required": false, "doc": "{ allowed_values?: [...], regex?, min?, max?, format? }" },
    { "name": "is_pii", "type": "boolean", "required": false },
    { "name": "parent_property_id", "type": "string", "required": false, "doc": "Parent UUID for object/array members." }
  ],
  "mutates": true,
  "returns": "{ ok: true, id, name, kind } | { error, ... }"
},
{
  "tool": "tracking_plan",
  "action": "publish",
  "summary": "Publish the working draft as an immutable version snapshot. Requires project admin (owner/admin) — this is the human approval gate.",
  "params": [
    { "name": "changelog", "type": "string", "required": false, "doc": "Human-readable summary of this version." }
  ],
  "mutates": true,
  "scope": "admin",
  "returns": "{ ok: true, version_id, version_number } | { error: permission_denied }"
}
```

Write analogous entries for the remaining actions (`get_event`, `validate`, `update_event`, `delete_event`, `set_event_sources`, `set_event_destination`, `remove_event_destination`, `update_property`, `delete_property`, `attach_property`, `detach_property`, `create/update/delete_category`, `create/update/delete_source`, `create/update/delete_destination`, `connect/disconnect_source_destination`, `create/update/delete_metric`) — each lists exactly the params consumed in `run_action`’s field pickers above. Validate the file parses: `python -c "import json; json.load(open('app/tools/specs/data/tracking_plan.json'))"`.

- [ ] **Step 3: Write the dispatcher integration test**

```python
# tests/tools/test_tracking_plan_dispatch.py
import json
from pathlib import Path

from app.tools.unified import TRACKING_PLAN_ROUTES


def test_new_actions_are_routed():
    for action in ["get_plan", "create_event", "create_property", "attach_property", "publish"]:
        assert action in TRACKING_PLAN_ROUTES
        tool, legacy_action = TRACKING_PLAN_ROUTES[action]
        assert tool == "tracking_plan_v2"
        assert legacy_action == action


def test_tracking_plan_specs_parse_and_cover_new_actions():
    data = json.loads(Path("app/tools/specs/data/tracking_plan.json").read_text())
    actions = {entry["action"] for entry in data}
    for action in ["get_plan", "create_event", "create_property", "publish"]:
        assert action in actions
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/tools/test_tracking_plan_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole new suite + lint**

Run:
```bash
python -m pytest tests/services/tracking_plan/ tests/tools/ -v
ruff check app/tools/ app/services/tracking_plan/ tests/tools/
ruff format --check app/tools/tracking_plan_tools.py app/services/tracking_plan/publish.py
```
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add app/tools/unified.py app/tools/specs/data/tracking_plan.json tests/tools/test_tracking_plan_dispatch.py
git commit -m "feat(tracking-plan): wire structured actions into tracking_plan dispatcher + specs"
```

---

## Self-Review (against spec §6)

- **Reads** `get_plan`/`get_event`/`validate` (spec §6 Reads) → Task 2. ✅
- **Write CRUD** for events/properties/attach-detach/categories/sources/destinations/routing/event-sources/event-destinations/metrics (spec §6 Writes) → Task 2 `run_action`. ✅
- **Batch** — `attach_property`/`set_event_sources` accept arrays; broader `batch` envelope is a thin future add (each action is already idempotent). Noted, not a Phase-1 blocker. ✅
- **Publish human gate** (spec §6) → admin-role check in Task 2 + `publish_branch` service Task 1. ✅
- **Self-describing specs** (spec §6) → Task 4 JSON. ✅
- **Single meta-tool** (decision #8) → one `tracking_plan_v2` legacy tool behind the `tracking_plan` dispatcher. ✅
- **`scan_source`** (spec §6) → **deferred** (documented at top). ⚠️ Flag to user.
- **Placeholder scan:** field-picker helpers + per-action specs are concrete; the "write analogous entries" instruction in Task 4 Step 2 lists the exact actions + says to mirror the consumed params (concrete, not a TODO). ✅
- **Name consistency:** action strings in `run_action`, `TRACKING_PLAN_ROUTES`, and the dispatch test match. ✅
