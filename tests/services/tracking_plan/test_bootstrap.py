# tests/services/tracking_plan/test_bootstrap.py
import pytest

from app.models.tracking_plan import TPBranch
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from tests.services.tracking_plan.test_models import _make_project_and_user


@pytest.mark.anyio
async def test_get_or_create_plan_is_idempotent_and_makes_main_branch(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)

        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id, name="My Plan")
        assert plan.name == "My Plan"
        assert plan.default_branch_id is not None

        branch = await get_main_branch(session, plan)
        assert branch.is_main is True
        assert branch.name == "main"
        assert plan.default_branch_id == branch.id

        # Second call returns the same plan (idempotent), creates no second branch
        plan2 = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        assert plan2.id == plan.id
        from sqlalchemy import func, select

        count = await session.scalar(
            select(func.count()).select_from(TPBranch).where(TPBranch.plan_id == plan.id)
        )
        assert count == 1
