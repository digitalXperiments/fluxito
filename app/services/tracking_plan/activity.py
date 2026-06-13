"""Append-only tracking-plan activity log: one writer, branch-scoped reads."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.tracking_plan import TPActivity


def record_activity(
    session: Any,
    *,
    plan_id: uuid.UUID,
    branch_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    action: str,
    summary: str,
) -> None:
    """Append one activity row to the session (caller owns the commit)."""
    session.add(
        TPActivity(
            plan_id=plan_id,
            branch_id=branch_id,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            action=action,
            summary=summary,
        )
    )


async def list_activity(
    session: Any,
    branch: Any,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 100,
) -> list[TPActivity]:
    """Most-recent-first activity for a branch, optionally scoped to one entity."""
    stmt = select(TPActivity).where(
        TPActivity.plan_id == branch.plan_id,
        TPActivity.branch_id == branch.id,
    )
    if entity_type:
        stmt = stmt.where(TPActivity.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(TPActivity.entity_id == entity_id)
    stmt = stmt.order_by(TPActivity.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


def activity_to_dict(a: TPActivity) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "entity_type": a.entity_type,
        "entity_id": str(a.entity_id) if a.entity_id else None,
        "actor_id": str(a.actor_id) if a.actor_id else None,
        "action": a.action,
        "summary": a.summary,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
