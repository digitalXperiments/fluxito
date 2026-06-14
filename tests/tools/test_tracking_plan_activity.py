from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.tracking_plan import TPActivity
from app.services.tracking_plan import get_main_branch, get_or_create_plan
from app.tools.tracking_plan_tools import run_action
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _ctx_branch(session, role="admin"):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    branch = await get_main_branch(session, plan)
    ctx = SimpleNamespace(role=role, user_id=str(user_id), project_id=str(project_id), plan=plan)
    return ctx, branch, plan


async def _rows(session, plan):
    stmt = select(TPActivity).where(TPActivity.plan_id == plan.id).order_by(TPActivity.created_at)
    return list((await session.execute(stmt)).scalars().all())


@pytest.mark.anyio
async def test_create_event_logs_one_activity(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch, plan = await _ctx_branch(session)
        r = await run_action(session, branch, ctx, "create_event", {"name": "purchase"})
        rows = await _rows(session, plan)
        assert len(rows) == 1
        assert rows[0].entity_type == "event"
        assert str(rows[0].entity_id) == r["id"]
        assert rows[0].action == "create_event"
        assert rows[0].branch_id == branch.id
        assert "event" in rows[0].summary


@pytest.mark.anyio
async def test_reads_do_not_log(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch, plan = await _ctx_branch(session)
        await run_action(session, branch, ctx, "get_plan", {})
        await run_action(session, branch, ctx, "validate", {})
        assert await _rows(session, plan) == []


@pytest.mark.anyio
async def test_update_logs_entity_id_from_params(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch, plan = await _ctx_branch(session)
        r = await run_action(session, branch, ctx, "create_event", {"name": "purchase"})
        await run_action(session, branch, ctx, "update_event", {"event_id": r["id"], "purpose": "x"})
        rows = await _rows(session, plan)
        assert [x.action for x in rows] == ["create_event", "update_event"]
        assert str(rows[1].entity_id) == r["id"]


@pytest.mark.anyio
async def test_comment_actions_not_logged_as_activity(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch, plan = await _ctx_branch(session)
        r = await run_action(session, branch, ctx, "create_event", {"name": "purchase"})
        await run_action(
            session,
            branch,
            ctx,
            "add_comment",
            {"entity_type": "event", "entity_id": r["id"], "body": "hi"},
        )
        rows = await _rows(session, plan)
        assert [x.action for x in rows] == ["create_event"]
