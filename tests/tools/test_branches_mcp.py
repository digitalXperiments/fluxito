# tests/tools/test_branches_mcp.py
"""Branch-aware run_action tests exercising the branch management actions
and verifying that entity CRUD scoped to a feature branch is isolated from main."""

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
async def test_create_branch_and_list(db_session_factory):
    """create_branch returns ok; list_branches shows main + new branch."""
    async with db_session_factory() as session:
        ctx, main = await _ctx_branch(session)

        r = await run_action(session, main, ctx, "create_branch", {"name": "feature/x"})
        assert r["ok"] is True
        assert r["name"] == "feature/x"
        branch_id = r["id"]

        r2 = await run_action(session, main, ctx, "list_branches", {})
        names = [b["name"] for b in r2["branches"]]
        assert "main" in names
        assert "feature/x" in names
        # main comes first
        assert r2["branches"][0]["is_main"] is True
        assert branch_id in [b["id"] for b in r2["branches"]]


@pytest.mark.anyio
async def test_branch_isolation(db_session_factory):
    """An event created on a feature branch does not appear on main's get_plan."""
    async with db_session_factory() as session:
        ctx, main = await _ctx_branch(session)

        # Create a feature branch
        cr = await run_action(session, main, ctx, "create_branch", {"name": "feat/isolated"})
        feat_id = cr["id"]

        # Resolve the feature branch object
        from app.services.tracking_plan.branches import get_branch

        feat_branch = await get_branch(session, ctx.plan, feat_id)

        # Create an event on the feature branch only
        await run_action(session, feat_branch, ctx, "create_event", {"name": "feat_only_event"})

        # main branch should NOT see the event
        main_plan = await run_action(session, main, ctx, "get_plan", {})
        main_event_names = [e["name"] for e in main_plan["events"]]
        assert "feat_only_event" not in main_event_names

        # feature branch SHOULD see the event
        feat_plan = await run_action(session, feat_branch, ctx, "get_plan", {})
        feat_event_names = [e["name"] for e in feat_plan["events"]]
        assert "feat_only_event" in feat_event_names


@pytest.mark.anyio
async def test_diff_shows_added_event(db_session_factory):
    """diff action shows the event added on the feature branch."""
    async with db_session_factory() as session:
        ctx, main = await _ctx_branch(session)

        cr = await run_action(session, main, ctx, "create_branch", {"name": "feat/diff-test"})
        feat_id = cr["id"]

        from app.services.tracking_plan.branches import get_branch

        feat_branch = await get_branch(session, ctx.plan, feat_id)
        await run_action(session, feat_branch, ctx, "create_event", {"name": "new_event"})

        diff = await run_action(session, main, ctx, "diff", {"head": feat_id})
        added_names = [e["name"] for e in diff["events"]["added"]]
        assert "new_event" in added_names
        assert diff["summary"]["added"] >= 1


@pytest.mark.anyio
async def test_merge_branch_requires_admin(db_session_factory):
    """merge_branch is denied for member role; succeeds for owner; main gains the event."""
    async with db_session_factory() as session:
        ctx, main = await _ctx_branch(session, role="member")

        cr = await run_action(session, main, ctx, "create_branch", {"name": "feat/merge"})
        feat_id = cr["id"]

        from app.services.tracking_plan.branches import get_branch

        feat_branch = await get_branch(session, ctx.plan, feat_id)
        await run_action(session, feat_branch, ctx, "create_event", {"name": "merged_event"})

        # member cannot merge
        denied = await run_action(session, main, ctx, "merge_branch", {"branch_id": feat_id})
        assert denied["error"] is True
        assert denied["error_type"] == "permission_denied"

        # Elevate to owner and merge
        ctx.role = "owner"
        ok = await run_action(
            session, main, ctx, "merge_branch", {"branch_id": feat_id, "changelog": "merged feat/merge"}
        )
        assert ok["ok"] is True

        # Refresh main branch object after merge (its content was replaced)
        from app.services.tracking_plan import get_main_branch

        main_refreshed = await get_main_branch(session, ctx.plan)
        main_plan = await run_action(session, main_refreshed, ctx, "get_plan", {})
        main_event_names = [e["name"] for e in main_plan["events"]]
        assert "merged_event" in main_event_names


@pytest.mark.anyio
async def test_set_review_status(db_session_factory):
    """set_review_status updates the branch and is rejected on main."""
    async with db_session_factory() as session:
        ctx, main = await _ctx_branch(session)

        cr = await run_action(session, main, ctx, "create_branch", {"name": "feat/review"})
        feat_id = cr["id"]

        r = await run_action(
            session,
            main,
            ctx,
            "set_review_status",
            {"branch_id": feat_id, "review_status": "ready_for_review"},
        )
        assert r["ok"] is True
        assert r["review_status"] == "ready_for_review"

        # Cannot set review_status on main
        from app.services.tracking_plan.branches import get_branch

        main_branch_obj = await get_branch(session, ctx.plan, "main")
        r2 = await run_action(
            session,
            main,
            ctx,
            "set_review_status",
            {"branch_id": str(main_branch_obj.id), "review_status": "approved"},
        )
        assert r2["error"] is True
        assert r2["error_type"] == "validation_failed"


@pytest.mark.anyio
async def test_abandon_branch(db_session_factory):
    """abandon_branch marks the branch abandoned; main cannot be abandoned."""
    async with db_session_factory() as session:
        ctx, main = await _ctx_branch(session)

        cr = await run_action(session, main, ctx, "create_branch", {"name": "feat/abandon"})
        feat_id = cr["id"]

        r = await run_action(session, main, ctx, "abandon_branch", {"branch_id": feat_id})
        assert r["ok"] is True

        # Verify status via get_branch
        gb = await run_action(session, main, ctx, "get_branch", {"branch_id": feat_id})
        assert gb["status"] == "abandoned"

        # Cannot abandon main
        from app.services.tracking_plan.branches import get_branch

        main_branch_obj = await get_branch(session, ctx.plan, "main")
        r2 = await run_action(session, main, ctx, "abandon_branch", {"branch_id": str(main_branch_obj.id)})
        assert r2["error"] is True
        assert r2["error_type"] == "validation_failed"
