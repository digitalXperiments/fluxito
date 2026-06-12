# app/services/tracking_plan/bootstrap.py
"""Plan + branch lifecycle. Phase 1 guarantees exactly one `main` branch."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import TPBranch, TPPlan

from .common import coerce_uuid
from .exceptions import NotFoundError


async def get_or_create_plan(
    session: AsyncSession, *, project_id: Any, user_id: Any, name: str | None = None
) -> TPPlan:
    """Return the project's plan, creating it (and its `main` branch) if absent."""
    pid = coerce_uuid(project_id)
    existing = await session.execute(select(TPPlan).where(TPPlan.project_id == pid))
    plan = existing.scalar_one_or_none()
    if plan is not None:
        return plan

    uid = coerce_uuid(user_id)
    plan = TPPlan(project_id=pid, name=name or "Tracking Plan", created_by=uid)
    session.add(plan)
    await session.flush()  # populate plan.id

    main = TPBranch(plan_id=plan.id, name="main", is_main=True, created_by=uid)
    session.add(main)
    await session.flush()  # populate main.id

    plan.default_branch_id = main.id
    await session.flush()
    return plan


async def get_main_branch(session: AsyncSession, plan: TPPlan) -> TPBranch:
    """Return the plan's `main` branch or raise NotFoundError."""
    result = await session.execute(
        select(TPBranch).where(TPBranch.plan_id == plan.id, TPBranch.is_main.is_(True))
    )
    branch = result.scalar_one_or_none()
    if branch is None:
        raise NotFoundError(f"main branch missing for plan {plan.id}")
    return branch
