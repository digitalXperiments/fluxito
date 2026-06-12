# app/services/tracking_plan/properties.py
"""Property library CRUD + event<->property attachment."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    PROPERTY_DATA_TYPES,
    PROPERTY_KINDS,
    TPEvent,
    TPEventProperty,
    TPProperty,
)

from .common import _UNSET, apply_fields, coerce_uuid, get_or_raise
from .exceptions import ConflictError, NotFoundError, ValidationError

_PROPERTY_FIELDS = {"name", "description", "data_type", "constraints", "is_pii", "parent_property_id"}


def _validate_property_shape(*, data_type: str, constraints: dict | None) -> None:
    if data_type not in PROPERTY_DATA_TYPES:
        raise ValidationError(f"data_type must be one of {PROPERTY_DATA_TYPES}, got {data_type!r}")
    if constraints and "allowed_values" in constraints:
        allowed = constraints["allowed_values"]
        if not isinstance(allowed, list) or len(allowed) == 0:
            raise ValidationError("constraints.allowed_values must be a non-empty list")


async def _prop_name_taken(session, branch_id, kind, name, *, exclude_id=None) -> bool:
    stmt = select(TPProperty.id).where(
        TPProperty.branch_id == branch_id, TPProperty.kind == kind, TPProperty.name == name
    )
    if exclude_id is not None:
        stmt = stmt.where(TPProperty.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


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
    parent_property_id: Any = None,
) -> TPProperty:
    if not name or not name.strip():
        raise ValidationError("property name is required")
    if kind not in PROPERTY_KINDS:
        raise ValidationError(f"kind must be one of {PROPERTY_KINDS}, got {kind!r}")
    _validate_property_shape(data_type=data_type, constraints=constraints)
    name = name.strip()
    if await _prop_name_taken(session, branch.id, kind, name):
        raise ConflictError(f"property '{name}' ({kind}) already exists")
    parent_id = None
    if parent_property_id is not None:
        parent = await get_or_raise(session, TPProperty, parent_property_id, branch_id=branch.id)
        parent_id = parent.id
    prop = TPProperty(
        plan_id=branch.plan_id,
        branch_id=branch.id,
        name=name,
        kind=kind,
        data_type=data_type,
        description=description,
        constraints=constraints,
        is_pii=is_pii,
        parent_property_id=parent_id,
    )
    session.add(prop)
    await session.flush()
    return prop


async def update_property(session: AsyncSession, branch, property_id: Any, **fields: Any) -> TPProperty:
    prop = await get_or_raise(session, TPProperty, property_id, branch_id=branch.id)
    new_dt = fields.get("data_type", _UNSET)
    new_constraints = fields.get("constraints", _UNSET)
    _validate_property_shape(
        data_type=prop.data_type if new_dt is _UNSET else new_dt,
        constraints=prop.constraints if new_constraints is _UNSET else new_constraints,
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
