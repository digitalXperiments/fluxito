# app/services/tracking_plan/taxonomy.py
"""Category (event grouping) CRUD."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import TPBranch, TPCategory

from .common import _UNSET, apply_fields, get_or_raise
from .exceptions import ConflictError, ValidationError

_CATEGORY_FIELDS = {"name", "description", "color"}


async def _name_taken(session: AsyncSession, branch_id, name: str, *, exclude_id=None) -> bool:
    stmt = select(TPCategory.id).where(TPCategory.branch_id == branch_id, TPCategory.name == name)
    if exclude_id is not None:
        stmt = stmt.where(TPCategory.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


async def create_category(
    session: AsyncSession,
    branch: TPBranch,
    *,
    name: str,
    description: str | None = None,
    color: str | None = None,
) -> TPCategory:
    if not name or not name.strip():
        raise ValidationError("category name is required")
    name = name.strip()
    if await _name_taken(session, branch.id, name):
        raise ConflictError(f"category '{name}' already exists")
    cat = TPCategory(
        plan_id=branch.plan_id,
        branch_id=branch.id,
        name=name,
        description=description,
        color=color,
    )
    session.add(cat)
    await session.flush()
    return cat


async def update_category(
    session: AsyncSession, branch: TPBranch, category_id: Any, **fields: Any
) -> TPCategory:
    cat = await get_or_raise(session, TPCategory, category_id, branch_id=branch.id)
    new_name = fields.get("name", _UNSET)
    if new_name is not _UNSET:
        if not new_name or not new_name.strip():
            raise ValidationError("category name cannot be empty")
        fields["name"] = new_name.strip()
        if await _name_taken(session, branch.id, fields["name"], exclude_id=cat.id):
            raise ConflictError(f"category '{fields['name']}' already exists")
    apply_fields(cat, fields, _CATEGORY_FIELDS)
    await session.flush()
    return cat


async def delete_category(session: AsyncSession, branch: TPBranch, category_id: Any) -> None:
    cat = await get_or_raise(session, TPCategory, category_id, branch_id=branch.id)
    await session.delete(cat)
    await session.flush()
