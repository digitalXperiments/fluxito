# tests/services/tracking_plan/test_branches.py
"""Branch workflow: copy-on-write create, isolation, diff, merge, review."""

import pytest
from sqlalchemy import select

from app.models.tracking_plan import (
    TPCategory,
    TPDestination,
    TPEvent,
    TPEventDestination,
    TPEventProperty,
    TPEventSource,
    TPMetric,
    TPProperty,
    TPPropertyMember,
    TPSource,
    TPSourceDestination,
)
from app.services.tracking_plan import (
    ValidationError,
    abandon_branch,
    attach_property,
    connect_source_destination,
    create_branch,
    create_category,
    create_destination,
    create_event,
    create_metric,
    create_property,
    create_source,
    diff_branches,
    get_branch,
    get_main_branch,
    get_or_create_plan,
    list_branches,
    merge_branch,
    set_event_destination,
    set_event_sources,
    set_review_status,
    update_event,
)
from app.services.tracking_plan.properties import add_member

from .test_models import _make_project_and_user


async def _count(session, model, branch_id) -> int:
    rows = (await session.execute(select(model).where(model.branch_id == branch_id))).scalars().all()
    return len(rows)


async def _seed_main(session):
    """Build a fully-populated plan on main: category, event (in category),
    object property with a linked member property (tp_property_members), event<->property
    attachment, source, destination, source->dest routing, event-source scope,
    event-destination mapping, and a metric. Returns (plan, main, user_id)."""
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    main = await get_main_branch(session, plan)

    cat = await create_category(session, main, name="Commerce", description="shop")
    event = await create_event(session, main, name="purchase", category_id=cat.id, description="buy")

    # Object nesting now uses the link table: create parent object prop, create
    # member prop, then link them via add_member.
    parent = await create_property(session, main, name="cart", data_type="object")
    child = await create_property(session, main, name="cart.total", data_type="float")
    await add_member(session, main, parent.id, child.id, required=False, sort_order=0)
    await attach_property(session, main, event.id, child.id, required=True, example="9.99", sort_order=1)

    source = await create_source(session, main, name="web", platform_type="browser")
    dest = await create_destination(session, main, name="ga4", platform="ga4")
    await connect_source_destination(session, main, source.id, dest.id)
    await set_event_sources(
        session, main, event.id, [{"source_id": source.id, "implementation_status": "implemented"}]
    )
    await set_event_destination(
        session,
        main,
        event.id,
        dest.id,
        dest_event_name="purchase_ga4",
        property_mappings={"cart.total": "value"},
        notes="map total",
    )
    await create_metric(session, main, name="purchases", event_id=event.id)
    return plan, main, user_id


@pytest.mark.anyio
async def test_create_branch_deep_copies_all_entities(db_session_factory):
    async with db_session_factory() as session:
        plan, main, user_id = await _seed_main(session)

        branch = await create_branch(session, plan, name="feature-x", user_id=user_id)
        assert branch.is_main is False
        assert branch.base_branch_id == main.id
        assert branch.status == "active"
        assert branch.review_status == "draft"

        # Every branch-scoped entity count matches main.
        for model in (TPCategory, TPEvent, TPProperty, TPSource, TPDestination, TPMetric):
            assert await _count(session, model, branch.id) == await _count(session, model, main.id)

        # Link-table rows copied too.
        async def _link_count(model, parent_ids):
            rows = (await session.execute(select(model).where(model.event_id.in_(parent_ids)))).scalars()
            return len(list(rows))

        main_event_ids = (
            (await session.execute(select(TPEvent.id).where(TPEvent.branch_id == main.id))).scalars().all()
        )
        branch_event_ids = (
            (await session.execute(select(TPEvent.id).where(TPEvent.branch_id == branch.id))).scalars().all()
        )
        assert await _link_count(TPEventProperty, main_event_ids) == 1
        assert await _link_count(TPEventProperty, branch_event_ids) == 1
        assert await _link_count(TPEventSource, branch_event_ids) == 1
        assert await _link_count(TPEventDestination, branch_event_ids) == 1

        # ids all differ between branches.
        main_event = (await session.execute(select(TPEvent).where(TPEvent.branch_id == main.id))).scalar_one()
        branch_event = (
            await session.execute(select(TPEvent).where(TPEvent.branch_id == branch.id))
        ).scalar_one()
        assert branch_event.id != main_event.id

        # Copied event's category resolves to the copied category (not main's).
        branch_cat = (
            await session.execute(select(TPCategory).where(TPCategory.branch_id == branch.id))
        ).scalar_one()
        assert branch_event.category_id == branch_cat.id
        assert branch_event.category_id != main_event.category_id

        # Copied event's attached property resolves to the copied property.
        branch_props = {
            p.name: p
            for p in (await session.execute(select(TPProperty).where(TPProperty.branch_id == branch.id)))
            .scalars()
            .all()
        }
        ep = (
            await session.execute(select(TPEventProperty).where(TPEventProperty.event_id == branch_event.id))
        ).scalar_one()
        assert ep.property_id == branch_props["cart.total"].id
        assert ep.required is True
        assert ep.example == "9.99"

        # tp_property_members link was copied into the forked branch and remapped
        # to branch-local ids (not main's parent id).
        branch_child = branch_props["cart.total"]
        branch_parent = branch_props["cart"]
        main_parent = (
            await session.execute(
                select(TPProperty).where(TPProperty.branch_id == main.id, TPProperty.name == "cart")
            )
        ).scalar_one()

        # Branch member link points to the branch's parent (not main's parent).
        branch_member_link = (
            await session.execute(
                select(TPPropertyMember).where(
                    TPPropertyMember.parent_property_id == branch_parent.id,
                    TPPropertyMember.member_property_id == branch_child.id,
                )
            )
        ).scalar_one_or_none()
        assert branch_member_link is not None, "tp_property_members link was not copied to the branch"

        # Main's member link must NOT reference the branch's property ids.
        main_member_link = (
            await session.execute(
                select(TPPropertyMember).where(
                    TPPropertyMember.parent_property_id == main_parent.id,
                    TPPropertyMember.member_property_id == branch_child.id,
                )
            )
        ).scalar_one_or_none()
        assert main_member_link is None, "branch member link should not reference main's parent"

        # Metric's event resolves to the branch's copy (no property_id column anymore).
        branch_metric = (
            await session.execute(select(TPMetric).where(TPMetric.branch_id == branch.id))
        ).scalar_one()
        assert branch_metric.event_id == branch_event.id

        # Routing copied (source->dest).
        branch_source = (
            await session.execute(select(TPSource).where(TPSource.branch_id == branch.id))
        ).scalar_one()
        routes = (
            (
                await session.execute(
                    select(TPSourceDestination).where(TPSourceDestination.source_id == branch_source.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(routes) == 1


@pytest.mark.anyio
async def test_branch_isolation(db_session_factory):
    async with db_session_factory() as session:
        plan, main, user_id = await _seed_main(session)
        branch = await create_branch(session, plan, name="rename", user_id=user_id)

        branch_event = (
            await session.execute(select(TPEvent).where(TPEvent.branch_id == branch.id))
        ).scalar_one()
        await update_event(session, branch, branch_event.id, name="checkout")

        main_event = (await session.execute(select(TPEvent).where(TPEvent.branch_id == main.id))).scalar_one()
        assert main_event.name == "purchase"  # main untouched


@pytest.mark.anyio
async def test_diff_branches(db_session_factory):
    async with db_session_factory() as session:
        plan, main, user_id = await _seed_main(session)
        branch = await create_branch(session, plan, name="diffable", user_id=user_id)

        # Add an event, change the existing one on the branch.
        await create_event(session, branch, name="signup")
        branch_purchase = (
            await session.execute(
                select(TPEvent).where(TPEvent.branch_id == branch.id, TPEvent.name == "purchase")
            )
        ).scalar_one()
        await update_event(session, branch, branch_purchase.id, description="changed desc")

        # Remove one on the branch by adding it to main only: add an event to main
        # so it appears as "removed" from the branch's perspective.
        await create_event(session, main, name="legacy")

        diff = await diff_branches(session, plan, main, branch)

        added_names = {e["name"] for e in diff["events"]["added"]}
        removed_names = {e["name"] for e in diff["events"]["removed"]}
        changed_names = {c["name"] for c in diff["events"]["changed"]}
        assert "signup" in added_names
        assert "legacy" in removed_names
        assert "purchase" in changed_names
        assert diff["summary"]["added"] >= 1
        assert diff["summary"]["removed"] >= 1
        assert diff["summary"]["changed"] >= 1


@pytest.mark.anyio
async def test_merge_branch(db_session_factory):
    async with db_session_factory() as session:
        plan, main, user_id = await _seed_main(session)
        before_version = plan.current_version_id
        branch = await create_branch(session, plan, name="to-merge", user_id=user_id)

        branch_event = (
            await session.execute(select(TPEvent).where(TPEvent.branch_id == branch.id))
        ).scalar_one()
        await update_event(session, branch, branch_event.id, name="checkout")
        await create_event(session, branch, name="extra_event")

        result = await merge_branch(session, plan, branch, user_id=user_id)

        assert branch.status == "merged"
        assert branch.merged_at is not None
        assert result["merged_branch"] == str(branch.id)
        assert result["version_number"] is not None

        # main now reflects the branch's content.
        main_event_names = {
            e.name
            for e in (await session.execute(select(TPEvent).where(TPEvent.branch_id == main.id)))
            .scalars()
            .all()
        }
        assert "checkout" in main_event_names
        assert "extra_event" in main_event_names
        assert "purchase" not in main_event_names

        # A new version was published.
        assert plan.current_version_id is not None
        assert plan.current_version_id != before_version


@pytest.mark.anyio
async def test_set_review_status(db_session_factory):
    async with db_session_factory() as session:
        plan, main, user_id = await _seed_main(session)
        branch = await create_branch(session, plan, name="review-me", user_id=user_id)

        updated = await set_review_status(session, branch, "ready_for_review", reviewer_id=user_id)
        assert updated.review_status == "ready_for_review"
        assert updated.reviewer_id == user_id

        with pytest.raises(ValidationError):
            await set_review_status(session, branch, "bogus")

        # Rejected on main.
        with pytest.raises(ValidationError):
            await set_review_status(session, main, "approved")


@pytest.mark.anyio
async def test_create_branch_duplicate_name_conflicts(db_session_factory):
    from app.services.tracking_plan import ConflictError

    async with db_session_factory() as session:
        plan, main, user_id = await _seed_main(session)
        await create_branch(session, plan, name="dup", user_id=user_id)
        with pytest.raises(ConflictError):
            await create_branch(session, plan, name="dup", user_id=user_id)


@pytest.mark.anyio
async def test_get_and_list_branches(db_session_factory):
    async with db_session_factory() as session:
        plan, main, user_id = await _seed_main(session)
        b1 = await create_branch(session, plan, name="alpha", user_id=user_id)

        # by id and by name
        assert (await get_branch(session, plan, b1.id)).id == b1.id
        assert (await get_branch(session, plan, "alpha")).id == b1.id

        branches = await list_branches(session, plan)
        assert branches[0].is_main is True  # main first
        assert {b.name for b in branches} == {"main", "alpha"}


@pytest.mark.anyio
async def test_abandon_branch(db_session_factory):
    async with db_session_factory() as session:
        plan, main, user_id = await _seed_main(session)
        branch = await create_branch(session, plan, name="dead", user_id=user_id)
        await abandon_branch(session, branch)
        assert branch.status == "abandoned"
        with pytest.raises(ValidationError):
            await abandon_branch(session, main)
