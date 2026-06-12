# tests/services/tracking_plan/test_routing.py
import pytest

from app.services.tracking_plan import (
    connect_source_destination,
    create_destination,
    create_source,
    disconnect_source_destination,
    update_destination,
    update_source,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError, ValidationError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_source_destination_crud_and_routing(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)

        src = await create_source(session, branch, name="web", platform_type="web")
        assert src.platform_type == "web"
        with pytest.raises(ConflictError):
            await create_source(session, branch, name="web")

        dest = await create_destination(session, branch, name="GA4", platform="ga4")
        with pytest.raises(ValidationError):
            await create_destination(session, branch, name="bad")  # platform required

        route = await connect_source_destination(session, branch, src.id, dest.id)
        assert route.source_id == src.id
        # Idempotent
        route2 = await connect_source_destination(session, branch, src.id, dest.id)
        assert route2.id == route.id

        await disconnect_source_destination(session, branch, src.id, dest.id)
        from sqlalchemy import func, select

        from app.models.tracking_plan import TPSourceDestination

        n = await session.scalar(select(func.count()).select_from(TPSourceDestination))
        assert n == 0

        await update_source(session, branch, src.id, description="primary web")
        await update_destination(session, branch, dest.id, platform_account_id="G-123")
