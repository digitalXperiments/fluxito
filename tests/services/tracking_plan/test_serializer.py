import pytest

from app.services.tracking_plan import (
    add_property_to_bundle,
    attach_property,
    connect_source_destination,
    create_bundle,
    create_category,
    create_destination,
    create_event,
    create_metric,
    create_property,
    create_source,
    plan_to_dict,
    set_event_destination,
    set_event_sources,
)
from app.services.tracking_plan.bootstrap import get_main_branch, get_or_create_plan
from tests.services.tracking_plan.test_models import _make_project_and_user


@pytest.mark.anyio
async def test_plan_to_dict_full_shape(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id, name="P")
        branch = await get_main_branch(session, plan)

        cat = await create_category(session, branch, name="Commerce")
        ev = await create_event(session, branch, name="purchase", category_id=cat.id, tags=["money"])
        prop = await create_property(session, branch, name="value", data_type="float")
        await attach_property(session, branch, ev.id, prop.id, required=True, example="9.99")
        user_prop = await create_property(session, branch, name="plan_tier", data_type="string", kind="user")
        src = await create_source(session, branch, name="web", platform_type="web")
        dest = await create_destination(session, branch, name="GA4", platform="ga4")
        await connect_source_destination(session, branch, src.id, dest.id)
        await set_event_sources(
            session, branch, ev.id, [{"source_id": src.id, "implementation_status": "implemented"}]
        )
        await set_event_destination(session, branch, ev.id, dest.id, dest_event_name="purchase")
        await create_metric(session, branch, name="Revenue", type="sum", event_id=ev.id)

        data = await plan_to_dict(session, plan, branch)

        assert data["plan"]["name"] == "P"
        assert data["branch"]["name"] == "main"
        assert [c["name"] for c in data["categories"]] == ["Commerce"]

        assert len(data["events"]) == 1
        event = data["events"][0]
        assert event["name"] == "purchase"
        assert event["category"] == "Commerce"
        assert event["tags"] == ["money"]
        assert event["properties"][0]["name"] == "value"
        assert event["properties"][0]["required"] is True
        assert event["properties"][0]["example"] == "9.99"
        assert event["sources"][0]["name"] == "web"
        assert event["sources"][0]["implementation_status"] == "implemented"
        assert event["destinations"][0]["destination"] == "GA4"
        assert event["destinations"][0]["dest_event_name"] == "purchase"

        assert [p["name"] for p in data["properties"]["event"]] == ["value"]
        assert [p["name"] for p in data["properties"]["user"]] == ["plan_tier"]
        assert data["sources"][0]["destinations"] == ["GA4"]
        assert data["destinations"][0]["name"] == "GA4"
        assert data["metrics"][0]["name"] == "Revenue"


@pytest.mark.anyio
async def test_plan_to_dict_includes_bundles(db_session_factory):
    async with db_session_factory() as session:
        project_id, user_id = await _make_project_and_user(session)
        plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id, name="P")
        branch = await get_main_branch(session, plan)

        bundle = await create_bundle(session, branch, name="Item", description="item fields")
        p1 = await create_property(session, branch, name="item_id", data_type="string")
        p2 = await create_property(session, branch, name="price", data_type="float")
        await add_property_to_bundle(session, branch, bundle.id, p1.id, required=True, sort_order=0)
        await add_property_to_bundle(session, branch, bundle.id, p2.id, required=False, sort_order=1)

        data = await plan_to_dict(session, plan, branch)

        assert "bundles" in data
        assert len(data["bundles"]) == 1
        b = data["bundles"][0]
        assert b["name"] == "Item"
        assert b["description"] == "item fields"
        assert [p["name"] for p in b["properties"]] == ["item_id", "price"]
        assert b["properties"][0]["required"] is True
