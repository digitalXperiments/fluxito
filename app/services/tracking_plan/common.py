# app/services/tracking_plan/common.py
"""Shared helpers for the tracking-plan service."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .exceptions import NotFoundError

# Sentinel meaning "caller did not provide this field" (vs. explicitly None).
_UNSET: Any = object()


def coerce_uuid(value: Any) -> uuid.UUID:
    """Accept a UUID or its string form; raise ValueError on garbage."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def get_or_raise(
    session: AsyncSession, model: type, obj_id: Any, *, branch_id: uuid.UUID | None = None
):
    """Load a row by id or raise NotFoundError. If branch_id is given, also
    require the row's branch_id to match (prevents cross-branch references)."""
    obj = await session.get(model, coerce_uuid(obj_id))
    if obj is None:
        raise NotFoundError(f"{model.__name__} {obj_id} not found")
    if branch_id is not None and getattr(obj, "branch_id", None) != branch_id:
        raise NotFoundError(f"{model.__name__} {obj_id} not on branch {branch_id}")
    return obj


def apply_fields(obj: Any, fields: dict[str, Any], allowed: set[str]) -> None:
    """Set attributes from `fields` whose key is in `allowed` and whose value is
    not the _UNSET sentinel. Lets update_* funcs distinguish 'omit' from 'set None'."""
    for key, value in fields.items():
        if key in allowed and value is not _UNSET:
            setattr(obj, key, value)
