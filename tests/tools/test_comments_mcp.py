# tests/tools/test_comments_mcp.py
"""MCP run_action tests for the comment actions (branch-scoped)."""

from types import SimpleNamespace

import pytest

from app.services.tracking_plan import get_main_branch, get_or_create_plan
from app.services.tracking_plan.branches import get_branch
from app.tools.tracking_plan_tools import run_action
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _ctx_branch(session, role="admin"):
    """Return (ctx, main_branch) for a fresh project."""
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    branch = await get_main_branch(session, plan)
    ctx = SimpleNamespace(role=role, user_id=str(user_id), project_id=str(project_id), plan=plan)
    return ctx, branch


@pytest.mark.anyio
async def test_add_and_list_comment(db_session_factory):
    """add_comment returns ok+id; list_comments returns it."""
    async with db_session_factory() as session:
        ctx, main = await _ctx_branch(session)

        # Create an event to comment on
        ev_r = await run_action(session, main, ctx, "create_event", {"name": "checkout"})
        assert ev_r["ok"] is True
        event_id = ev_r["id"]

        # Add a comment
        cr = await run_action(
            session,
            main,
            ctx,
            "add_comment",
            {"entity_type": "event", "entity_id": event_id, "body": "Needs a currency property."},
        )
        assert cr["ok"] is True
        comment_id = cr["id"]
        assert comment_id

        # List comments — should find it
        lr = await run_action(
            session,
            main,
            ctx,
            "list_comments",
            {"entity_type": "event", "entity_id": event_id},
        )
        assert "comments" in lr
        assert len(lr["comments"]) == 1
        assert lr["comments"][0]["id"] == comment_id
        assert lr["comments"][0]["body"] == "Needs a currency property."


@pytest.mark.anyio
async def test_branch_scoped_comments(db_session_factory):
    """A comment on a feature branch does NOT appear in list_comments on main."""
    async with db_session_factory() as session:
        ctx, main = await _ctx_branch(session)

        # Create an event on main
        ev_r = await run_action(session, main, ctx, "create_event", {"name": "signup"})
        main_event_id = ev_r["id"]

        # Create a feature branch
        br = await run_action(session, main, ctx, "create_branch", {"name": "feat/comment-scope"})
        feat_branch = await get_branch(session, ctx.plan, br["id"])

        # Create a parallel event on the feature branch to comment on
        feat_ev_r = await run_action(session, feat_branch, ctx, "create_event", {"name": "signup_feat"})
        feat_event_id = feat_ev_r["id"]

        # Add a comment on the feature branch
        await run_action(
            session,
            feat_branch,
            ctx,
            "add_comment",
            {"entity_type": "event", "entity_id": feat_event_id, "body": "Feature branch comment"},
        )

        # list_comments on main must NOT include the feature-branch comment
        main_lr = await run_action(session, main, ctx, "list_comments", {})
        assert all(
            c["body"] != "Feature branch comment" for c in main_lr.get("comments", [])
        ), "Feature-branch comment leaked into main"

        # list_comments on feature branch MUST include it
        feat_lr = await run_action(session, feat_branch, ctx, "list_comments", {})
        bodies = [c["body"] for c in feat_lr.get("comments", [])]
        assert "Feature branch comment" in bodies
