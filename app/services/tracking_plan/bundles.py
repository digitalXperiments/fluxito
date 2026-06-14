# app/services/tracking_plan/bundles.py
"""Property bundles — named, reusable groups of properties added to events together.

Bundles are **template-copy**: ``attach_bundle_to_event`` copies each bundle
property into ``tp_event_properties`` (carrying the link's ``required`` flag) at
attach time. Editing a bundle afterwards does NOT retroactively change events it
was already attached to — that is a deliberate MVP choice; a live-link model
(events follow bundle edits) is future work.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    TPBranch,
    TPBundleProperty,
    TPEvent,
    TPEventProperty,
    TPProperty,
    TPPropertyBundle,
)

from .common import _UNSET, apply_fields, coerce_uuid, get_or_raise
from .exceptions import ConflictError, NotFoundError, ValidationError

_BUNDLE_FIELDS = {"name", "description"}


async def _bundle_name_taken(session, branch_id, name, *, exclude_id=None) -> bool:
    stmt = select(TPPropertyBundle.id).where(
        TPPropertyBundle.branch_id == branch_id, TPPropertyBundle.name == name
    )
    if exclude_id is not None:
        stmt = stmt.where(TPPropertyBundle.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


# ---------------------------------------------------------------------------
# Bundle CRUD
# ---------------------------------------------------------------------------


async def create_bundle(
    session: AsyncSession,
    branch: TPBranch,
    *,
    name: str,
    description: str | None = None,
) -> TPPropertyBundle:
    if not name or not name.strip():
        raise ValidationError("bundle name is required")
    name = name.strip()
    if await _bundle_name_taken(session, branch.id, name):
        raise ConflictError(f"bundle '{name}' already exists")
    bundle = TPPropertyBundle(
        plan_id=branch.plan_id,
        branch_id=branch.id,
        name=name,
        description=description,
    )
    session.add(bundle)
    await session.flush()
    return bundle


async def update_bundle(
    session: AsyncSession, branch: TPBranch, bundle_id: Any, **fields: Any
) -> TPPropertyBundle:
    bundle = await get_or_raise(session, TPPropertyBundle, bundle_id, branch_id=branch.id)
    new_name = fields.get("name", _UNSET)
    if new_name is not _UNSET:
        if not new_name or not new_name.strip():
            raise ValidationError("bundle name cannot be empty")
        fields["name"] = new_name.strip()
        if await _bundle_name_taken(session, branch.id, fields["name"], exclude_id=bundle.id):
            raise ConflictError(f"bundle '{fields['name']}' already exists")
    apply_fields(bundle, fields, _BUNDLE_FIELDS)
    await session.flush()
    return bundle


async def delete_bundle(session: AsyncSession, branch: TPBranch, bundle_id: Any) -> None:
    bundle = await get_or_raise(session, TPPropertyBundle, bundle_id, branch_id=branch.id)
    await session.delete(bundle)
    await session.flush()


async def list_bundles(session: AsyncSession, branch: TPBranch) -> list[TPPropertyBundle]:
    stmt = (
        select(TPPropertyBundle)
        .where(TPPropertyBundle.branch_id == branch.id)
        .order_by(TPPropertyBundle.name)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Bundle <-> property links
# ---------------------------------------------------------------------------


async def add_property_to_bundle(
    session: AsyncSession,
    branch: TPBranch,
    bundle_id: Any,
    property_id: Any,
    *,
    required: bool = False,
    sort_order: int = 0,
) -> TPBundleProperty:
    """Idempotent upsert of a bundle<->property link. Both the bundle and the
    property must live on ``branch``."""
    bundle = await get_or_raise(session, TPPropertyBundle, bundle_id, branch_id=branch.id)
    prop = await get_or_raise(session, TPProperty, property_id, branch_id=branch.id)

    existing = await session.execute(
        select(TPBundleProperty).where(
            TPBundleProperty.bundle_id == bundle.id,
            TPBundleProperty.property_id == prop.id,
        )
    )
    link = existing.scalar_one_or_none()
    if link is None:
        link = TPBundleProperty(bundle_id=bundle.id, property_id=prop.id)
        session.add(link)
    link.required = required
    link.sort_order = sort_order
    await session.flush()
    return link


async def remove_property_from_bundle(
    session: AsyncSession, branch: TPBranch, bundle_id: Any, property_id: Any
) -> None:
    bundle = await get_or_raise(session, TPPropertyBundle, bundle_id, branch_id=branch.id)
    existing = await session.execute(
        select(TPBundleProperty).where(
            TPBundleProperty.bundle_id == bundle.id,
            TPBundleProperty.property_id == coerce_uuid(property_id),
        )
    )
    link = existing.scalar_one_or_none()
    if link is None:
        raise NotFoundError(f"property {property_id} is not in bundle {bundle_id}")
    await session.delete(link)
    await session.flush()


# ---------------------------------------------------------------------------
# Attach bundle to event (template-copy)
# ---------------------------------------------------------------------------


async def attach_bundle_to_event(
    session: AsyncSession, branch: TPBranch, event_id: Any, bundle_id: Any
) -> list[TPEventProperty]:
    """Copy each property in the bundle onto the event as a ``tp_event_properties``
    link (carrying the bundle link's ``required`` and ``sort_order``). Idempotent:
    properties already attached to the event are skipped (left untouched).

    Returns the full set of event-property links for the bundle's properties on
    this event (both freshly-created and pre-existing)."""
    event = await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)
    bundle = await get_or_raise(session, TPPropertyBundle, bundle_id, branch_id=branch.id)

    bp_rows = (
        (
            await session.execute(
                select(TPBundleProperty)
                .where(TPBundleProperty.bundle_id == bundle.id)
                .order_by(TPBundleProperty.sort_order)
            )
        )
        .scalars()
        .all()
    )
    if not bp_rows:
        return []

    prop_ids = [bp.property_id for bp in bp_rows]
    existing_rows = (
        (
            await session.execute(
                select(TPEventProperty).where(
                    TPEventProperty.event_id == event.id,
                    TPEventProperty.property_id.in_(prop_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_prop = {ep.property_id: ep for ep in existing_rows}

    links: list[TPEventProperty] = []
    for bp in bp_rows:
        link = existing_by_prop.get(bp.property_id)
        if link is None:
            link = TPEventProperty(
                event_id=event.id,
                property_id=bp.property_id,
                required=bp.required,
                sort_order=bp.sort_order,
            )
            session.add(link)
        links.append(link)
    await session.flush()
    return links


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


async def bundle_to_dict(session: AsyncSession, bundle: TPPropertyBundle) -> dict:
    """Serialize a bundle and its properties (ordered by sort_order) to a dict."""
    bp_rows = (
        (
            await session.execute(
                select(TPBundleProperty)
                .where(TPBundleProperty.bundle_id == bundle.id)
                .order_by(TPBundleProperty.sort_order)
            )
        )
        .scalars()
        .all()
    )
    prop_ids = [bp.property_id for bp in bp_rows]
    props_by_id: dict = {}
    if prop_ids:
        prop_rows = (
            (await session.execute(select(TPProperty).where(TPProperty.id.in_(prop_ids)))).scalars().all()
        )
        props_by_id = {p.id: p for p in prop_rows}

    properties = []
    for bp in bp_rows:
        prop = props_by_id.get(bp.property_id)
        if prop is None:
            continue
        properties.append(
            {
                "property_id": str(bp.property_id),
                "name": prop.name,
                "data_type": prop.data_type,
                "required": bp.required,
                "sort_order": bp.sort_order,
            }
        )
    return {
        "id": str(bundle.id),
        "name": bundle.name,
        "description": bundle.description,
        "properties": properties,
    }
