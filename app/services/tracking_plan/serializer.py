# app/services/tracking_plan/serializer.py
"""plan_to_dict — the canonical structured read shape for a plan/branch.

Queries every table explicitly (no ORM lazy-loading under async) and assembles
a stable, JSON-serializable dict. This is the single source consumed by MCP
reads, the UI, markdown/xlsx export, and tp_versions snapshots."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import (
    TPBranch,
    TPBundleProperty,
    TPCategory,
    TPDestination,
    TPEvent,
    TPEventDestination,
    TPEventProperty,
    TPEventSource,
    TPMetric,
    TPPlan,
    TPProperty,
    TPPropertyBundle,
    TPPropertyMember,
    TPSource,
    TPSourceDestination,
)

# Maximum recursion depth when building the members tree inside _property_dict.
_MEMBERS_MAX_DEPTH = 6


async def plan_to_dict(session: AsyncSession, plan: TPPlan, branch: TPBranch) -> dict:
    bid = branch.id

    async def rows(model):
        result = await session.execute(select(model).where(model.branch_id == bid))
        return list(result.scalars().all())

    categories = await rows(TPCategory)
    events = await rows(TPEvent)
    properties = await rows(TPProperty)
    sources = await rows(TPSource)
    destinations = await rows(TPDestination)
    metrics = await rows(TPMetric)
    bundles = await rows(TPPropertyBundle)

    # Load all property-member links for this branch in one query.
    prop_ids = [p.id for p in properties]
    pm_rows: list[TPPropertyMember] = []
    if prop_ids:
        pm_result = await session.execute(
            select(TPPropertyMember)
            .where(TPPropertyMember.parent_property_id.in_(prop_ids))
            .order_by(TPPropertyMember.sort_order)
        )
        pm_rows = list(pm_result.scalars().all())

    # parent_property_id -> sorted list of member links
    members_by_parent: dict[uuid.UUID, list[TPPropertyMember]] = {}
    for pm in pm_rows:
        members_by_parent.setdefault(pm.parent_property_id, []).append(pm)

    cat_by_id = {c.id: c for c in categories}
    prop_by_id = {p.id: p for p in properties}
    src_by_id = {s.id: s for s in sources}
    dest_by_id = {d.id: d for d in destinations}
    event_ids = [e.id for e in events]
    source_ids = [s.id for s in sources]

    # Child rows scoped via parents (filter by the branch's event/source ids)
    def _by_event(items):
        out: dict = {}
        for it in items:
            out.setdefault(it.event_id, []).append(it)
        return out

    ep_rows = (
        (await session.execute(select(TPEventProperty).where(TPEventProperty.event_id.in_(event_ids))))
        .scalars()
        .all()
        if event_ids
        else []
    )
    es_rows = (
        (await session.execute(select(TPEventSource).where(TPEventSource.event_id.in_(event_ids))))
        .scalars()
        .all()
        if event_ids
        else []
    )
    ed_rows = (
        (await session.execute(select(TPEventDestination).where(TPEventDestination.event_id.in_(event_ids))))
        .scalars()
        .all()
        if event_ids
        else []
    )
    sd_rows = (
        (
            await session.execute(
                select(TPSourceDestination).where(TPSourceDestination.source_id.in_(source_ids))
            )
        )
        .scalars()
        .all()
        if source_ids
        else []
    )

    bundle_ids = [b.id for b in bundles]
    bp_rows = (
        (
            await session.execute(
                select(TPBundleProperty)
                .where(TPBundleProperty.bundle_id.in_(bundle_ids))
                .order_by(TPBundleProperty.sort_order)
            )
        )
        .scalars()
        .all()
        if bundle_ids
        else []
    )
    bp_by_bundle: dict = {}
    for bp in bp_rows:
        bp_by_bundle.setdefault(bp.bundle_id, []).append(bp)

    ep_by_event = _by_event(ep_rows)
    es_by_event = _by_event(es_rows)
    ed_by_event = _by_event(ed_rows)
    routes_by_source: dict = {}
    for r in sd_rows:
        routes_by_source.setdefault(r.source_id, []).append(r.destination_id)

    def _build_members_tree(
        prop_id: uuid.UUID,
        visited: set[uuid.UUID],
        depth: int,
    ) -> list[dict]:
        """Recursively build the members list for an object property.

        visited is per-branch (passed by reference from the outermost call)
        to deduplicate and prevent infinite loops on shared-reference cycles.
        depth is capped at _MEMBERS_MAX_DEPTH.
        """
        if depth >= _MEMBERS_MAX_DEPTH:
            return []
        links = members_by_parent.get(prop_id, [])
        result = []
        for lk in links:
            mid = lk.member_property_id
            if mid not in prop_by_id:
                # Member property not in this branch (shouldn't happen, but be safe).
                continue
            member_prop = prop_by_id[mid]
            if mid in visited:
                # Cycle guard: emit leaf without recursing.
                result.append(
                    {
                        "member_property_id": str(mid),
                        "name": member_prop.name,
                        "data_type": member_prop.data_type,
                        "is_list": member_prop.is_list,
                        "required": lk.required,
                        "sort_order": lk.sort_order,
                        "members": [],
                    }
                )
                continue
            visited.add(mid)
            nested: list[dict] = []
            if member_prop.data_type == "object":
                nested = _build_members_tree(mid, visited, depth + 1)
            visited.discard(mid)
            result.append(
                {
                    "member_property_id": str(mid),
                    "name": member_prop.name,
                    "data_type": member_prop.data_type,
                    "is_list": member_prop.is_list,
                    "required": lk.required,
                    "sort_order": lk.sort_order,
                    "members": nested,
                }
            )
        return result

    def _property_dict(p: TPProperty) -> dict:
        d: dict = {
            "id": str(p.id),
            "name": p.name,
            "kind": p.kind,
            "data_type": p.data_type,
            "description": p.description,
            "constraints": p.constraints,
            "is_pii": p.is_pii,
            "is_list": p.is_list,
        }
        if p.data_type == "object":
            d["members"] = _build_members_tree(p.id, visited={p.id}, depth=0)
        else:
            d["members"] = []
        return d

    def _metric_dict(m: TPMetric) -> dict:
        """Emit only the lightweight metric fields (measurement columns were dropped in 064)."""
        event_name = next((e.name for e in events if e.id == m.event_id), None)
        d: dict = {
            "id": str(m.id),
            "name": m.name,
            "description": m.description,
            "event_id": str(m.event_id) if m.event_id else None,
        }
        if event_name is not None:
            d["event_name"] = event_name
        return d

    def _event_dict(e: TPEvent) -> dict:
        attached = sorted(
            ep_by_event.get(e.id, []), key=lambda link: (link.sort_order, str(link.property_id))
        )

        def _attached_prop_dict(link) -> dict:
            p = prop_by_id[link.property_id]
            members: list[dict] = []
            if p.data_type == "object":
                members = _build_members_tree(p.id, visited={p.id}, depth=0)
            return {
                "property_id": str(p.id),
                "name": p.name,
                "data_type": p.data_type,
                "is_list": p.is_list,
                "required": link.required,
                "example": link.example,
                "override_description": link.override_description,
                "members": members,
            }

        return {
            "id": str(e.id),
            "name": e.name,
            "display_name": e.display_name,
            "description": e.description,
            "category": cat_by_id[e.category_id].name if e.category_id in cat_by_id else None,
            "tags": e.tags or [],
            "trigger_type": e.trigger_type,
            "trigger_config": e.trigger_config,
            "purpose": e.purpose,
            "owner_business": e.owner_business,
            "owner_technical": e.owner_technical,
            "consent_required": e.consent_required or [],
            "properties": [_attached_prop_dict(link) for link in attached if link.property_id in prop_by_id],
            "sources": [
                {
                    "name": src_by_id[link.source_id].name,
                    "implementation_status": link.implementation_status,
                }
                for link in es_by_event.get(e.id, [])
                if link.source_id in src_by_id
            ],
            "destinations": [
                {
                    "destination": dest_by_id[link.destination_id].name,
                    "dest_event_name": link.dest_event_name,
                    "property_mappings": link.property_mappings,
                    "enabled": link.enabled,
                    "notes": link.notes,
                }
                for link in ed_by_event.get(e.id, [])
                if link.destination_id in dest_by_id
            ],
        }

    def _bundle_dict(b: TPPropertyBundle) -> dict:
        return {
            "id": str(b.id),
            "name": b.name,
            "description": b.description,
            "properties": [
                {
                    "property_id": str(bp.property_id),
                    "name": prop_by_id[bp.property_id].name,
                    "data_type": prop_by_id[bp.property_id].data_type,
                    "required": bp.required,
                    "sort_order": bp.sort_order,
                }
                for bp in bp_by_bundle.get(b.id, [])
                if bp.property_id in prop_by_id
            ],
        }

    return {
        "plan": {
            "id": str(plan.id),
            "project_id": str(plan.project_id),
            "name": plan.name,
            "description": plan.description,
            "current_version_id": str(plan.current_version_id) if plan.current_version_id else None,
        },
        "branch": {"id": str(branch.id), "name": branch.name, "is_main": branch.is_main},
        "categories": [
            {"id": str(c.id), "name": c.name, "description": c.description, "color": c.color}
            for c in categories
        ],
        "events": [_event_dict(e) for e in sorted(events, key=lambda x: x.name)],
        # Shared pool: ALL properties of each kind (members still appear here — that's correct by design).
        "properties": {
            "event": [_property_dict(p) for p in properties if p.kind == "event"],
            "user": [_property_dict(p) for p in properties if p.kind == "user"],
            "group": [_property_dict(p) for p in properties if p.kind == "group"],
            "system": [_property_dict(p) for p in properties if p.kind == "system"],
        },
        "sources": [
            {
                "id": str(s.id),
                "name": s.name,
                "platform_type": s.platform_type,
                "description": s.description,
                "connector_ref": s.connector_ref,
                "destinations": sorted(
                    dest_by_id[d].name for d in routes_by_source.get(s.id, []) if d in dest_by_id
                ),
            }
            for s in sorted(sources, key=lambda x: x.name)
        ],
        "destinations": [
            {
                "id": str(d.id),
                "name": d.name,
                "platform": d.platform,
                "platform_account_id": d.platform_account_id,
                "config": d.config,
            }
            for d in sorted(destinations, key=lambda x: x.name)
        ],
        "metrics": [_metric_dict(m) for m in sorted(metrics, key=lambda x: x.name)],
        "bundles": [_bundle_dict(b) for b in sorted(bundles, key=lambda x: x.name)],
    }
