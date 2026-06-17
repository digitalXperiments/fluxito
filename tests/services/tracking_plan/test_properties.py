# tests/services/tracking_plan/test_properties.py
import pytest

from app.services.tracking_plan import (
    attach_property,
    create_event,
    create_property,
    detach_property,
    plan_to_dict,
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
async def test_is_list_property_serializes_true(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
        branch = await get_main_branch(session, plan)

        await create_property(session, branch, name="item_ids", data_type="string", is_list=True)
        data = await plan_to_dict(session, plan, branch)
        prop = next(p for p in data["properties"]["event"] if p["name"] == "item_ids")
        assert prop["is_list"] is True


@pytest.mark.anyio
async def test_numeric_min_greater_than_max_rejected(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        # 'int' is no longer a valid data_type; the canonical name is 'integer'.
        with pytest.raises(ValidationError):
            await create_property(
                session, branch, name="qty", data_type="integer", constraints={"min": 10, "max": 1}
            )
        # Valid min <= max is accepted.
        ok = await create_property(
            session, branch, name="qty2", data_type="integer", constraints={"min": 1, "max": 10}
        )
        assert ok.constraints == {"min": 1, "max": 10}


@pytest.mark.anyio
async def test_add_member_rejects_offbranch_member(db_session_factory):
    """add_member with a member_property_id that does not exist on the branch raises NotFoundError."""
    import uuid

    from app.services.tracking_plan.properties import add_member

    async with db_session_factory() as session:
        branch = await _branch(session)
        obj_prop = await create_property(session, branch, name="address", data_type="object")
        with pytest.raises(NotFoundError):
            await add_member(session, branch, obj_prop.id, uuid.uuid4())
