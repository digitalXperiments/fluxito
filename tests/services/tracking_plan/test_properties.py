# tests/services/tracking_plan/test_properties.py
import pytest

from app.services.tracking_plan import (
    attach_property,
    create_event,
    create_property,
    detach_property,
    update_property,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError, NotFoundError, ValidationError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_property_create_validation(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)

        prop = await create_property(session, branch, name="currency", data_type="string")
        assert prop.kind == "event"

        with pytest.raises(ValidationError):
            await create_property(session, branch, name="bad", data_type="nope")

        with pytest.raises(ValidationError):
            await create_property(
                session, branch, name="plan_tier", data_type="string", constraints={"allowed_values": []}
            )

        # Same name allowed under a different kind, blocked under the same kind
        await create_property(session, branch, name="currency", data_type="string", kind="user")
        with pytest.raises(ConflictError):
            await create_property(session, branch, name="currency", data_type="string")


@pytest.mark.anyio
async def test_attach_detach_with_override(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        event = await create_event(session, branch, name="purchase")
        prop = await create_property(session, branch, name="value", data_type="float")

        link = await attach_property(session, branch, event.id, prop.id, required=True, example="9.99")
        assert link.required is True
        assert link.example == "9.99"

        # Re-attaching the same property updates the override rather than duplicating
        link2 = await attach_property(session, branch, event.id, prop.id, required=False)
        assert link2.id == link.id
        assert link2.required is False

        await detach_property(session, branch, event.id, prop.id)
        from sqlalchemy import func, select

        from app.models.tracking_plan import TPEventProperty

        n = await session.scalar(
            select(func.count()).select_from(TPEventProperty).where(TPEventProperty.event_id == event.id)
        )
        assert n == 0


@pytest.mark.anyio
async def test_update_property_rejects_offbranch_parent(db_session_factory):
    import uuid

    async with db_session_factory() as session:
        branch = await _branch(session)
        prop = await create_property(session, branch, name="city", data_type="string")
        with pytest.raises(NotFoundError):
            await update_property(session, branch, prop.id, parent_property_id=uuid.uuid4())
