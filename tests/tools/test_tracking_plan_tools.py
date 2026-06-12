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

        r = await run_action(
            session,
            branch,
            ctx,
            "attach_property",
            {"event_id": event_id, "property_id": prop_id, "required": True},
        )
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
