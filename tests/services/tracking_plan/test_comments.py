# tests/services/tracking_plan/test_comments.py
"""Service-layer tests for branch-scoped comment threads."""

import pytest

from app.services.tracking_plan import (
    add_comment,
    create_event,
    delete_comment,
    get_main_branch,
    get_or_create_plan,
    list_comments,
    resolve_comment,
)
from app.services.tracking_plan.branches import create_branch
from app.services.tracking_plan.exceptions import ValidationError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _setup(session):
    """Return (plan, branch, event) on main."""
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    branch = await get_main_branch(session, plan)
    ev = await create_event(session, branch, name="purchase")
    return plan, branch, ev, user_id


@pytest.mark.anyio
async def test_add_comment_and_list(db_session_factory):
    """Add a comment on an event; list_comments returns it."""
    async with db_session_factory() as session:
        _plan, branch, ev, user_id = await _setup(session)

        c = await add_comment(
            session,
            branch,
            entity_type="event",
            entity_id=ev.id,
            author_id=user_id,
            body="This event is missing the currency property.",
        )
        assert c.id is not None
        assert c.body == "This event is missing the currency property."
        assert c.resolved is False

        comments = await list_comments(session, branch, entity_type="event", entity_id=ev.id)
        assert len(comments) == 1
        assert comments[0].id == c.id


@pytest.mark.anyio
async def test_threaded_reply_and_ordering(db_session_factory):
    """A reply with parent_id appears; both returned ordered by created_at."""
    async with db_session_factory() as session:
        _plan, branch, ev, user_id = await _setup(session)

        root = await add_comment(
            session,
            branch,
            entity_type="event",
            entity_id=ev.id,
            author_id=user_id,
            body="Root comment",
        )
        reply = await add_comment(
            session,
            branch,
            entity_type="event",
            entity_id=ev.id,
            author_id=user_id,
            body="Reply to root",
            parent_id=root.id,
        )

        comments = await list_comments(session, branch, entity_type="event", entity_id=ev.id)
        assert len(comments) == 2
        # root comes first (ordered by created_at asc)
        assert comments[0].id == root.id
        assert comments[1].id == reply.id
        assert comments[1].parent_id == root.id


@pytest.mark.anyio
async def test_blank_body_raises_validation_error(db_session_factory):
    """Blank body raises ValidationError."""
    async with db_session_factory() as session:
        _plan, branch, ev, user_id = await _setup(session)

        with pytest.raises(ValidationError):
            await add_comment(
                session,
                branch,
                entity_type="event",
                entity_id=ev.id,
                author_id=user_id,
                body="   ",
            )


@pytest.mark.anyio
async def test_bad_entity_type_raises_validation_error(db_session_factory):
    """Unknown entity_type raises ValidationError."""
    async with db_session_factory() as session:
        _plan, branch, ev, user_id = await _setup(session)

        with pytest.raises(ValidationError):
            await add_comment(
                session,
                branch,
                entity_type="foobar",
                entity_id=ev.id,
                author_id=user_id,
                body="Valid body",
            )


@pytest.mark.anyio
async def test_resolve_comment(db_session_factory):
    """resolve_comment sets resolved=True."""
    async with db_session_factory() as session:
        _plan, branch, ev, user_id = await _setup(session)

        c = await add_comment(
            session,
            branch,
            entity_type="event",
            entity_id=ev.id,
            author_id=user_id,
            body="Needs to be resolved.",
        )
        assert c.resolved is False

        updated = await resolve_comment(session, c.id, resolved=True)
        assert updated.resolved is True


@pytest.mark.anyio
async def test_delete_comment_cascades_replies(db_session_factory):
    """Deleting a root comment cascades to its replies (count → 0)."""
    async with db_session_factory() as session:
        _plan, branch, ev, user_id = await _setup(session)

        root = await add_comment(
            session,
            branch,
            entity_type="event",
            entity_id=ev.id,
            author_id=user_id,
            body="Root",
        )
        await add_comment(
            session,
            branch,
            entity_type="event",
            entity_id=ev.id,
            author_id=user_id,
            body="Reply",
            parent_id=root.id,
        )

        comments_before = await list_comments(session, branch, entity_type="event", entity_id=ev.id)
        assert len(comments_before) == 2

        await delete_comment(session, root.id)

        comments_after = await list_comments(session, branch, entity_type="event", entity_id=ev.id)
        assert len(comments_after) == 0


@pytest.mark.anyio
async def test_comments_are_branch_scoped(db_session_factory):
    """Comments on a feature branch are not visible on main."""
    async with db_session_factory() as session:
        plan, main_branch, ev, user_id = await _setup(session)

        feat_branch = await create_branch(
            session,
            plan,
            name="feat/comments-scope",
            user_id=user_id,
        )

        # Create an event on the feature branch to comment on
        feat_ev = await create_event(session, feat_branch, name="feat_event")

        await add_comment(
            session,
            feat_branch,
            entity_type="event",
            entity_id=feat_ev.id,
            author_id=user_id,
            body="Comment on feature branch only",
        )

        # main branch list should be empty
        main_comments = await list_comments(session, main_branch)
        assert len(main_comments) == 0

        # feature branch list should have the comment
        feat_comments = await list_comments(session, feat_branch)
        assert len(feat_comments) == 1
        assert feat_comments[0].body == "Comment on feature branch only"
