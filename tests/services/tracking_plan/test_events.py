# tests/services/tracking_plan/test_events.py
import pytest

from app.services.tracking_plan import (
    create_destination,
    create_event,
    create_source,
    delete_event,
    remove_event_destination,
    set_event_destination,
    set_event_sources,
    update_event,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError, NotFoundError, ValidationError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_event_crud_and_uniqueness(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        ev = await create_event(session, branch, name="purchase", purpose="money")
        assert ev.purpose == "money"
        with pytest.raises(ConflictError):
            await create_event(session, branch, name="purchase")
        with pytest.raises(ValidationError):
            await create_event(session, branch, name="  ")
        ev2 = await update_event(session, branch, ev.id, display_name="Purchase")
        assert ev2.display_name == "Purchase"
        await delete_event(session, branch, ev.id)
        with pytest.raises(NotFoundError):
            await update_event(session, branch, ev.id, name="x")


@pytest.mark.anyio
async def test_set_event_sources_replaces_and_sets_status(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        ev = await create_event(session, branch, name="purchase")
        web = await create_source(session, branch, name="web")
        ios = await create_source(session, branch, name="ios")

        links = await set_event_sources(
            session, branch, ev.id, [{"source_id": web.id, "implementation_status": "implemented"}]
        )
        assert len(links) == 1
        assert links[0].implementation_status == "implemented"

        # Replacing the set drops web, adds ios with default status
        links = await set_event_sources(session, branch, ev.id, [{"source_id": ios.id}])
        assert len(links) == 1
        assert links[0].source_id == ios.id
        assert links[0].implementation_status == "planned"

        with pytest.raises(ValidationError):
            await set_event_sources(
                session, branch, ev.id, [{"source_id": ios.id, "implementation_status": "bogus"}]
            )


@pytest.mark.anyio
async def test_set_event_destination_mapping(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        ev = await create_event(session, branch, name="purchase")
        dest = await create_destination(session, branch, name="GA4 prod", platform="ga4")

        mapping = await set_event_destination(
            session, branch, ev.id, dest.id, dest_event_name="purchase", property_mappings={"value": "value"}
        )
        assert mapping.dest_event_name == "purchase"
        # Upsert: calling again updates the same row
        mapping2 = await set_event_destination(session, branch, ev.id, dest.id, enabled=False)
        assert mapping2.id == mapping.id
        assert mapping2.enabled is False

        # Removing the mapping leaves zero rows
        await remove_event_destination(session, branch, ev.id, dest.id)
        from sqlalchemy import func, select

        from app.models.tracking_plan import TPEventDestination

        n = await session.scalar(
            select(func.count()).select_from(TPEventDestination).where(TPEventDestination.event_id == ev.id)
        )
        assert n == 0


@pytest.mark.anyio
async def test_set_event_sources_rejects_duplicate_source(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        ev = await create_event(session, branch, name="signup")
        src = await create_source(session, branch, name="web")

        with pytest.raises(ValidationError):
            await set_event_sources(session, branch, ev.id, [{"source_id": src.id}, {"source_id": src.id}])
