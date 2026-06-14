# tests/tools/test_tracking_plan_integration.py
"""End-to-end dispatch test for the structured tracking plan.

Unlike test_tracking_plan_tools.py (which calls `run_action` directly) and
test_tracking_plan_dispatch.py (which only checks the routing table + spec
JSON), this exercises the REAL dispatch path the unified `tracking_plan`
surface uses at runtime:

    tracking_plan(action=..., params=...)
        -> _make_dispatcher builds a FLAT call_args dict
        -> legacy_tool.run(call_args)      <-- the path that used to blow up
        -> _run_tracking_plan_v2 -> run_action -> service

The regression it guards: `tracking_plan_v2` used to be registered with a
`(action, **params)` signature, which FastMCP turned into a REQUIRED pydantic
`params` field. `.run({"action": "create_event", "name": "purchase"})` then
failed with `ValidationError: params: Field required` before the body ran. So
every structured call failed at runtime even though `run_action` was correct.
"""

from types import SimpleNamespace

import pytest

import app.app_state as state
from app.models.tracking_plan import TPEvent
from app.tools.tracking_plan_tools import _TrackingPlanV2Tool
from tests.services.tracking_plan.test_models import _make_project_and_user


@pytest.fixture
def tracking_plan_v2_tool():
    """The registered legacy tool object, accessed via its `.run()` contract —
    exactly how the unified dispatcher reaches it."""
    return _TrackingPlanV2Tool()


async def _setup_ctx(db_session_factory, monkeypatch, role="admin"):
    """Seed a project + user, point state.db_session_factory at the test
    factory, and set the MCP ContextVars the tool resolves project/user from.

    Returns (project_id, user_id) and a cleanup callable for the ContextVars."""
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        await session.commit()

    # The tool opens its own session via state.db_session_factory(); point it
    # at the test factory so writes land in the test transaction/DB.
    monkeypatch.setattr(state, "db_session_factory", db_session_factory)

    project_ctx = SimpleNamespace(project_id=str(project_id), role=role)
    user_ctx = SimpleNamespace(user_id=str(user_id))
    p_tok = state.current_project_ctx.set(project_ctx)
    u_tok = state.current_user_ctx.set(user_ctx)

    def _cleanup():
        state.current_project_ctx.reset(p_tok)
        state.current_user_ctx.reset(u_tok)

    return project_id, user_id, _cleanup


@pytest.mark.anyio
async def test_create_event_through_run_persists(db_session_factory, monkeypatch, tracking_plan_v2_tool):
    """A structured create_event call routed through `.run(flat_call_args)`
    succeeds AND the event is persisted. This is the exact path the
    `tracking_plan` dispatcher uses — it failed before the fix."""
    project_id, _user_id, cleanup = await _setup_ctx(db_session_factory, monkeypatch)
    try:
        # FLAT call_args, just like _make_dispatcher builds: params spread at the
        # top level with `action` injected. NO nested "params" key.
        result = await tracking_plan_v2_tool.run(
            {"action": "create_event", "name": "purchase", "purpose": "money"}
        )
    finally:
        cleanup()

    assert result.get("ok") is True, result
    assert result["name"] == "purchase"

    # The tool committed its own session — verify the row is actually in the DB.
    async with db_session_factory() as session:
        from sqlalchemy import select

        rows = (await session.execute(select(TPEvent).where(TPEvent.name == "purchase"))).scalars().all()
    assert len(rows) == 1
    assert str(rows[0].id) == result["id"]


@pytest.mark.anyio
async def test_error_action_maps_through_run(db_session_factory, monkeypatch, tracking_plan_v2_tool):
    """Error cases also map correctly through the real `.run()` path: a duplicate
    event name surfaces as a structured conflict error (no exception, no commit)."""
    _project_id, _user_id, cleanup = await _setup_ctx(db_session_factory, monkeypatch)
    try:
        first = await tracking_plan_v2_tool.run({"action": "create_event", "name": "signup"})
        assert first.get("ok") is True, first

        dup = await tracking_plan_v2_tool.run({"action": "create_event", "name": "signup"})
        assert dup.get("error") is True
        assert dup["error_type"] == "conflict"

        unknown = await tracking_plan_v2_tool.run({"action": "frobnicate"})
        assert unknown.get("error") is True
        assert unknown["error_type"] == "unknown_action"
    finally:
        cleanup()

    # The duplicate must NOT have been persisted (still exactly one "signup").
    async with db_session_factory() as session:
        from sqlalchemy import select

        rows = (await session.execute(select(TPEvent).where(TPEvent.name == "signup"))).scalars().all()
    assert len(rows) == 1


@pytest.mark.anyio
async def test_no_active_project_through_run(db_session_factory, monkeypatch, tracking_plan_v2_tool):
    """With no active project context, `.run()` returns the standard
    no_active_project error rather than raising."""
    monkeypatch.setattr(state, "db_session_factory", db_session_factory)
    p_tok = state.current_project_ctx.set(None)
    try:
        result = await tracking_plan_v2_tool.run({"action": "create_event", "name": "x"})
    finally:
        state.current_project_ctx.reset(p_tok)
    assert result.get("error") is True
    assert result["error_type"] == "no_active_project"
