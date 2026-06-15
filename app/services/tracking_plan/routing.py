# app/services/tracking_plan/routing.py
"""Source + destination CRUD and source->destination routing."""

import logging
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

logger = logging.getLogger(__name__)

_SOURCE_FIELDS = {"name", "platform_type", "description", "connector_ref"}
_DEST_FIELDS = {"name", "platform", "platform_account_id", "config"}


def _normalize_slug(value: str) -> str:
    """Trim whitespace and lowercase a platform slug."""
    return value.strip().lower()


def _check_dest_platform(platform: str) -> dict | None:
    """Warn if *platform* is not in the vendor catalog.

    Returns a ``config`` overlay dict (``{"custom": True}``) when the slug is
    off-catalog so callers can stamp it onto the destination row.  Returns
    ``None`` when the slug is known (no overlay needed).  Never raises —
    unknown slugs are accepted (warn-only, Option A).
    """
    try:
        from .vendors import is_known_slug

        if not is_known_slug(platform):
            logger.warning(
                "tracking_plan.routing: destination platform %r is not in the vendor catalog "
                "(persisting as custom)",
                platform,
            )
            return {"custom": True}
    except Exception:
        logger.debug("tracking_plan.routing: vendor catalog unavailable; skipping slug check")
    return None


async def _taken(session, model, branch_id, name, *, exclude_id=None) -> bool:
    stmt = select(model.id).where(model.branch_id == branch_id, model.name == name)
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


def _build_connector_ref(fields: dict[str, Any]) -> dict[str, Any]:
    """Merge a ``vendor_slug`` field into ``connector_ref`` JSONB.

    When the caller supplies a ``vendor_slug`` key we store it (plus a
    ``custom`` flag when the slug is off-catalog) inside the existing
    ``connector_ref`` JSONB column — no migration required.  The
    ``vendor_slug`` key is consumed here and must NOT be forwarded to
    ``apply_fields`` (it is not a model column).
    """
    if "vendor_slug" not in fields:
        return fields

    vendor_slug_raw = fields.pop("vendor_slug")  # remove non-column key
    if not vendor_slug_raw:
        return fields

    vendor_slug = _normalize_slug(str(vendor_slug_raw))

    # Determine whether this is a known catalog slug
    is_custom = True
    try:
        from .vendors import is_known_slug

        is_custom = not is_known_slug(vendor_slug)
    except Exception:
        pass

    if is_custom:
        logger.warning(
            "tracking_plan.routing: source vendor_slug %r is not in the vendor catalog "
            "(persisting as custom)",
            vendor_slug,
        )

    ref: dict = dict(fields.get("connector_ref") or {})
    ref["vendor_slug"] = vendor_slug
    ref["custom"] = is_custom
    fields = {**fields, "connector_ref": ref}
    return fields


async def create_source(session: AsyncSession, branch: TPBranch, *, name: str, **fields: Any) -> TPSource:
    if not name or not name.strip():
        raise ValidationError("source name is required")
    name = name.strip()
    if await _taken(session, TPSource, branch.id, name):
        raise ConflictError(f"source '{name}' already exists")
    fields = _build_connector_ref(fields)
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
    fields = _build_connector_ref(fields)
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
    platform = _normalize_slug(platform)
    if await _taken(session, TPDestination, branch.id, name):
        raise ConflictError(f"destination '{name}' already exists")
    # Warn-only vendor check (Option A): stamp config={"custom": True} when
    # the slug is off-catalog but always persist.
    custom_overlay = _check_dest_platform(platform)
    if custom_overlay is not None and "config" not in fields:
        fields = {**fields, "config": custom_overlay}
    dest = TPDestination(plan_id=branch.plan_id, branch_id=branch.id, name=name, platform=platform)
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
    if "platform" in fields:
        if not fields["platform"] or not str(fields["platform"]).strip():
            raise ValidationError("destination platform cannot be empty")
        fields["platform"] = _normalize_slug(str(fields["platform"]))
        # Warn-only vendor check (Option A): stamp config={"custom": True} when
        # the new slug is off-catalog, but only if the caller didn't also send
        # an explicit config override.
        custom_overlay = _check_dest_platform(fields["platform"])
        if custom_overlay is not None and "config" not in fields:
            fields = {**fields, "config": custom_overlay}
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
