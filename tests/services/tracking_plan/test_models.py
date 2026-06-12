# tests/services/tracking_plan/test_models.py
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.tracking_plan import TPBranch, TPEvent, TPPlan


async def _make_project_and_user(session):
    """Insert a minimal project + user to satisfy FKs; return (project_id, user_id)."""
    from app.models.project import Project
    from app.models.user import User

    user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com")
    session.add(user)
    await session.flush()
    project = Project(name="P", slug=f"p-{uuid.uuid4().hex[:8]}", owner_id=user.id)
    session.add(project)
    await session.flush()
    return project.id, user.id


@pytest.mark.anyio
async def test_plan_event_unique_name_per_branch(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)

        plan = TPPlan(project_id=project_id, name="Plan", created_by=user_id)
        session.add(plan)
        await session.flush()
        branch = TPBranch(plan_id=plan.id, name="main", is_main=True, created_by=user_id)
        session.add(branch)
        await session.flush()

        session.add(TPEvent(plan_id=plan.id, branch_id=branch.id, name="purchase"))
        await session.flush()

        # Duplicate event name on the same branch must violate the unique constraint
        session.add(TPEvent(plan_id=plan.id, branch_id=branch.id, name="purchase"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.anyio
async def test_one_plan_per_project(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        session.add(TPPlan(project_id=project_id, name="A", created_by=user_id))
        await session.flush()
        session.add(TPPlan(project_id=project_id, name="B", created_by=user_id))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
