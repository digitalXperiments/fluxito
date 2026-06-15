# tests/services/tracking_plan/test_members.py
"""Tests for object-property member operations (tp_property_members link table)."""

import pytest

from app.services.tracking_plan import create_property
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import NotFoundError, ValidationError
from app.services.tracking_plan.properties import (
    add_member,
    create_and_link_member,
    remove_member,
    reorder_members,
)
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_add_member_happy_path(db_session_factory):
    """Linking a scalar property as a member of an object property succeeds."""
    async with db_session_factory() as session:
        branch = await _branch(session)
        obj_prop = await create_property(session, branch, name="address", data_type="object")
        street = await create_property(session, branch, name="street", data_type="string")

        link = await add_member(session, branch, obj_prop.id, street.id, required=True, sort_order=0)
        assert link.parent_property_id == obj_prop.id
        assert link.member_property_id == street.id
        assert link.required is True
        assert link.sort_order == 0


@pytest.mark.anyio
async def test_add_member_idempotent(db_session_factory):
    """Re-adding an existing member updates required/sort_order in place (no duplicate)."""
    async with db_session_factory() as session:
        branch = await _branch(session)
        obj_prop = await create_property(session, branch, name="cart", data_type="object")
        total = await create_property(session, branch, name="total", data_type="float")

        link1 = await add_member(session, branch, obj_prop.id, total.id, required=False, sort_order=0)
        link2 = await add_member(session, branch, obj_prop.id, total.id, required=True, sort_order=5)

        assert link1.id == link2.id  # same row updated
        assert link2.required is True
        assert link2.sort_order == 5


@pytest.mark.anyio
async def test_add_member_rejects_non_object_parent(db_session_factory):
    """Members can only be added to data_type='object' properties."""
    async with db_session_factory() as session:
        branch = await _branch(session)
        scalar = await create_property(session, branch, name="amount", data_type="float")
        other = await create_property(session, branch, name="currency", data_type="string")

        with pytest.raises(ValidationError, match="data_type='object'"):
            await add_member(session, branch, scalar.id, other.id)


@pytest.mark.anyio
async def test_add_member_rejects_self_membership(db_session_factory):
    """A property cannot be a member of itself."""
    async with db_session_factory() as session:
        branch = await _branch(session)
        obj_prop = await create_property(session, branch, name="nested", data_type="object")

        with pytest.raises(ValidationError, match="itself"):
            await add_member(session, branch, obj_prop.id, obj_prop.id)


@pytest.mark.anyio
async def test_add_member_rejects_cycle(db_session_factory):
    """Linking properties in a cycle (A → B → A) must be rejected."""
    async with db_session_factory() as session:
        branch = await _branch(session)
        a = await create_property(session, branch, name="A", data_type="object")
        b = await create_property(session, branch, name="B", data_type="object")

        # A contains B
        await add_member(session, branch, a.id, b.id)

        # B contains A would create a cycle — must be rejected.
        with pytest.raises(ValidationError, match="cycle"):
            await add_member(session, branch, b.id, a.id)


@pytest.mark.anyio
async def test_add_member_rejects_offbranch_member(db_session_factory):
    """add_member with a member_property_id that does not exist on the branch raises NotFoundError."""
    import uuid

    async with db_session_factory() as session:
        branch = await _branch(session)
        obj_prop = await create_property(session, branch, name="container", data_type="object")

        with pytest.raises(NotFoundError):
            await add_member(session, branch, obj_prop.id, uuid.uuid4())


@pytest.mark.anyio
async def test_remove_member(db_session_factory):
    """Removing a member unlinks it without deleting either property from the library."""
    from sqlalchemy import select

    from app.models.tracking_plan import TPProperty, TPPropertyMember

    async with db_session_factory() as session:
        branch = await _branch(session)
        obj_prop = await create_property(session, branch, name="order", data_type="object")
        item = await create_property(session, branch, name="item_id", data_type="string")
        await add_member(session, branch, obj_prop.id, item.id)

        await remove_member(session, branch, obj_prop.id, item.id)

        # Link is gone.
        link = (
            await session.execute(
                select(TPPropertyMember).where(
                    TPPropertyMember.parent_property_id == obj_prop.id,
                    TPPropertyMember.member_property_id == item.id,
                )
            )
        ).scalar_one_or_none()
        assert link is None

        # Both properties still exist in the library.
        assert (await session.get(TPProperty, obj_prop.id)) is not None
        assert (await session.get(TPProperty, item.id)) is not None


@pytest.mark.anyio
async def test_remove_member_not_found(db_session_factory):
    """Removing a non-existent member link raises NotFoundError."""
    import uuid

    async with db_session_factory() as session:
        branch = await _branch(session)
        obj_prop = await create_property(session, branch, name="wrapper", data_type="object")

        with pytest.raises(NotFoundError):
            await remove_member(session, branch, obj_prop.id, uuid.uuid4())


@pytest.mark.anyio
async def test_reorder_members(db_session_factory):
    """reorder_members reassigns sort_order to match the supplied id order."""
    async with db_session_factory() as session:
        branch = await _branch(session)
        obj_prop = await create_property(session, branch, name="product", data_type="object")
        name_p = await create_property(session, branch, name="prod_name", data_type="string")
        price_p = await create_property(session, branch, name="prod_price", data_type="float")
        qty_p = await create_property(session, branch, name="prod_qty", data_type="integer")

        await add_member(session, branch, obj_prop.id, name_p.id, sort_order=0)
        await add_member(session, branch, obj_prop.id, price_p.id, sort_order=1)
        await add_member(session, branch, obj_prop.id, qty_p.id, sort_order=2)

        # Reverse the order.
        updated = await reorder_members(session, branch, obj_prop.id, [qty_p.id, price_p.id, name_p.id])
        assert len(updated) == 3

        order = {lk.member_property_id: lk.sort_order for lk in updated}
        assert order[qty_p.id] == 0
        assert order[price_p.id] == 1
        assert order[name_p.id] == 2


@pytest.mark.anyio
async def test_reorder_members_unknown_id_raises(db_session_factory):
    """reorder_members raises NotFoundError for an id that is not a member."""
    import uuid

    async with db_session_factory() as session:
        branch = await _branch(session)
        obj_prop = await create_property(session, branch, name="thing", data_type="object")
        child = await create_property(session, branch, name="child_val", data_type="string")
        await add_member(session, branch, obj_prop.id, child.id)

        with pytest.raises(NotFoundError):
            await reorder_members(session, branch, obj_prop.id, [child.id, uuid.uuid4()])


@pytest.mark.anyio
async def test_create_and_link_member(db_session_factory):
    """create_and_link_member creates a new library property and links it in one call."""
    async with db_session_factory() as session:
        branch = await _branch(session)
        obj_prop = await create_property(session, branch, name="shipping", data_type="object")

        new_prop, link = await create_and_link_member(
            session,
            branch,
            obj_prop.id,
            name="zip_code",
            data_type="string",
            required=True,
            sort_order=0,
        )

        assert new_prop.name == "zip_code"
        assert new_prop.data_type == "string"
        assert link.parent_property_id == obj_prop.id
        assert link.member_property_id == new_prop.id
        assert link.required is True
