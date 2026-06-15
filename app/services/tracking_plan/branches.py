# app/services/tracking_plan/branches.py
"""Branch workflow: copy-on-write create, list/get, review status, diff, merge.

A plan always has exactly one `main` branch (created by bootstrap). Feature
branches are created by deep-copying main's (or another branch's) content into a
fresh branch with fully remapped ids — so the two branches are independent and
editing one never touches the other. Merge is MVP last-writer-wins: it replaces
main's content wholesale with the branch's, then optionally publishes a version.
"""

import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    REVIEW_STATUSES,
    TPBranch,
    TPCategory,
    TPDestination,
    TPEvent,
    TPEventDestination,
    TPEventProperty,
    TPEventSource,
    TPMetric,
    TPPlan,
    TPProperty,
    TPSource,
    TPSourceDestination,
)

from .bootstrap import get_main_branch
from .common import coerce_uuid
from .exceptions import ConflictError, NotFoundError, ValidationError
from .publish import publish_branch
from .serializer import plan_to_dict
from .validation import validate_plan


# ---------------------------------------------------------------------------
# Create (copy-on-write)
# ---------------------------------------------------------------------------
async def create_branch(
    session: AsyncSession,
    plan: TPPlan,
    *,
    name: str,
    user_id: Any,
    from_branch: TPBranch | None = None,
    description: str | None = None,
) -> TPBranch:
    """Create a new branch as a copy-on-write fork of ``from_branch`` (default:
    the plan's main branch). All branch-scoped content is deep-copied with new
    ids so the new branch is fully independent of its source."""
    if not name or not name.strip():
        raise ValidationError("branch name is required")
    name = name.strip()

    from_ = from_branch or await get_main_branch(session, plan)

    if await _branch_name_taken(session, plan.id, name):
        raise ConflictError(f"branch '{name}' already exists")

    branch = TPBranch(
        plan_id=plan.id,
        name=name,
        is_main=False,
        base_branch_id=from_.id,
        base_version_id=plan.current_version_id,
        status="active",
        review_status="draft",
        created_by=coerce_uuid(user_id),
        description=description,
    )
    session.add(branch)
    await session.flush()  # populate branch.id

    await _copy_branch_contents(session, plan, from_.id, branch.id)
    return branch


async def _branch_name_taken(session: AsyncSession, plan_id: uuid.UUID, name: str) -> bool:
    stmt = select(TPBranch.id).where(TPBranch.plan_id == plan_id, TPBranch.name == name)
    return (await session.execute(stmt)).first() is not None


async def _copy_branch_contents(
    session: AsyncSession,
    plan: TPPlan,
    src_branch_id: uuid.UUID,
    dst_branch_id: uuid.UUID,
) -> None:
    """Deep-copy every branch-scoped entity from ``src`` into ``dst`` with id
    remapping. Order matters: parents before children, and properties are copied
    in two passes so the self-referential parent_property_id resolves cleanly."""
    plan_id = plan.id

    async def _scoped(model):
        result = await session.execute(select(model).where(model.branch_id == src_branch_id))
        return list(result.scalars().all())

    # 1. Categories
    cat_map: dict[uuid.UUID, uuid.UUID] = {}
    for cat in await _scoped(TPCategory):
        new_cat = TPCategory(
            plan_id=plan_id,
            branch_id=dst_branch_id,
            name=cat.name,
            description=cat.description,
            color=cat.color,
        )
        session.add(new_cat)
        await session.flush()
        cat_map[cat.id] = new_cat.id

    # 2. Properties — first pass: copy all with parent_property_id=None.
    src_properties = await _scoped(TPProperty)
    prop_map: dict[uuid.UUID, uuid.UUID] = {}
    src_parent_of: dict[uuid.UUID, uuid.UUID] = {}
    for prop in src_properties:
        if prop.parent_property_id is not None:
            src_parent_of[prop.id] = prop.parent_property_id
        new_prop = TPProperty(
            plan_id=plan_id,
            branch_id=dst_branch_id,
            name=prop.name,
            kind=prop.kind,
            data_type=prop.data_type,
            description=prop.description,
            constraints=prop.constraints,
            parent_property_id=None,
            is_pii=prop.is_pii,
        )
        session.add(new_prop)
        await session.flush()
        prop_map[prop.id] = new_prop.id
    # Second pass: wire up parent_property_id on the new rows.
    for old_id, old_parent in src_parent_of.items():
        new_prop = await session.get(TPProperty, prop_map[old_id])
        if new_prop is not None and old_parent in prop_map:
            new_prop.parent_property_id = prop_map[old_parent]
    await session.flush()

    # 3. Events (remap category_id)
    src_events = await _scoped(TPEvent)
    event_map: dict[uuid.UUID, uuid.UUID] = {}
    for ev in src_events:
        new_ev = TPEvent(
            plan_id=plan_id,
            branch_id=dst_branch_id,
            name=ev.name,
            display_name=ev.display_name,
            description=ev.description,
            category_id=cat_map.get(ev.category_id) if ev.category_id else None,
            tags=ev.tags,
            trigger_type=ev.trigger_type,
            trigger_config=ev.trigger_config,
            purpose=ev.purpose,
            owner_business=ev.owner_business,
            owner_technical=ev.owner_technical,
            consent_required=ev.consent_required,
        )
        session.add(new_ev)
        await session.flush()
        event_map[ev.id] = new_ev.id

    src_event_ids = list(event_map.keys())

    # 4. Event<->property links
    if src_event_ids:
        ep_rows = (
            (
                await session.execute(
                    select(TPEventProperty).where(TPEventProperty.event_id.in_(src_event_ids))
                )
            )
            .scalars()
            .all()
        )
        for link in ep_rows:
            if link.event_id not in event_map or link.property_id not in prop_map:
                continue
            session.add(
                TPEventProperty(
                    event_id=event_map[link.event_id],
                    property_id=prop_map[link.property_id],
                    required=link.required,
                    example=link.example,
                    override_description=link.override_description,
                    sort_order=link.sort_order,
                )
            )

    # 5. Sources + Destinations
    src_map: dict[uuid.UUID, uuid.UUID] = {}
    for src in await _scoped(TPSource):
        new_src = TPSource(
            plan_id=plan_id,
            branch_id=dst_branch_id,
            name=src.name,
            platform_type=src.platform_type,
            description=src.description,
            connector_ref=src.connector_ref,
        )
        session.add(new_src)
        await session.flush()
        src_map[src.id] = new_src.id

    dest_map: dict[uuid.UUID, uuid.UUID] = {}
    for dest in await _scoped(TPDestination):
        new_dest = TPDestination(
            plan_id=plan_id,
            branch_id=dst_branch_id,
            name=dest.name,
            platform=dest.platform,
            platform_account_id=dest.platform_account_id,
            config=dest.config,
        )
        session.add(new_dest)
        await session.flush()
        dest_map[dest.id] = new_dest.id

    # 6. Source->destination routing
    src_source_ids = list(src_map.keys())
    if src_source_ids:
        sd_rows = (
            (
                await session.execute(
                    select(TPSourceDestination).where(TPSourceDestination.source_id.in_(src_source_ids))
                )
            )
            .scalars()
            .all()
        )
        for route in sd_rows:
            if route.source_id not in src_map or route.destination_id not in dest_map:
                continue
            session.add(
                TPSourceDestination(
                    source_id=src_map[route.source_id],
                    destination_id=dest_map[route.destination_id],
                )
            )

    # 7. Event<->source scoping
    if src_event_ids:
        es_rows = (
            (await session.execute(select(TPEventSource).where(TPEventSource.event_id.in_(src_event_ids))))
            .scalars()
            .all()
        )
        for link in es_rows:
            if link.event_id not in event_map or link.source_id not in src_map:
                continue
            session.add(
                TPEventSource(
                    event_id=event_map[link.event_id],
                    source_id=src_map[link.source_id],
                    implementation_status=link.implementation_status,
                )
            )

        # 8. Event<->destination mapping rules (property_mappings is opaque — copy verbatim)
        ed_rows = (
            (
                await session.execute(
                    select(TPEventDestination).where(TPEventDestination.event_id.in_(src_event_ids))
                )
            )
            .scalars()
            .all()
        )
        for link in ed_rows:
            if link.event_id not in event_map or link.destination_id not in dest_map:
                continue
            session.add(
                TPEventDestination(
                    event_id=event_map[link.event_id],
                    destination_id=dest_map[link.destination_id],
                    dest_event_name=link.dest_event_name,
                    property_mappings=link.property_mappings,
                    enabled=link.enabled,
                    notes=link.notes,
                )
            )

    # 9. Metrics (remap event_id + property_id)
    for metric in await _scoped(TPMetric):
        session.add(
            TPMetric(
                plan_id=plan_id,
                branch_id=dst_branch_id,
                name=metric.name,
                description=metric.description,
                type=metric.type,
                event_id=event_map.get(metric.event_id) if metric.event_id else None,
                property_id=prop_map.get(metric.property_id) if metric.property_id else None,
                filters=metric.filters,
                dashboard_card_id=metric.dashboard_card_id,
            )
        )

    await session.flush()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
async def list_branches(session: AsyncSession, plan: TPPlan) -> list[TPBranch]:
    """All branches of a plan, main first then by created_at ascending."""
    result = await session.execute(
        select(TPBranch)
        .where(TPBranch.plan_id == plan.id)
        .order_by(TPBranch.is_main.desc(), TPBranch.created_at.asc())
    )
    return list(result.scalars().all())


async def get_branch(session: AsyncSession, plan: TPPlan, ref: Any) -> TPBranch:
    """Resolve a branch by id (UUID/str) or by name, scoped to the plan."""
    branch: TPBranch | None = None
    try:
        bid = coerce_uuid(ref)
    except (ValueError, AttributeError, TypeError):
        bid = None
    if bid is not None:
        branch = await session.get(TPBranch, bid)
        if branch is not None and branch.plan_id != plan.id:
            branch = None
    if branch is None:
        result = await session.execute(
            select(TPBranch).where(TPBranch.plan_id == plan.id, TPBranch.name == str(ref))
        )
        branch = result.scalar_one_or_none()
    if branch is None:
        raise NotFoundError(f"branch {ref!r} not found on plan {plan.id}")
    return branch


# ---------------------------------------------------------------------------
# Review status
# ---------------------------------------------------------------------------
async def set_review_status(
    session: AsyncSession,
    branch: TPBranch,
    review_status: str,
    *,
    reviewer_id: Any = None,
) -> TPBranch:
    """Set a branch's review status. Rejected on the main branch."""
    if branch.is_main:
        raise ValidationError("the main branch has no review status")
    if review_status not in REVIEW_STATUSES:
        raise ValidationError(f"review_status must be one of {REVIEW_STATUSES}, got {review_status!r}")
    branch.review_status = review_status
    if reviewer_id is not None:
        branch.reviewer_id = coerce_uuid(reviewer_id)
    await session.flush()
    return branch


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------
# Volatile/id-ish fields stripped before comparing content across branches, so
# the inevitable cross-branch id differences don't surface as spurious changes.
_VOLATILE_KEYS = {"id", "parent_property_id", "current_version_id", "branch", "plan"}


def _strip_ids(value: Any) -> Any:
    """Recursively drop id-ish keys so two cross-branch dicts compare on content."""
    if isinstance(value, dict):
        return {k: _strip_ids(v) for k, v in value.items() if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip_ids(v) for v in value]
    return value


def _normalized(value: Any) -> str:
    return json.dumps(_strip_ids(value), sort_keys=True, default=str)


def _diff_collection(base_items: list[dict], head_items: list[dict], *, key: str = "name") -> dict:
    base_by_key = {item[key]: item for item in base_items}
    head_by_key = {item[key]: item for item in head_items}

    added = [head_by_key[k] for k in head_by_key if k not in base_by_key]
    removed = [base_by_key[k] for k in base_by_key if k not in head_by_key]
    changed = []
    for k in head_by_key:
        if k in base_by_key and _normalized(base_by_key[k]) != _normalized(head_by_key[k]):
            changed.append({"name": k, "before": base_by_key[k], "after": head_by_key[k]})
    return {"added": added, "removed": removed, "changed": changed}


async def diff_branches(
    session: AsyncSession,
    plan: TPPlan,
    base_branch: TPBranch,
    head_branch: TPBranch,
) -> dict:
    """Structural diff of head vs base. For each collection, report added (in
    head only), removed (in base only), and changed (present in both but content
    differs after stripping cross-branch ids). Properties diff per-kind."""
    base = await plan_to_dict(session, plan, base_branch)
    head = await plan_to_dict(session, plan, head_branch)

    events = _diff_collection(base["events"], head["events"])
    categories = _diff_collection(base["categories"], head["categories"])
    sources = _diff_collection(base["sources"], head["sources"])
    destinations = _diff_collection(base["destinations"], head["destinations"])
    metrics = _diff_collection(base["metrics"], head["metrics"])

    properties: dict[str, dict] = {}
    for kind in ("event", "user", "group", "system"):
        properties[kind] = _diff_collection(
            base["properties"].get(kind, []), head["properties"].get(kind, [])
        )

    diff = {
        "events": events,
        "properties": properties,
        "sources": sources,
        "destinations": destinations,
        "metrics": metrics,
        "categories": categories,
    }

    def _count(d: dict, field: str) -> int:
        return len(d[field])

    added = removed = changed = 0
    for collection in (events, categories, sources, destinations, metrics):
        added += _count(collection, "added")
        removed += _count(collection, "removed")
        changed += _count(collection, "changed")
    for kind_diff in properties.values():
        added += _count(kind_diff, "added")
        removed += _count(kind_diff, "removed")
        changed += _count(kind_diff, "changed")

    diff["summary"] = {"added": added, "removed": removed, "changed": changed}
    return diff


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------
async def merge_branch(
    session: AsyncSession,
    plan: TPPlan,
    branch: TPBranch,
    *,
    user_id: Any,
    publish: bool = True,
    changelog: str | None = None,
) -> dict:
    """Merge a branch into main (MVP last-writer-wins).

    There is **no three-way merge / conflict detection** in this MVP: main's
    branch-scoped content is deleted wholesale and replaced with a fresh deep
    copy of the branch's content. Whatever the branch contains wins outright —
    concurrent edits made directly on main since the branch was forked are lost.
    The branch is then marked ``merged`` and (by default) a new version is
    published from main.
    """
    if branch.is_main:
        raise ValidationError("cannot merge the main branch into itself")
    if branch.status != "active":
        raise ValidationError(f"only active branches can be merged (status={branch.status!r})")

    report = await validate_plan(session, plan, branch)
    blocking = [f for f in report["findings"] if f.get("severity") == "error"]
    if blocking:
        raise ValidationError(
            f"Cannot merge branch '{branch.name}': {len(blocking)} blocking (error-severity) issue(s) must be resolved first."
        )

    main = await get_main_branch(session, plan)
    await _clear_branch_contents(session, main.id)
    await _copy_branch_contents(session, plan, branch.id, main.id)

    branch.status = "merged"
    branch.merged_at = func.now()
    await session.flush()
    # Refresh so merged_at is a real timestamp rather than the SQL expression
    # (callers/tests may read it back).
    await session.refresh(branch)

    version_number: str | None = None
    if publish:
        version = await publish_branch(
            session,
            plan,
            main,
            user_id=user_id,
            changelog=changelog or f"Merged branch {branch.name}",
        )
        version_number = version.version_number

    return {"merged_branch": str(branch.id), "version_number": version_number}


async def _clear_branch_contents(session: AsyncSession, branch_id: uuid.UUID) -> None:
    """Delete all branch-scoped content for a branch, children first. We delete
    the link tables explicitly (they are scoped via their parent rows, not by
    branch_id) so nothing is left dangling even where a cascade would suffice."""
    # Resolve the parent-row ids on this branch up front.
    event_ids = (
        (await session.execute(select(TPEvent.id).where(TPEvent.branch_id == branch_id))).scalars().all()
    )
    source_ids = (
        (await session.execute(select(TPSource.id).where(TPSource.branch_id == branch_id))).scalars().all()
    )

    async def _delete(model) -> None:
        rows = (await session.execute(select(model).where(model.branch_id == branch_id))).scalars().all()
        for row in rows:
            await session.delete(row)

    async def _delete_by(model, column, ids) -> None:
        if not ids:
            return
        rows = (await session.execute(select(model).where(column.in_(ids)))).scalars().all()
        for row in rows:
            await session.delete(row)

    # Metrics (branch-scoped, reference events/properties via SET NULL)
    await _delete(TPMetric)
    # Link tables scoped by their parent events / sources
    await _delete_by(TPEventDestination, TPEventDestination.event_id, event_ids)
    await _delete_by(TPEventSource, TPEventSource.event_id, event_ids)
    await _delete_by(TPEventProperty, TPEventProperty.event_id, event_ids)
    await _delete_by(TPSourceDestination, TPSourceDestination.source_id, source_ids)
    await session.flush()
    # Parents
    await _delete(TPEvent)
    await _delete(TPProperty)
    await _delete(TPSource)
    await _delete(TPDestination)
    await _delete(TPCategory)
    await session.flush()


# ---------------------------------------------------------------------------
# Abandon
# ---------------------------------------------------------------------------
async def abandon_branch(session: AsyncSession, branch: TPBranch) -> TPBranch:
    """Mark a branch abandoned. Rejected on the main branch."""
    if branch.is_main:
        raise ValidationError("cannot abandon the main branch")
    branch.status = "abandoned"
    await session.flush()
    return branch
