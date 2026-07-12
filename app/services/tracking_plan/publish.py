# app/services/tracking_plan/publish.py
"""Publish a branch as an immutable version snapshot (JSONB plan_to_dict)."""

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import TPBranch, TPPlan, TPVersion

from .common import coerce_uuid
from .exceptions import ValidationError
from .serializer import plan_to_dict
from .validation import validate_plan


def _next_version_number(latest: str | None) -> str:
    """Minor-bump versioning. '1.0' if none/garbage; else major.(minor+1)."""
    if not latest:
        return "1.0"
    parts = latest.split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return "1.0"
    return f"{major}.{minor + 1}"


async def publish_branch(
    session: AsyncSession, plan: TPPlan, branch: TPBranch, *, user_id: Any, changelog: str | None = None
) -> TPVersion:
    """Snapshot the branch into a new immutable version and point the plan at it.

    Raises ValidationError if the branch has any error-severity findings (blocking
    issues that must be resolved before the plan can be published).
    """
    report = await validate_plan(session, plan, branch)
    blocking = [f for f in report["findings"] if f.get("severity") == "error"]
    if blocking:
        raise ValidationError(
            f"Cannot publish: {len(blocking)} blocking (error-severity) issue(s) must be resolved first."
        )

    latest = (
        (
            await session.execute(
                select(TPVersion.version_number)
                .where(TPVersion.plan_id == plan.id)
                .order_by(desc(TPVersion.published_at))
            )
        )
        .scalars()
        .first()
    )

    # Snapshots are immutable plan definition — exclude volatile live drift data.
    snapshot = await plan_to_dict(session, plan, branch, include_drift=False)
    version_number = _next_version_number(latest)
    snapshot["__version__"] = version_number
    version = TPVersion(
        plan_id=plan.id,
        branch_id=branch.id,
        version_number=version_number,
        snapshot=snapshot,
        changelog=changelog,
        published_by=coerce_uuid(user_id),
    )
    session.add(version)
    await session.flush()
    plan.current_version_id = version.id
    await session.flush()
    return version


async def latest_snapshot_for_project(session: AsyncSession, project_id: Any) -> dict | None:
    """Return the most-recently-published snapshot dict for a project, or None.
    Used by downstream consumers (audit, tag testing) in Plan 1D."""
    plan = (
        await session.execute(select(TPPlan).where(TPPlan.project_id == coerce_uuid(project_id)))
    ).scalar_one_or_none()
    if plan is None or plan.current_version_id is None:
        return None
    version = await session.get(TPVersion, plan.current_version_id)
    return version.snapshot if version is not None else None
