# tests/services/tracking_plan/test_metrics.py
import pytest

from app.services.tracking_plan import create_event, create_metric, delete_metric, update_metric
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError, ValidationError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_metric_crud(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        ev = await create_event(session, branch, name="purchase")

        m = await create_metric(session, branch, name="Revenue", type="sum", event_id=ev.id)
        assert m.type == "sum"
        with pytest.raises(ConflictError):
            await create_metric(session, branch, name="Revenue", type="count")
        with pytest.raises(ValidationError):
            await create_metric(session, branch, name="Bad", type="nope")

        await update_metric(session, branch, m.id, description="total money")
        await delete_metric(session, branch, m.id)
