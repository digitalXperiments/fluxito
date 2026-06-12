import pytest

from app.services.tracking_plan import create_category, delete_category, update_category
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError, NotFoundError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_category_crud(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)

        cat = await create_category(session, branch, name="Commerce", color="#0af")
        assert cat.name == "Commerce"

        with pytest.raises(ConflictError):
            await create_category(session, branch, name="Commerce")

        updated = await update_category(session, branch, cat.id, description="Buy flow")
        assert updated.description == "Buy flow"

        await delete_category(session, branch, cat.id)
        with pytest.raises(NotFoundError):
            await update_category(session, branch, cat.id, name="X")
