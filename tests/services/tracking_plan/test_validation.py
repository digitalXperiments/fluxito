import pytest

from app.services.tracking_plan import (
    create_event,
    create_property,
    create_source,
    set_event_sources,
    validate_plan,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from tests.services.tracking_plan.test_models import _make_project_and_user


@pytest.mark.anyio
async def test_validate_plan_flags_gaps(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)

        ev = await create_event(session, branch, name="purchase")  # no source, no dest, no props
        await create_property(session, branch, name="orphan", data_type="string")  # unused

        report = await validate_plan(session, plan, branch)
        codes = {f["code"] for f in report["findings"]}
        assert "event_no_source" in codes
        assert "event_no_destination" in codes
        assert "event_no_properties" in codes
        assert "unused_property" in codes
        assert report["counts"]["events"] == 1

        # Once the event has a source, that finding clears
        src = await create_source(session, branch, name="web")
        await set_event_sources(session, branch, ev.id, [{"source_id": src.id}])
        report2 = await validate_plan(session, plan, branch)
        assert "event_no_source" not in {f["code"] for f in report2["findings"]}
