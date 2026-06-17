# app/services/tracking_plan/properties.py
"""Property library CRUD + event<->property attachment + object member ops."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    PROPERTY_DATA_TYPES,
    PROPERTY_KINDS,
    TPEvent,
    TPEventProperty,
    TPProperty,
    TPPropertyMember,
)

from .common import _UNSET, apply_fields, coerce_uuid, get_or_raise
from .exceptions import ConflictError, NotFoundError, ValidationError

_PROPERTY_FIELDS = {
    "name",
    "description",
    "data_type",
    "constraints",
    "is_pii",
    "is_list",
}

# Maximum allowed nesting depth for object property members.
_MAX_MEMBER_DEPTH = 6


def _validate_property_shape(*, data_type: str, constraints: dict | None) -> None:
    if data_type not in PROPERTY_DATA_TYPES:
        raise ValidationError(f"data_type must be one of {PROPERTY_DATA_TYPES}, got {data_type!r}")
    if not constraints:
        return
    if "allowed_values" in constraints:
        allowed = constraints["allowed_values"]
        if not isinstance(allowed, list) or len(allowed) == 0:
            raise ValidationError("constraints.allowed_values must be a non-empty list")
    # Numeric range: min/max, when present, must be numbers with min <= max.
    # bool is a subclass of int, so reject it explicitly. regex/format stay opaque.
    has_min = "min" in constraints and constraints["min"] is not None
    has_max = "max" in constraints and constraints["max"] is not None
    cmin = constraints.get("min")
    cmax = constraints.get("max")
    if has_min and (isinstance(cmin, bool) or not isinstance(cmin, (int, float))):
        raise ValidationError("constraints.min must be a number")
    if has_max and (isinstance(cmax, bool) or not isinstance(cmax, (int, float))):
        raise ValidationError("constraints.max must be a number")
    if has_min and has_max and cmin > cmax:
        raise ValidationError("constraints.min must be <= constraints.max")


async def _prop_name_taken(session, branch_id, kind, name, *, exclude_id=None) -> bool:
    stmt = select(TPProperty.id).where(
        TPProperty.branch_id == branch_id, TPProperty.kind == kind, TPProperty.name == name
    )
    if exclude_id is not None:
        stmt = stmt.where(TPProperty.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


async def _get_member_links(session: AsyncSession, parent_id: uuid.UUID) -> list[TPPropertyMember]:
    """Return all TPPropertyMember rows for a given parent, ordered by sort_order."""
    result = await session.execute(
        select(TPPropertyMember)
        .where(TPPropertyMember.parent_property_id == parent_id)
        .order_by(TPPropertyMember.sort_order)
    )
    return list(result.scalars().all())


async def _check_no_cycle(
    session: AsyncSession,
    parent_id: uuid.UUID,
    candidate_member_id: uuid.UUID,
    *,
    depth: int = 0,
) -> None:
    """Raise ValidationError if adding candidate_member_id as a member of parent_id
    would create a cycle (a property that is transitively a member of itself) or
    exceed _MAX_MEMBER_DEPTH."""
    if depth >= _MAX_MEMBER_DEPTH:
        raise ValidationError(
            f"Adding this member would exceed the maximum nesting depth ({_MAX_MEMBER_DEPTH})"
        )
    if candidate_member_id == parent_id:
        raise ValidationError("A property cannot be a member of itself")
    # Walk upward: find every object that has parent_id as a member. If any of
    # those ancestors is candidate_member_id, we have a cycle.
    ancestor_links = await session.execute(
        select(TPPropertyMember.parent_property_id).where(TPPropertyMember.member_property_id == parent_id)
    )
    for (ancestor_id,) in ancestor_links.all():
        if ancestor_id == candidate_member_id:
            raise ValidationError("Adding this member would create a membership cycle")
        await _check_no_cycle(session, ancestor_id, candidate_member_id, depth=depth + 1)


async def create_property(
    session: AsyncSession,
    branch,
    *,
    name: str,
    data_type: str,
    kind: str = "event",
    description: str | None = None,
    constraints: dict | None = None,
    is_pii: bool = False,
    is_list: bool = False,
) -> TPProperty:
    if not name or not name.strip():
        raise ValidationError("property name is required")
    if kind not in PROPERTY_KINDS:
        raise ValidationError(f"kind must be one of {PROPERTY_KINDS}, got {kind!r}")
    _validate_property_shape(data_type=data_type, constraints=constraints)
    name = name.strip()
    if await _prop_name_taken(session, branch.id, kind, name):
        raise ConflictError(f"property '{name}' ({kind}) already exists")
    prop = TPProperty(
        plan_id=branch.plan_id,
        branch_id=branch.id,
        name=name,
        kind=kind,
        data_type=data_type,
        description=description,
        constraints=constraints,
        is_pii=is_pii,
        is_list=is_list,
    )
    session.add(prop)
    await session.flush()
    return prop


async def update_property(session: AsyncSession, branch, property_id: Any, **fields: Any) -> TPProperty:
    prop = await get_or_raise(session, TPProperty, property_id, branch_id=branch.id)
    new_dt = fields.get("data_type", _UNSET)
    new_constraints = fields.get("constraints", _UNSET)
    effective_dt = prop.data_type if new_dt is _UNSET else new_dt
    _validate_property_shape(
        data_type=effective_dt,
        constraints=prop.constraints if new_constraints is _UNSET else new_constraints,
    )

    # If the property is being changed from object to a scalar type, check for
    # existing members. We reject the change rather than silently orphaning them.
    if new_dt is not _UNSET and new_dt != "object" and prop.data_type == "object":
        existing_members = await _get_member_links(session, prop.id)
        if existing_members:
            raise ValidationError(
                f"Cannot change data_type from 'object' to '{new_dt}': the property has "
                f"{len(existing_members)} member(s). Remove all members first."
            )

    new_name = fields.get("name", _UNSET)
    if new_name is not _UNSET:
        if not new_name or not new_name.strip():
            raise ValidationError("property name cannot be empty")
        fields["name"] = new_name.strip()
        if await _prop_name_taken(session, branch.id, prop.kind, fields["name"], exclude_id=prop.id):
            raise ConflictError(f"property '{fields['name']}' ({prop.kind}) already exists")

    apply_fields(prop, fields, _PROPERTY_FIELDS)
    await session.flush()
    return prop


async def delete_property(session: AsyncSession, branch, property_id: Any) -> None:
    prop = await get_or_raise(session, TPProperty, property_id, branch_id=branch.id)
    await session.delete(prop)
    await session.flush()


# ---------------------------------------------------------------------------
# Object member operations (tp_property_members link table)
# ---------------------------------------------------------------------------


async def add_member(
    session: AsyncSession,
    branch,
    parent_id: Any,
    member_property_id: Any,
    *,
    required: bool = False,
    sort_order: int = 0,
) -> TPPropertyMember:
    """Link an existing library property as a member key of a parent object property.

    Validates:
    - parent must be data_type='object' (or object+is_list).
    - member_property_id must exist on the same branch.
    - No self-membership, no cycles, max nesting depth respected.
    - Idempotent: re-adding updates required/sort_order in place.
    """
    parent_uid = coerce_uuid(parent_id)
    member_uid = coerce_uuid(member_property_id)

    parent = await get_or_raise(session, TPProperty, parent_uid, branch_id=branch.id)
    if parent.data_type != "object":
        raise ValidationError(
            f"Members can only be added to properties with data_type='object', "
            f"but '{parent.name}' has data_type='{parent.data_type}'"
        )

    # Confirm member property is in the same branch.
    await get_or_raise(session, TPProperty, member_uid, branch_id=branch.id)

    # Cycle + depth guard.
    await _check_no_cycle(session, parent_uid, member_uid)

    # Idempotent: check for existing link.
    existing_result = await session.execute(
        select(TPPropertyMember).where(
            TPPropertyMember.parent_property_id == parent_uid,
            TPPropertyMember.member_property_id == member_uid,
        )
    )
    link = existing_result.scalar_one_or_none()
    if link is None:
        link = TPPropertyMember(
            parent_property_id=parent_uid,
            member_property_id=member_uid,
            required=required,
            sort_order=sort_order,
        )
        session.add(link)
    else:
        link.required = required
        link.sort_order = sort_order

    await session.flush()
    return link


async def remove_member(
    session: AsyncSession,
    branch,
    parent_id: Any,
    member_property_id: Any,
) -> None:
    """Unlink a member property from a parent object property.

    This removes the link only — neither property is deleted from the library.
    """
    parent_uid = coerce_uuid(parent_id)
    member_uid = coerce_uuid(member_property_id)

    # Validate both sides exist on this branch.
    await get_or_raise(session, TPProperty, parent_uid, branch_id=branch.id)

    result = await session.execute(
        select(TPPropertyMember).where(
            TPPropertyMember.parent_property_id == parent_uid,
            TPPropertyMember.member_property_id == member_uid,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise NotFoundError(f"property {member_property_id} is not a member of {parent_id}")
    await session.delete(link)
    await session.flush()


async def reorder_members(
    session: AsyncSession,
    branch,
    parent_id: Any,
    ordered_member_ids: list[Any],
) -> list[TPPropertyMember]:
    """Reassign sort_order on member links to match the supplied id order.

    Only the members present in ordered_member_ids are reordered; any omitted
    members retain their existing sort_order.  Raises NotFoundError if any id
    is not an existing member of parent_id.
    """
    parent_uid = coerce_uuid(parent_id)
    await get_or_raise(session, TPProperty, parent_uid, branch_id=branch.id)

    # Load all existing links for this parent.
    existing_links = await _get_member_links(session, parent_uid)
    link_by_member: dict[uuid.UUID, TPPropertyMember] = {lk.member_property_id: lk for lk in existing_links}

    updated: list[TPPropertyMember] = []
    for pos, mid in enumerate(ordered_member_ids):
        mid_uuid = coerce_uuid(mid)
        lk = link_by_member.get(mid_uuid)
        if lk is None:
            raise NotFoundError(f"property {mid} is not a member of {parent_id}")
        lk.sort_order = pos
        updated.append(lk)

    await session.flush()
    return updated


async def create_and_link_member(
    session: AsyncSession,
    branch,
    parent_id: Any,
    *,
    name: str,
    data_type: str,
    kind: str = "event",
    description: str | None = None,
    constraints: dict | None = None,
    is_pii: bool = False,
    is_list: bool = False,
    required: bool = False,
    sort_order: int = 0,
) -> tuple[TPProperty, TPPropertyMember]:
    """Create a new library property and immediately link it as a member of parent_id.

    Convenience for the "create new member inline" UI path. Uses create_property
    and add_member so all validation runs in both places.
    """
    new_prop = await create_property(
        session,
        branch,
        name=name,
        data_type=data_type,
        kind=kind,
        description=description,
        constraints=constraints,
        is_pii=is_pii,
        is_list=is_list,
    )
    link = await add_member(
        session,
        branch,
        parent_id,
        new_prop.id,
        required=required,
        sort_order=sort_order,
    )
    return new_prop, link


# ---------------------------------------------------------------------------
# Event<->property attachment
# ---------------------------------------------------------------------------


async def attach_property(
    session: AsyncSession,
    branch,
    event_id: Any,
    property_id: Any,
    *,
    required: bool = False,
    example: str | None = None,
    override_description: str | None = None,
    sort_order: int = 0,
) -> TPEventProperty:
    """Attach a library property to an event. Idempotent: re-attaching updates
    the existing link's overrides instead of inserting a duplicate."""
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    prop = await get_or_raise(session, TPProperty, property_id, branch_id=branch.id)

    existing = await session.execute(
        select(TPEventProperty).where(
            TPEventProperty.event_id == event.id, TPEventProperty.property_id == prop.id
        )
    )
    link = existing.scalar_one_or_none()
    if link is None:
        link = TPEventProperty(event_id=event.id, property_id=prop.id)
        session.add(link)
    link.required = required
    link.example = example
    link.override_description = override_description
    link.sort_order = sort_order
    await session.flush()
    return link


async def detach_property(session: AsyncSession, branch, event_id: Any, property_id: Any) -> None:
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    existing = await session.execute(
        select(TPEventProperty).where(
            TPEventProperty.event_id == event.id,
            TPEventProperty.property_id == coerce_uuid(property_id),
        )
    )
    link = existing.scalar_one_or_none()
    if link is None:
        raise NotFoundError(f"property {property_id} is not attached to event {event_id}")
    await session.delete(link)
    await session.flush()
