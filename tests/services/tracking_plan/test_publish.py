# tests/services/tracking_plan/test_publish.py
import pytest

from app.models.tracking_plan import TPVersion
from app.services.tracking_plan import create_event, get_main_branch, get_or_create_plan
from app.services.tracking_plan.publish import _next_version_number, publish_branch
from tests.services.tracking_plan.test_models import _make_project_and_user


def test_next_version_number():
    assert _next_version_number(None) == "1.0"
    assert _next_version_number("1.0") == "1.1"
    assert _next_version_number("2.7") == "2.8"
    assert _next_version_number("garbage") == "1.0"


@pytest.mark.anyio
async def test_publish_snapshots_and_sets_current(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)
        await create_event(session, branch, name="purchase")

        version = await publish_branch(session, plan, branch, user_id=user_id, changelog="first")
        assert version.version_number == "1.0"
        assert plan.current_version_id == version.id
        assert version.snapshot["events"][0]["name"] == "purchase"

        v2 = await publish_branch(session, plan, branch, user_id=user_id)
        assert v2.version_number == "1.1"
        assert plan.current_version_id == v2.id

        from sqlalchemy import func, select

        n = await session.scalar(
            select(func.count()).select_from(TPVersion).where(TPVersion.plan_id == plan.id)
        )
        assert n == 2
