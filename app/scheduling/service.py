"""
APScheduler service wiring.

Provides the module-level scheduler that ``app.main``'s lifespan calls
into. Three concerns live here:

  1. Creating the ``AsyncIOScheduler`` with a Redis jobstore (so
     multiple API replicas don't double-fire the same schedule).
  2. Adding/updating/removing jobs to mirror the ``ReportSchedule``
     rows in the DB.
  3. On startup, loading every enabled schedule and upserting its job —
     this catches rows that were created while the worker was offline.

Why AsyncIOScheduler?
  The API process already runs an asyncio event loop; in-process
  scheduling keeps the deployment a single container. The Redis
  jobstore means a second replica is safe: only one replica picks
  up each fire (APScheduler acquires a lock per job).

Why separate ``run_scheduled_report`` from the APScheduler job?
  The job callable is ``_apscheduler_fire_schedule`` which is a thin
  shim: it calls ``run_scheduled_report`` and swallows ``LookupError``
  (schedule deleted between the trigger firing and the job running).
  Everything else propagates to APScheduler's logger so infrastructure
  errors are still visible.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC

from sqlalchemy import select

import app.app_state as app_state
from app.models.scheduled_report import ReportSchedule
from app.models.test_flows import TestFlow
from app.scheduling.runner import run_scheduled_report, run_test_flow

logger = logging.getLogger(__name__)


# ``_scheduler`` is private but read by ``sync_schedule_job`` and friends
# below. Tests can patch it to an in-memory fake.
_scheduler = None  # type: ignore[var-annotated]


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


async def start_scheduler(redis_url: str) -> None:
    """Initialise the scheduler and load existing enabled schedules.

    Called from ``app.main.lifespan`` at startup, after
    ``app_state.db_session_factory`` is ready (the runner uses it).

    Idempotent — calling twice is a no-op (second call is logged and
    ignored).
    """
    global _scheduler
    if _scheduler is not None:
        logger.info("start_scheduler called but scheduler is already running")
        return

    try:
        from apscheduler.executors.asyncio import AsyncIOExecutor
        from apscheduler.jobstores.redis import RedisJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError as exc:
        logger.warning(
            "APScheduler not installed — scheduled reports are disabled in this process: %s",
            exc,
        )
        return

    # Parse redis URL into host/port/db — APScheduler's RedisJobStore
    # doesn't accept a URL string.
    host, port, db_index, password = _parse_redis_url(redis_url)

    jobstore_kwargs: dict = {
        "host": host,
        "port": port,
        "db": db_index,
        # Namespace APScheduler's keys so they don't collide with app keys.
        "jobs_key": "amcp:scheduled_jobs",
        "run_times_key": "amcp:scheduled_run_times",
    }
    if password:
        jobstore_kwargs["password"] = password

    try:
        jobstore = RedisJobStore(**jobstore_kwargs)
    except Exception as exc:
        logger.error(
            "Failed to create APScheduler Redis jobstore — scheduled reports disabled: %s",
            exc,
        )
        return

    _scheduler = AsyncIOScheduler(
        jobstores={"default": jobstore},
        executors={"default": AsyncIOExecutor()},
        job_defaults={
            # Coalesce missed runs: if the worker was down for 3 hours
            # and a daily schedule fired twice in that window, we only
            # run it once on resume. Right behaviour for reports.
            "coalesce": True,
            # Grace period for late runs — 1 hour feels right given
            # typical network/restart hiccups.
            "misfire_grace_time": 3600,
            "max_instances": 1,
        },
    )
    _scheduler.start()
    logger.info("Scheduler started (Redis jobstore at %s:%s/%s)", host, port, db_index)

    # Best-effort initial sync — load enabled schedules from the DB and
    # upsert their jobs. This catches rows created while the worker was
    # offline.
    try:
        await _initial_sync()
    except Exception as exc:
        logger.exception("Initial schedule sync failed (non-fatal): %s", exc)

    # Fixed daily job: recompute tracking-plan drift for every project.
    try:
        _register_drift_job()
    except Exception as exc:
        logger.exception("Drift job registration failed (non-fatal): %s", exc)


async def stop_scheduler() -> None:
    """Shut down the scheduler cleanly. Safe to call if never started."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception as exc:
        logger.warning("Scheduler shutdown error (ignored): %s", exc)
    _scheduler = None
    logger.info("Scheduler stopped")


# --------------------------------------------------------------------------- #
# Tracking-plan drift — fixed daily sweep (not a per-row schedule)
# --------------------------------------------------------------------------- #

_DRIFT_JOB_ID = "tp_drift_daily"
# 03:30 UTC — after the GA4 BigQuery export has typically landed the prior day.
_DRIFT_CRON = "30 3 * * *"


async def _run_drift_job() -> None:
    """APScheduler callable: recompute drift for all projects. Errors are logged."""
    from app.services.tracking_plan.drift import run_drift_computation

    try:
        summary = await run_drift_computation()
        logger.info("drift sweep complete: %s", summary)
    except Exception:
        logger.exception("drift sweep failed")


def _register_drift_job() -> None:
    if _scheduler is None:
        return
    from apscheduler.triggers.cron import CronTrigger

    _scheduler.add_job(
        _run_drift_job,
        CronTrigger.from_crontab(_DRIFT_CRON, timezone="UTC"),
        id=_DRIFT_JOB_ID,
        replace_existing=True,
    )
    logger.info("registered daily drift job (%s UTC)", _DRIFT_CRON)


async def _initial_sync() -> None:
    """Load every enabled schedule from the DB and upsert its job."""
    sess_factory = app_state.db_session_factory
    if sess_factory is None:
        logger.warning("initial sync skipped — db_session_factory not set")
        return
    async with sess_factory() as db:
        result = await db.execute(select(ReportSchedule).where(ReportSchedule.enabled.is_(True)))
        schedules = list(result.scalars().all())
    loaded = 0
    for s in schedules:
        try:
            sync_schedule_job(s)
            loaded += 1
        except Exception as exc:
            logger.warning("initial sync: skipping schedule %s: %s", s.id, exc)
    logger.info("initial sync loaded %d schedule job(s)", loaded)

    # Also load enabled test flows that carry a cron expression.
    async with sess_factory() as db:
        flow_result = await db.execute(
            select(TestFlow).where(
                TestFlow.enabled.is_(True),
                TestFlow.schedule_cron.is_not(None),
            )
        )
        flows = list(flow_result.scalars().all())
    flow_loaded = 0
    for f in flows:
        try:
            sync_flow_job(f)
            flow_loaded += 1
        except Exception as exc:
            logger.warning("initial sync: skipping test flow %s: %s", f.id, exc)
    logger.info("initial sync loaded %d test-flow job(s)", flow_loaded)


# --------------------------------------------------------------------------- #
# Job CRUD — called by the schedule routes
# --------------------------------------------------------------------------- #


def sync_schedule_job(schedule: ReportSchedule) -> None:
    """Add or replace the APScheduler job for ``schedule``.

    If the schedule is disabled, the job is removed instead. Safe to
    call from the route handler — no async, no I/O apart from the
    jobstore's Redis pipeline.
    """
    if _scheduler is None:
        # Scheduler isn't running (either import failure or test mode).
        # Callers may still create DB rows; the next ``_initial_sync``
        # will pick them up.
        logger.debug("sync_schedule_job: scheduler not running, skipping %s", schedule.id)
        return

    job_id = _job_id(schedule.id)

    if not schedule.enabled:
        try:
            _scheduler.remove_job(job_id)
        except Exception:
            pass
        return

    try:
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("apscheduler not importable in sync_schedule_job")
        return

    try:
        trigger = CronTrigger.from_crontab(
            schedule.cron_expression,
            timezone=schedule.timezone or "UTC",
        )
    except Exception as exc:
        logger.error(
            "sync_schedule_job: bad cron '%s' for schedule %s: %s",
            schedule.cron_expression,
            schedule.id,
            exc,
        )
        return

    _scheduler.add_job(
        _apscheduler_fire_schedule,
        trigger=trigger,
        id=job_id,
        name=f"report:{schedule.name}",
        args=[str(schedule.id)],
        replace_existing=True,
    )
    logger.info(
        "sync_schedule_job: upserted %s (%s, tz=%s)",
        job_id,
        schedule.cron_expression,
        schedule.timezone,
    )


def remove_schedule_job(schedule_id: uuid.UUID | str) -> None:
    """Remove the APScheduler job for ``schedule_id`` if present."""
    if _scheduler is None:
        return
    job_id = _job_id(schedule_id)
    try:
        _scheduler.remove_job(job_id)
    except Exception:
        pass


def get_next_run_time(schedule_id: uuid.UUID | str):
    """Return the next scheduled fire time (or None if not scheduled)."""
    if _scheduler is None:
        return None
    job = _scheduler.get_job(_job_id(schedule_id))
    return job.next_run_time if job else None


# --------------------------------------------------------------------------- #
# Test-flow job CRUD — called by the test-flow routes
# --------------------------------------------------------------------------- #


def sync_flow_job(flow: TestFlow) -> None:
    """Add or replace the APScheduler job for a scheduled ``TestFlow``.

    Mirrors :func:`sync_schedule_job`. The job is removed when the flow is
    disabled or has no ``schedule_cron``. Safe to call from a route handler.
    """
    if _scheduler is None:
        logger.debug("sync_flow_job: scheduler not running, skipping %s", flow.id)
        return

    job_id = _flow_job_id(flow.id)

    if not flow.enabled or not flow.schedule_cron:
        try:
            _scheduler.remove_job(job_id)
        except Exception:
            pass
        return

    try:
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("apscheduler not importable in sync_flow_job")
        return

    try:
        trigger = CronTrigger.from_crontab(
            flow.schedule_cron,
            timezone=flow.timezone or "UTC",
        )
    except Exception as exc:
        logger.error(
            "sync_flow_job: bad cron '%s' for flow %s: %s",
            flow.schedule_cron,
            flow.id,
            exc,
        )
        return

    _scheduler.add_job(
        _apscheduler_fire_flow,
        trigger=trigger,
        id=job_id,
        name=f"testflow:{flow.name}",
        args=[str(flow.id)],
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    logger.info(
        "sync_flow_job: upserted %s (%s, tz=%s)",
        job_id,
        flow.schedule_cron,
        flow.timezone,
    )


def remove_flow_job(flow_id: uuid.UUID | str) -> None:
    """Remove the APScheduler job for ``flow_id`` if present."""
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(_flow_job_id(flow_id))
    except Exception:
        pass


def get_flow_next_run_time(flow_id: uuid.UUID | str):
    """Return the next fire time for a flow's scheduled job (or None)."""
    if _scheduler is None:
        return None
    job = _scheduler.get_job(_flow_job_id(flow_id))
    return job.next_run_time if job else None


def compute_flow_next_run(flow: TestFlow):
    """Compute the next fire time for ``flow`` directly from its cron trigger.

    Returned as a naive UTC ``datetime`` (matching the ``utcnow()`` convention
    used elsewhere for ``last_run_at`` / ``next_run_at``). Independent of
    whether the scheduler is running, so routes can persist it even in test
    mode. Returns ``None`` when the flow isn't schedulable or the cron is bad.
    """
    if not flow.enabled or not flow.schedule_cron:
        return None
    try:
        from datetime import datetime

        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(
            flow.schedule_cron,
            timezone=flow.timezone or "UTC",
        )
        now = datetime.now(UTC)
        nxt = trigger.get_next_fire_time(None, now)
        if nxt is None:
            return None
        return nxt.astimezone(UTC).replace(tzinfo=None)
    except Exception as exc:
        logger.warning("compute_flow_next_run failed for flow %s: %s", flow.id, exc)
        return None


async def _apscheduler_fire_flow(flow_id_str: str) -> None:
    """Thin wrapper APScheduler invokes when a test-flow trigger fires."""
    try:
        await run_test_flow(flow_id_str)
    except LookupError as exc:
        # Flow deleted/disabled between the trigger firing and the job running.
        logger.info("scheduled test-flow skipped: %s", exc)
    except Exception:
        logger.exception("scheduled test-flow crashed for %s", flow_id_str)
        raise


# --------------------------------------------------------------------------- #
# APScheduler job callable
# --------------------------------------------------------------------------- #


async def _apscheduler_fire_schedule(schedule_id_str: str) -> None:
    """Thin wrapper APScheduler invokes when a trigger fires.

    We keep this separate from ``run_scheduled_report`` so tests can
    exercise the runner without booting APScheduler at all, and so the
    runner doesn't know about APScheduler's error semantics.
    """
    try:
        await run_scheduled_report(schedule_id_str, triggered_by="schedule")
    except LookupError as exc:
        # Schedule was deleted or disabled between the trigger firing
        # and the job running — that's fine.
        logger.info("scheduled job skipped: %s", exc)
    except Exception:
        # Any other exception escapes to APScheduler's logger. We
        # don't swallow it here — infrastructure failures should be
        # visible.
        logger.exception("scheduled job crashed for %s", schedule_id_str)
        raise


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _job_id(schedule_id: uuid.UUID | str) -> str:
    return f"report:{schedule_id}"


def _flow_job_id(flow_id: uuid.UUID | str) -> str:
    return f"testflow:{flow_id}"


def _parse_redis_url(url: str) -> tuple[str, int, int, str | None]:
    """Parse a ``redis://[:password@]host:port/db`` URL into pieces.

    APScheduler's ``RedisJobStore`` takes host/port/db/password as
    separate kwargs rather than a URL, so we do the split here.
    Defaults match the ``redis[asyncio]`` client's own defaults.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url or "redis://localhost:6379/0")
    host = parsed.hostname or "localhost"
    port = int(parsed.port or 6379)
    db_index = 0
    if parsed.path and parsed.path.strip("/"):
        try:
            db_index = int(parsed.path.strip("/"))
        except ValueError:
            db_index = 0
    password = parsed.password or None
    return host, port, db_index, password
