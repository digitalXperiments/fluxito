# tests/services/tracking_plan/test_bundles.py
import pytest
from sqlalchemy import func, select

from app.models.tracking_plan import TPBundleProperty, TPEventProperty
from app.services.tracking_plan import (
    add_property_to_bundle,
    attach_bundle_to_event,
    bundle_to_dict,
    create_bundle,
    create_event,
    create_property,
    delete_bundle,
    list_bundles,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from app.services.tracking_plan.exceptions import ConflictError
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _branch(session):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    return await get_main_branch(session, plan)


@pytest.mark.anyio
async def test_bundle_crud_and_serialization(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)

        bundle = await create_bundle(session, branch, name="Ecommerce Item", description="item fields")
        p1 = await create_property(session, branch, name="item_id", data_type="string")
        p2 = await create_property(session, branch, name="price", data_type="float")

        # Add two properties with required + sort_order; order is by sort_order.
        await add_property_to_bundle(session, branch, bundle.id, p2.id, required=True, sort_order=1)
        await add_property_to_bundle(session, branch, bundle.id, p1.id, required=False, sort_order=0)

        bundles = await list_bundles(session, branch)
        assert [b.name for b in bundles] == ["Ecommerce Item"]

        as_dict = await bundle_to_dict(session, bundle)
        assert as_dict["name"] == "Ecommerce Item"
        assert as_dict["description"] == "item fields"
        # ordered by sort_order: item_id (0) then price (1)
        assert [p["name"] for p in as_dict["properties"]] == ["item_id", "price"]
        assert as_dict["properties"][0]["required"] is False
        assert as_dict["properties"][1]["required"] is True
        assert as_dict["properties"][1]["data_type"] == "float"


@pytest.mark.anyio
async def test_add_property_to_bundle_is_idempotent(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        bundle = await create_bundle(session, branch, name="B")
        prop = await create_property(session, branch, name="x", data_type="string")

        link1 = await add_property_to_bundle(session, branch, bundle.id, prop.id, required=False)
        link2 = await add_property_to_bundle(session, branch, bundle.id, prop.id, required=True)
        assert link1.id == link2.id
        assert link2.required is True

        n = await session.scalar(
            select(func.count()).select_from(TPBundleProperty).where(TPBundleProperty.bundle_id == bundle.id)
        )
        assert n == 1


@pytest.mark.anyio
async def test_attach_bundle_to_event_creates_links_idempotently(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        event = await create_event(session, branch, name="purchase")
        bundle = await create_bundle(session, branch, name="Item")
        p1 = await create_property(session, branch, name="item_id", data_type="string")
        p2 = await create_property(session, branch, name="price", data_type="float")
        await add_property_to_bundle(session, branch, bundle.id, p1.id, required=True, sort_order=0)
        await add_property_to_bundle(session, branch, bundle.id, p2.id, required=False, sort_order=1)

        links = await attach_bundle_to_event(session, branch, event.id, bundle.id)
        assert len(links) == 2

        n = await session.scalar(
            select(func.count()).select_from(TPEventProperty).where(TPEventProperty.event_id == event.id)
        )
        assert n == 2
        # required flag was copied from the bundle link
        by_prop = {
            ep.property_id: ep
            for ep in (
                await session.execute(select(TPEventProperty).where(TPEventProperty.event_id == event.id))
            )
            .scalars()
            .all()
        }
        assert by_prop[p1.id].required is True
        assert by_prop[p2.id].required is False

        # Re-attaching does not duplicate event-property rows.
        links2 = await attach_bundle_to_event(session, branch, event.id, bundle.id)
        assert len(links2) == 2
        n2 = await session.scalar(
            select(func.count()).select_from(TPEventProperty).where(TPEventProperty.event_id == event.id)
        )
        assert n2 == 2


@pytest.mark.anyio
async def test_duplicate_bundle_name_conflicts(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        await create_bundle(session, branch, name="Item")
        with pytest.raises(ConflictError):
            await create_bundle(session, branch, name="Item")


@pytest.mark.anyio
async def test_delete_bundle_cascades_links(db_session_factory):
    async with db_session_factory() as session:
        branch = await _branch(session)
        bundle = await create_bundle(session, branch, name="Item")
        prop = await create_property(session, branch, name="x", data_type="string")
        await add_property_to_bundle(session, branch, bundle.id, prop.id)

        await delete_bundle(session, branch, bundle.id)

        n = await session.scalar(
            select(func.count()).select_from(TPBundleProperty).where(TPBundleProperty.bundle_id == bundle.id)
        )
        assert n == 0
