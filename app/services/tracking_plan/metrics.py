# app/services/tracking_plan/metrics.py
"""Event-based metric CRUD."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import METRIC_TYPES, TPBranch, TPEvent, TPMetric, TPProperty

from .common import apply_fields, get_or_raise
from .exceptions import ConflictError, ValidationError

_METRIC_FIELDS = {"name", "description", "type", "event_id", "property_id", "filters"}


async def _metric_name_taken(session, branch_id, name, *, exclude_id=None) -> bool:
    stmt = select(TPMetric.id).where(TPMetric.branch_id == branch_id, TPMetric.name == name)
    if exclude_id is not None:
        stmt = stmt.where(TPMetric.id != exclude_id)
    return (await session.execute(stmt)).first() is not None


def _validate_type(metric_type: str) -> None:
    if metric_type not in METRIC_TYPES:
        raise ValidationError(f"metric type must be one of {METRIC_TYPES}, got {metric_type!r}")


async def create_metric(
    session: AsyncSession,
    branch: TPBranch,
    *,
    name: str,
    type: str = "count",
    description: str | None = None,
    event_id: Any = None,
    property_id: Any = None,
    filters: dict | None = None,
) -> TPMetric:
    if not name or not name.strip():
        raise ValidationError("metric name is required")
    _validate_type(type)
    name = name.strip()
    if await _metric_name_taken(session, branch.id, name):
        raise ConflictError(f"metric '{name}' already exists")
    ev_id = None
    if event_id is not None:
        ev_id = (await get_or_raise(session, TPEvent, event_id, branch_id=branch.id)).id
    prop_id = None
    if property_id is not None:
        prop_id = (await get_or_raise(session, TPProperty, property_id, branch_id=branch.id)).id
    metric = TPMetric(
        plan_id=branch.plan_id,
        branch_id=branch.id,
        name=name,
        type=type,
        description=description,
        event_id=ev_id,
        property_id=prop_id,
        filters=filters,
    )
    session.add(metric)
    await session.flush()
    return metric


async def update_metric(session: AsyncSession, branch: TPBranch, metric_id: Any, **fields: Any) -> TPMetric:
    metric = await get_or_raise(session, TPMetric, metric_id, branch_id=branch.id)
    if "type" in fields:
        _validate_type(fields["type"])
    if "name" in fields:
        if not fields["name"] or not fields["name"].strip():
            raise ValidationError("metric name cannot be empty")
        fields["name"] = fields["name"].strip()
        if await _metric_name_taken(session, branch.id, fields["name"], exclude_id=metric.id):
            raise ConflictError(f"metric '{fields['name']}' already exists")
    if fields.get("event_id"):
        await get_or_raise(session, TPEvent, fields["event_id"], branch_id=branch.id)
    if fields.get("property_id"):
        await get_or_raise(session, TPProperty, fields["property_id"], branch_id=branch.id)
    apply_fields(metric, fields, _METRIC_FIELDS)
    await session.flush()
    return metric


async def delete_metric(session: AsyncSession, branch: TPBranch, metric_id: Any) -> None:
    metric = await get_or_raise(session, TPMetric, metric_id, branch_id=branch.id)
    await session.delete(metric)
    await session.flush()
