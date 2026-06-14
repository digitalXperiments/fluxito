# app/services/tracking_plan/routing.py
"""Source + destination CRUD and source->destination routing."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    TPBranch,
    TPDestination,
    TPSource,
    TPSourceDestination,
)

from .common import apply_fields, get_or_raise
from .exceptions import ConflictError, ValidationError

_SOURCE_FIELDS = {"name", "platform_type", "description", "connector_ref"}
_DEST_FIELDS = {"name", "platform", "platform_account_id", "config"}


async def _taken(session, model, branch_id, name, *, exclude_id=None) -> bool:
    stmt = select(model.id).where(model.branch_id == branch_id, model.name == name)
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


async def create_source(session: AsyncSession, branch: TPBranch, *, name: str, **fields: Any) -> TPSource:
    if not name or not name.strip():
        raise ValidationError("source name is required")
    name = name.strip()
    if await _taken(session, TPSource, branch.id, name):
        raise ConflictError(f"source '{name}' already exists")
    src = TPSource(plan_id=branch.plan_id, branch_id=branch.id, name=name)
    apply_fields(src, fields, _SOURCE_FIELDS - {"name"})
    session.add(src)
    await session.flush()
    return src


async def update_source(session: AsyncSession, branch: TPBranch, source_id: Any, **fields: Any) -> TPSource:
    src = await get_or_raise(session, TPSource, source_id, branch_id=branch.id)
    if "name" in fields:
        if not fields["name"] or not fields["name"].strip():
            raise ValidationError("source name cannot be empty")
        fields["name"] = fields["name"].strip()
        if await _taken(session, TPSource, branch.id, fields["name"], exclude_id=src.id):
            raise ConflictError(f"source '{fields['name']}' already exists")
    apply_fields(src, fields, _SOURCE_FIELDS)
    await session.flush()
    return src


async def delete_source(session: AsyncSession, branch: TPBranch, source_id: Any) -> None:
    src = await get_or_raise(session, TPSource, source_id, branch_id=branch.id)
    await session.delete(src)
    await session.flush()


async def create_destination(
    session: AsyncSession, branch: TPBranch, *, name: str, platform: str | None = None, **fields: Any
) -> TPDestination:
    if not name or not name.strip():
        raise ValidationError("destination name is required")
    if not platform or not platform.strip():
        raise ValidationError("destination platform is required")
    name = name.strip()
    if await _taken(session, TPDestination, branch.id, name):
        raise ConflictError(f"destination '{name}' already exists")
    dest = TPDestination(plan_id=branch.plan_id, branch_id=branch.id, name=name, platform=platform.strip())
    apply_fields(dest, fields, _DEST_FIELDS - {"name", "platform"})
    session.add(dest)
    await session.flush()
    return dest


async def update_destination(
    session: AsyncSession, branch: TPBranch, destination_id: Any, **fields: Any
) -> TPDestination:
    dest = await get_or_raise(session, TPDestination, destination_id, branch_id=branch.id)
    if "name" in fields:
        if not fields["name"] or not fields["name"].strip():
            raise ValidationError("destination name cannot be empty")
        fields["name"] = fields["name"].strip()
        if await _taken(session, TPDestination, branch.id, fields["name"], exclude_id=dest.id):
            raise ConflictError(f"destination '{fields['name']}' already exists")
    apply_fields(dest, fields, _DEST_FIELDS)
    await session.flush()
    return dest


async def delete_destination(session: AsyncSession, branch: TPBranch, destination_id: Any) -> None:
    dest = await get_or_raise(session, TPDestination, destination_id, branch_id=branch.id)
    await session.delete(dest)
    await session.flush()


async def connect_source_destination(
    session: AsyncSession, branch: TPBranch, source_id: Any, destination_id: Any
) -> TPSourceDestination:
    """Route a source to a destination. Idempotent."""
    src = await get_or_raise(session, TPSource, source_id, branch_id=branch.id)
    dest = await get_or_raise(session, TPDestination, destination_id, branch_id=branch.id)
    existing = await session.execute(
        select(TPSourceDestination).where(
            TPSourceDestination.source_id == src.id, TPSourceDestination.destination_id == dest.id
        )
    )
    route = existing.scalar_one_or_none()
    if route is None:
        route = TPSourceDestination(source_id=src.id, destination_id=dest.id)
        session.add(route)
        await session.flush()
    return route


async def disconnect_source_destination(
    session: AsyncSession, branch: TPBranch, source_id: Any, destination_id: Any
) -> None:
    src = await get_or_raise(session, TPSource, source_id, branch_id=branch.id)
    dest = await get_or_raise(session, TPDestination, destination_id, branch_id=branch.id)
    existing = await session.execute(
        select(TPSourceDestination).where(
            TPSourceDestination.source_id == src.id,
            TPSourceDestination.destination_id == dest.id,
        )
    )
    route = existing.scalar_one_or_none()
    if route is not None:
        await session.delete(route)
        await session.flush()
