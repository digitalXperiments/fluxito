# app/services/tracking_plan/events.py
"""Event CRUD, source scoping (+ per-source status), and destination mapping rules."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    IMPL_STATUSES,
    TPBranch,
    TPCategory,
    TPDestination,
    TPEvent,
    TPEventDestination,
    TPEventSource,
    TPSource,
)

from .common import _UNSET, apply_fields, coerce_uuid, get_or_raise
from .exceptions import ConflictError, ValidationError

_EVENT_FIELDS = {
    "name",
    "display_name",
    "description",
    "category_id",
    "tags",
    "trigger_type",
    "trigger_config",
    "purpose",
    "owner_business",
    "owner_technical",
    "consent_required",
}
_EVENT_DEST_FIELDS = {"dest_event_name", "property_mappings", "enabled", "notes"}


async def _event_name_taken(session, branch_id, name, *, exclude_id=None) -> bool:
    stmt = select(TPEvent.id).where(TPEvent.branch_id == branch_id, TPEvent.name == name)
    if exclude_id is not None:
        stmt = stmt.where(TPEvent.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


async def create_event(session: AsyncSession, branch: TPBranch, *, name: str, **fields: Any) -> TPEvent:
    if not name or not name.strip():
        raise ValidationError("event name is required")
    name = name.strip()
    if await _event_name_taken(session, branch.id, name):
        raise ConflictError(f"event '{name}' already exists")
    if fields.get("category_id"):
        await get_or_raise(session, TPCategory, fields["category_id"], branch_id=branch.id)
    event = TPEvent(plan_id=branch.plan_id, branch_id=branch.id, name=name)
    apply_fields(event, fields, _EVENT_FIELDS - {"name"})
    session.add(event)
    await session.flush()
    return event


async def update_event(session: AsyncSession, branch: TPBranch, event_id: Any, **fields: Any) -> TPEvent:
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    new_name = fields.get("name", _UNSET)
    if new_name is not _UNSET:
        if not new_name or not new_name.strip():
            raise ValidationError("event name cannot be empty")
        fields["name"] = new_name.strip()
        if await _event_name_taken(session, branch.id, fields["name"], exclude_id=event.id):
            raise ConflictError(f"event '{fields['name']}' already exists")
    if fields.get("category_id"):
        await get_or_raise(session, TPCategory, fields["category_id"], branch_id=branch.id)
    apply_fields(event, fields, _EVENT_FIELDS)
    await session.flush()
    return event


async def delete_event(session: AsyncSession, branch: TPBranch, event_id: Any) -> None:
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    await session.delete(event)
    await session.flush()


async def set_event_sources(
    session: AsyncSession, branch: TPBranch, event_id: Any, scopes: list[dict]
) -> list[TPEventSource]:
    """Replace an event's source-scoping set. Each scope dict: {source_id,
    implementation_status?}. Status defaults to 'planned'."""
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)

    # Validate first (all-or-nothing)
    resolved = []
    seen: set = set()
    for scope in scopes:
        status = scope.get("implementation_status", "planned")
        if status not in IMPL_STATUSES:
            raise ValidationError(f"implementation_status must be one of {IMPL_STATUSES}, got {status!r}")
        source = await get_or_raise(session, TPSource, scope["source_id"], branch_id=branch.id)
        if source.id in seen:
            raise ValidationError(f"source {scope['source_id']} listed more than once")
        seen.add(source.id)
        resolved.append((source.id, status))

    # Delete the current set, then insert the new one
    existing = await session.execute(select(TPEventSource).where(TPEventSource.event_id == event.id))
    for link in existing.scalars().all():
        await session.delete(link)
    await session.flush()

    out = []
    for source_id, status in resolved:
        link = TPEventSource(event_id=event.id, source_id=source_id, implementation_status=status)
        session.add(link)
        out.append(link)
    await session.flush()
    return out


async def set_event_destination(
    session: AsyncSession,
    branch: TPBranch,
    event_id: Any,
    destination_id: Any,
    **fields: Any,
) -> TPEventDestination:
    """Upsert the (event x destination) mapping rule."""
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    dest = await get_or_raise(session, TPDestination, destination_id, branch_id=branch.id)

    existing = await session.execute(
        select(TPEventDestination).where(
            TPEventDestination.event_id == event.id, TPEventDestination.destination_id == dest.id
        )
    )
    mapping = existing.scalar_one_or_none()
    if mapping is None:
        mapping = TPEventDestination(event_id=event.id, destination_id=dest.id)
        session.add(mapping)
    apply_fields(mapping, fields, _EVENT_DEST_FIELDS)
    await session.flush()
    return mapping


async def remove_event_destination(
    session: AsyncSession, branch: TPBranch, event_id: Any, destination_id: Any
) -> None:
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    existing = await session.execute(
        select(TPEventDestination).where(
            TPEventDestination.event_id == event.id,
            TPEventDestination.destination_id == coerce_uuid(destination_id),
        )
    )
    mapping = existing.scalar_one_or_none()
    if mapping is not None:
        await session.delete(mapping)
        await session.flush()
