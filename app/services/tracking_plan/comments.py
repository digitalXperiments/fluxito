# app/services/tracking_plan/comments.py
"""Comment threads on tracking-plan entities — branch-scoped.

Comments live on the branch where they were posted.  They are NOT
automatically migrated when a branch is merged into main; that is left as
future work (a post-merge "carry forward" pass).

Thread structure: top-level comments have parent_id=None; replies set
parent_id to the root (or any ancestor) comment.  The UI is expected to
reconstruct the visual thread from parent_id client-side.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import COMMENT_ENTITY_TYPES, TPBranch, TPComment

from .common import coerce_uuid, get_or_raise
from .exceptions import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def comment_to_dict(c: TPComment) -> dict:
    """Serialize a TPComment to a plain dict suitable for MCP/HTTP responses."""
    return {
        "id": str(c.id),
        "branch_id": str(c.branch_id),
        "entity_type": c.entity_type,
        "entity_id": str(c.entity_id),
        "parent_id": str(c.parent_id) if c.parent_id else None,
        "author_id": str(c.author_id),
        "body": c.body,
        "mentions": [str(m) for m in c.mentions] if c.mentions else None,
        "resolved": c.resolved,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


async def add_comment(
    session: AsyncSession,
    branch: TPBranch,
    *,
    entity_type: str,
    entity_id: Any,
    author_id: Any,
    body: str,
    parent_id: Any = None,
    mentions: list[Any] | None = None,
) -> TPComment:
    """Create a new comment (or threaded reply) on a branch entity.

    Validates:
    - entity_type is one of COMMENT_ENTITY_TYPES
    - body is non-blank
    - if parent_id is given, the parent exists on the same branch
    """
    if entity_type not in COMMENT_ENTITY_TYPES:
        raise ValidationError(f"entity_type must be one of {COMMENT_ENTITY_TYPES}, got {entity_type!r}")
    if not body or not body.strip():
        raise ValidationError("comment body cannot be blank")

    resolved_parent_id: uuid.UUID | None = None
    if parent_id is not None:
        parent = await get_or_raise(session, TPComment, parent_id, branch_id=branch.id)
        resolved_parent_id = parent.id

    resolved_mentions: list[uuid.UUID] | None = None
    if mentions:
        resolved_mentions = [coerce_uuid(m) for m in mentions]

    comment = TPComment(
        plan_id=branch.plan_id,
        branch_id=branch.id,
        entity_type=entity_type,
        entity_id=coerce_uuid(entity_id),
        parent_id=resolved_parent_id,
        author_id=coerce_uuid(author_id),
        body=body.strip(),
        mentions=resolved_mentions,
        resolved=False,
    )
    session.add(comment)
    await session.flush()
    return comment


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


async def list_comments(
    session: AsyncSession,
    branch: TPBranch,
    *,
    entity_type: str | None = None,
    entity_id: Any = None,
) -> list[TPComment]:
    """Return comments on this branch, optionally filtered to a specific entity.

    Results are ordered by created_at ascending so callers can thread them
    by parent_id in insertion order.
    """
    stmt = select(TPComment).where(TPComment.branch_id == branch.id)
    if entity_type is not None:
        stmt = stmt.where(TPComment.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(TPComment.entity_id == coerce_uuid(entity_id))
    stmt = stmt.order_by(TPComment.created_at)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Mutation operations (by comment id — no branch guard needed after load)
# ---------------------------------------------------------------------------


async def resolve_comment(
    session: AsyncSession,
    comment_id: Any,
    *,
    resolved: bool = True,
) -> TPComment:
    """Set the resolved flag on a comment (default: mark as resolved)."""
    comment = await get_or_raise(session, TPComment, comment_id)
    comment.resolved = resolved
    await session.flush()
    return comment


async def edit_comment(
    session: AsyncSession,
    comment_id: Any,
    *,
    body: str,
) -> TPComment:
    """Replace the body of an existing comment."""
    if not body or not body.strip():
        raise ValidationError("comment body cannot be blank")
    comment = await get_or_raise(session, TPComment, comment_id)
    comment.body = body.strip()
    await session.flush()
    return comment


async def delete_comment(
    session: AsyncSession,
    comment_id: Any,
) -> None:
    """Delete a comment.  Children (replies) cascade via the FK ondelete=CASCADE."""
    comment = await get_or_raise(session, TPComment, comment_id)
    await session.delete(comment)
    await session.flush()
