import pytest

from app.services.tracking_plan import get_main_branch, get_or_create_plan
from app.services.tracking_plan.activity import (
    activity_to_dict,
    list_activity,
    record_activity,
)
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _plan_branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    branch = await get_main_branch(session, plan)
    return plan, branch, user_id


@pytest.mark.anyio
async def test_record_and_list_activity(db_session_factory):
    async with db_session_factory() as session:
        plan, branch, user_id = await _plan_branch(session)
        record_activity(
            session,
            plan_id=plan.id,
            branch_id=branch.id,
            entity_type="event",
            entity_id=None,
            actor_id=user_id,
            action="create_event",
            summary="created event 'purchase'",
        )
        await session.flush()
        rows = await list_activity(session, branch)
        assert len(rows) == 1
        d = activity_to_dict(rows[0])
        assert d["entity_type"] == "event"
        assert d["action"] == "create_event"
        assert d["summary"] == "created event 'purchase'"
