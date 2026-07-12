# app/services/tracking_plan/drift/service.py
"""Drift orchestrator: resolve → GA4 tier → BigQuery tier → persist.

``compute_drift(project_id)`` recomputes one project's drift in a single
transaction (used by both the daily job and the manual "refresh" action).
``run_drift_computation()`` iterates every project with drift enabled, isolating
per-project failures.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.app_state as app_state
from app.models.tracking_plan import (
    TPDriftConfig,
    TPEventDrift,
    TPParamObservation,
    TPPlan,
)
from app.services.tracking_plan.bootstrap import get_main_branch
from app.services.tracking_plan.serializer import plan_to_dict

from .bq_drift import ParamObsRow, fetch_param_rows, parse_param_rows
from .ga4_drift import EventDriftRow, diff_events, fetch_live_events
from .resolve import resolve_drift_targets

logger = logging.getLogger(__name__)


def _apply_param_tier(
    drift_rows: dict[str, EventDriftRow],
    param_obs: list[ParamObsRow],
    plan_event_names: set[str],
) -> dict[str, float | None]:
    """Fold BigQuery observations into event drift: coverage % + unplanned → drifted.

    Returns per-event coverage so the caller can persist it. Mutates ``drift_rows``
    in place, seeding rows for events that had BQ data but no GA4 row.
    """
    by_event: dict[str, list[ParamObsRow]] = {}
    for o in param_obs:
        by_event.setdefault(o.event_name, []).append(o)

    coverage: dict[str, float | None] = {}
    for event_name, obs in by_event.items():
        planned_pcts = [o.present_pct for o in obs if not o.is_unplanned and o.present_pct is not None]
        cov = round(sum(planned_pcts) / len(planned_pcts), 2) if planned_pcts else None
        coverage[event_name] = cov

        unplanned = [o.param_key for o in obs if o.is_unplanned]
        row = drift_rows.get(event_name)
        if row is None:
            # BigQuery saw the event but GA4 tier didn't run / didn't include it.
            row = EventDriftRow(event_name=event_name, status="in_plan", source="bq")
            drift_rows[event_name] = row
        if unplanned and event_name in plan_event_names:
            row.status = "drifted"
            keys = ", ".join(sorted(unplanned)[:5])
            row.reasons.append(f"Live sends unplanned parameter(s): {keys}.")
    return coverage


async def compute_drift(session: AsyncSession, project_id: uuid.UUID) -> dict:
    """Recompute and persist drift for one project. Caller commits.

    No-op (returns ``{"skipped": ...}``) when the project has no plan or no GA4/BQ
    target — we never write speculative "broken" rows just because analytics isn't
    connected.
    """
    plan = (await session.execute(select(TPPlan).where(TPPlan.project_id == project_id))).scalar_one_or_none()
    if plan is None:
        return {"skipped": "no_plan"}

    existing_cfg = (
        await session.execute(select(TPDriftConfig).where(TPDriftConfig.project_id == project_id))
    ).scalar_one_or_none()
    if existing_cfg is not None and not existing_cfg.enabled:
        return {"skipped": "disabled"}

    branch = await get_main_branch(session, plan)
    data = await plan_to_dict(session, plan, branch)
    events = data.get("events") or []
    plan_event_names = {e["name"] for e in events}
    plan_params_by_event = {e["name"]: {p["name"] for p in (e.get("properties") or [])} for e in events}

    targets = await resolve_drift_targets(session, project_id)
    if not targets.has_ga4 and not targets.has_bq:
        targets.config.last_run_at = datetime.now(UTC)
        targets.config.last_error = "No GA4 or BigQuery target resolved."
        return {"skipped": "no_target"}

    # Tier 1 — GA4 event volume / firing. Only diff when we actually have live data,
    # otherwise every plan event would be spuriously flagged "broken".
    drift_rows: dict[str, EventDriftRow] = {}
    if targets.has_ga4:
        live = await fetch_live_events(targets.ga4_connection_id, targets.ga4_property_id)
        drift_rows = diff_events(plan_event_names, live)

    # Tier 2 — BigQuery per-parameter fill-rate + unplanned params.
    param_obs: list[ParamObsRow] = []
    if targets.has_bq and plan_event_names:
        rows = await fetch_param_rows(
            targets.bq_conn, targets.bq_conn.project_id, targets.bq_dataset, sorted(plan_event_names)
        )
        param_obs = parse_param_rows(rows, plan_params_by_event)
    coverage = _apply_param_tier(drift_rows, param_obs, plan_event_names)

    _persist(session, plan.id, drift_rows, coverage, param_obs)
    targets.config.last_run_at = datetime.now(UTC)
    targets.config.last_error = None
    return {
        "events": len(drift_rows),
        "unplanned_params": sum(1 for o in param_obs if o.is_unplanned),
        "ga4": targets.has_ga4,
        "bq": targets.has_bq,
    }


def _persist(
    session: AsyncSession,
    plan_id: uuid.UUID,
    drift_rows: dict[str, EventDriftRow],
    coverage: dict[str, float | None],
    param_obs: list[ParamObsRow],
) -> None:
    """Replace-all: drift rows are cache, cheapest to rebuild wholesale per run."""
    now = datetime.now(UTC)
    session.execute(delete(TPEventDrift).where(TPEventDrift.plan_id == plan_id))
    session.execute(delete(TPParamObservation).where(TPParamObservation.plan_id == plan_id))

    for row in drift_rows.values():
        session.add(
            TPEventDrift(
                plan_id=plan_id,
                event_name=row.event_name,
                status=row.status,
                volume_7d=row.volume_7d,
                param_coverage_pct=coverage.get(row.event_name),
                last_seen_at=now if (row.volume_7d or 0) > 0 else None,
                detail={"reasons": row.reasons} if row.reasons else None,
                source=row.source,
                computed_at=now,
            )
        )
    for o in param_obs:
        session.add(
            TPParamObservation(
                plan_id=plan_id,
                event_name=o.event_name,
                param_key=o.param_key,
                present_pct=o.present_pct,
                sample_value=o.sample_value,
                data_type_observed=None,
                is_unplanned=o.is_unplanned,
                source=o.source,
                computed_at=now,
            )
        )


async def run_drift_computation() -> dict:
    """Daily job entry point: recompute drift for every project with it enabled.

    Isolates per-project failures so one bad connection doesn't abort the sweep.
    """
    if app_state.db_session_factory is None:
        raise RuntimeError("db_session_factory not initialised")

    processed = 0
    failed = 0
    # Iterate every project that has a plan — compute_drift get-or-creates the
    # config and skips any project whose config has been explicitly disabled.
    async with app_state.db_session_factory() as session:
        project_ids = list((await session.execute(select(TPPlan.project_id).distinct())).scalars())
    for pid in project_ids:
        try:
            async with app_state.db_session_factory() as session:
                await compute_drift(session, pid)
                await session.commit()
            processed += 1
        except Exception:
            logger.exception("drift computation failed for project %s", pid)
            failed += 1
    return {"processed": processed, "failed": failed}
