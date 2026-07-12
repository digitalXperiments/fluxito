"""
Scheduled report worker.

Single public entry point:

    await run_scheduled_report(schedule_id, triggered_by="schedule")

Called from three places:

  1. APScheduler's job function (see ``service.py``) on cron fire.
  2. The "Run now" button on the Scheduled Reports UI (``triggered_by``
     is ``"manual"`` so runs can be filtered in audit history).
  3. Tests (directly, with a stub sender injected via ``app_state``).

Execution pipeline (one run):

  1. Load the schedule + its parent dashboard + project (one round trip).
     Bail with a friendly ``RUN_STATUS_FAILED`` row if the schedule is
     disabled, the dashboard has been deleted, or the project is
     suspended.
  2. Resolve filter macros → concrete start/end dates in the schedule's
     timezone. See ``app.scheduling.macros``.
  3. Hydrate the dashboard's cards (shared path via
     ``app.dashboards.hydration``) — this is where most wall-clock time
     goes. We do this *once* regardless of channel count.
  4. For each channel in ``schedule.channels``:
       * ``{"type": "email", "sender_id": "...", "to": ["a@b", ...]}``
         → render the PDF, build an ``EmailMessage`` with it attached,
         and call the sender. The sender is resolved by id so the
         worker never has to guess which one is "default".
       * ``{"type": "slack", "webhook_id": "..."}`` → render Block Kit
         blocks from the *same* hydrated cards and post via the webhook
         sender.
  5. Aggregate per-channel results into a ``ReportRun`` row:
       * All channels succeeded → ``RUN_STATUS_SUCCESS``.
       * All channels failed → ``RUN_STATUS_FAILED``.
       * Mixed → ``RUN_STATUS_PARTIAL``.
  6. Update ``consecutive_failures`` on the schedule:
       * On success / partial, reset to 0.
       * On failure, increment. If we hit
         ``FAILURE_AUTO_DISABLE_THRESHOLD``, set ``enabled=False``,
         remove the APScheduler job, and log loudly. (Optional: a
         future commit can email the project owner.)

Zero-persistence rule
  The PDF bytes and rendered blocks live in memory for the duration of
  the run and are dropped. ``ReportRun`` is metadata-only — no payload,
  no recipient list (just a count). See the model docstring.

Error-handling philosophy
  Channel-level failures (a webhook 500, an SMTP auth error) are caught
  and surface in the ``ReportRun`` row. Exceptions that escape the
  per-channel block indicate a bug or a DB/infrastructure problem and
  will propagate to APScheduler, which logs them and marks the job as
  errored (but doesn't disable it — that's our job based on
  ``consecutive_failures``).
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.app_state as app_state
from app.dashboards.hydration import card_to_payload, hydrate_dashboard_cards
from app.dashboards.pdf_renderer import render_dashboard_pdf
from app.models.dashboard import Dashboard, DashboardCard
from app.models.scheduled_report import (
    FAILURE_AUTO_DISABLE_THRESHOLD,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
    TRIGGERED_BY_MANUAL,
    TRIGGERED_BY_SCHEDULE,
    ProjectEmailSender,
    ProjectSlackWebhook,
    ReportRun,
    ReportSchedule,
)
from app.scheduling.macros import resolve_filter_macros

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


async def run_test_flow(flow_id: uuid.UUID | str) -> uuid.UUID:
    """APScheduler-facing entry point for a scheduled test flow.

    Thin wrapper delegating to the flow runner's own orchestration
    (``app.tag_testing.flow_runner.service.run_flow``), which owns row
    creation, execution, evaluation, audit mirroring and notifications.
    Kept here so the scheduler wiring mirrors ``run_scheduled_report``.

    Returns the ``TestFlowRun.id``. Raises ``LookupError`` if the flow no
    longer exists.
    """
    from app.tag_testing.flow_runner.service import run_flow

    return await run_flow(flow_id, trigger="schedule")


async def run_scheduled_report(
    schedule_id: uuid.UUID | str,
    triggered_by: str = TRIGGERED_BY_SCHEDULE,
) -> uuid.UUID:
    """Run one scheduled report end-to-end.

    Returns:
      The ``ReportRun.id`` of the audit row that was written.

    Raises:
      LookupError — if the schedule doesn't exist (treated as a
                    no-op rather than a failure since the APScheduler
                    job may still be firing for a row we just deleted).
      RuntimeError — if critical dependencies (``app_state.db_session_factory``)
                     aren't wired up. Should never happen in normal
                     runtime.
    """
    sess_factory = app_state.db_session_factory
    if sess_factory is None:
        raise RuntimeError(
            "run_scheduled_report called before app_state.db_session_factory "
            "was initialised — scheduler started before lifespan finished?"
        )

    if isinstance(schedule_id, str):
        schedule_id = uuid.UUID(schedule_id)

    started_at = datetime.utcnow()
    t_start = time.perf_counter()

    async with sess_factory() as db:
        schedule, dashboard = await _load_schedule_and_dashboard(db, schedule_id)
        if schedule is None or dashboard is None:
            # Nothing to run — log and return. Don't create a ReportRun
            # for a nonexistent schedule (there's no FK target).
            logger.info(
                "run_scheduled_report: schedule %s or its dashboard no longer exists",
                schedule_id,
            )
            raise LookupError(f"schedule {schedule_id} not found")

        if not schedule.enabled and triggered_by == TRIGGERED_BY_SCHEDULE:
            logger.info(
                "run_scheduled_report: schedule %s is disabled — skipping scheduled fire",
                schedule.id,
            )
            raise LookupError(f"schedule {schedule_id} is disabled")

        # Create the audit row up front — if we crash mid-run the row
        # stays at status=running and a future cleanup job (or a human)
        # can see it.
        run = ReportRun(
            schedule_id=schedule.id,
            started_at=started_at,
            status=RUN_STATUS_RUNNING,
            triggered_by=triggered_by
            if triggered_by
            in {
                TRIGGERED_BY_SCHEDULE,
                TRIGGERED_BY_MANUAL,
            }
            else TRIGGERED_BY_SCHEDULE,
        )
        db.add(run)
        await db.flush()
        run_id: uuid.UUID = run.id

        # Resolve macros → concrete filter params
        filter_params = resolve_filter_macros(
            schedule.filter_params or {},
            tz=schedule.timezone or "UTC",
        )

        # Load + filter cards (platform filter applies *before* execution)
        cards = await _load_and_filter_cards(db, dashboard, filter_params)
        try:
            await hydrate_dashboard_cards(
                dashboard,
                cards,
                date_filter={
                    "start_date": filter_params.get("start_date", ""),
                    "end_date": filter_params.get("end_date", ""),
                }
                if (filter_params.get("start_date") or filter_params.get("end_date"))
                else None,
            )
        except Exception as exc:
            # Hydration blew up entirely — the per-card ERROR path
            # should have caught anything recoverable. This is a real
            # failure we should record.
            logger.exception("hydration failed for schedule %s", schedule.id)
            await _finalize_run(
                db,
                schedule,
                run,
                t_start=t_start,
                status=RUN_STATUS_FAILED,
                recipient_count=0,
                channels_succeeded=0,
                channels_failed=len(schedule.channels or []),
                error=f"Dashboard hydration failed: {exc}",
            )
            await _handle_failure_counter(schedule)
            await db.commit()
            return run_id

        # Dispatch to each channel — gather per-channel results
        results = await _dispatch_channels(
            db=db,
            schedule=schedule,
            dashboard=dashboard,
            cards=cards,
            filter_params=filter_params,
        )

        recipient_count = sum(r.recipient_count for r in results)
        succeeded = sum(1 for r in results if r.ok)
        failed = sum(1 for r in results if not r.ok)

        if not results:
            status = RUN_STATUS_FAILED
            error = "Schedule has no channels configured."
        elif failed == 0:
            status = RUN_STATUS_SUCCESS
            error = None
        elif succeeded == 0:
            status = RUN_STATUS_FAILED
            error = "; ".join(r.error for r in results if r.error) or "all channels failed"
        else:
            status = RUN_STATUS_PARTIAL
            error = "; ".join(r.error for r in results if r.error) or None

        await _finalize_run(
            db,
            schedule,
            run,
            t_start=t_start,
            status=status,
            recipient_count=recipient_count,
            channels_succeeded=succeeded,
            channels_failed=failed,
            error=error,
        )

        # Update consecutive_failures + maybe auto-disable
        if status == RUN_STATUS_FAILED:
            await _handle_failure_counter(schedule)
        else:
            schedule.consecutive_failures = 0

        await db.commit()
        return run_id


# --------------------------------------------------------------------------- #
# Internals — loading
# --------------------------------------------------------------------------- #


async def _load_schedule_and_dashboard(
    db: AsyncSession,
    schedule_id: uuid.UUID,
) -> tuple[ReportSchedule | None, Dashboard | None]:
    """One-shot load for the schedule + its parent dashboard."""
    schedule = await db.get(ReportSchedule, schedule_id)
    if schedule is None:
        return None, None
    dashboard = await db.get(Dashboard, schedule.dashboard_id)
    return schedule, dashboard


async def _load_and_filter_cards(
    db: AsyncSession,
    dashboard: Dashboard,
    filter_params: dict[str, Any],
) -> list[DashboardCard]:
    """Load cards in position order, apply platform filter.

    The date filter is passed to hydration separately — it affects
    execution env vars, not the set of cards.
    """
    cards_result = await db.execute(
        select(DashboardCard)
        .where(DashboardCard.dashboard_id == dashboard.id)
        .order_by(DashboardCard.position)
    )
    cards = list(cards_result.scalars().all())

    platforms = filter_params.get("platforms") or []
    allowed = {p.strip().lower() for p in platforms if p and p.strip()}
    if allowed:
        cards = [c for c in cards if (c.platform or "").lower() in allowed]
    return cards


# --------------------------------------------------------------------------- #
# Internals — channel dispatch
# --------------------------------------------------------------------------- #


class _ChannelResult:
    """Per-channel outcome used to aggregate the ``ReportRun``."""

    __slots__ = ("error", "ok", "recipient_count")

    def __init__(self, ok: bool, recipient_count: int = 0, error: str | None = None) -> None:
        self.ok = ok
        self.recipient_count = recipient_count
        self.error = error


async def _dispatch_channels(
    db: AsyncSession,
    schedule: ReportSchedule,
    dashboard: Dashboard,
    cards: list[DashboardCard],
    filter_params: dict[str, Any],
) -> list[_ChannelResult]:
    """Fan out the hydrated dashboard to every configured channel.

    Channels run sequentially rather than in parallel because:
      * The hydration was done once; all channels share the result.
      * The most expensive per-channel step (PDF render) is already
        inside a thread, so concurrent channels wouldn't meaningfully
        speed things up.
      * Sequential is easier to reason about for failure accounting —
        order is stable in the audit row's ``error`` field.
    """
    channels = schedule.channels or []
    if not channels:
        return []

    # PDF is rendered lazily — only once, only if an email channel asks
    # for it. Slack path doesn't need it.
    rendered_pdf_bytes: bytes | None = None

    results: list[_ChannelResult] = []
    for i, ch in enumerate(channels):
        if not isinstance(ch, dict):
            results.append(_ChannelResult(False, error=f"channel[{i}] is not a dict"))
            continue
        ch_type = (ch.get("type") or "").lower()
        try:
            if ch_type == "email":
                # Lazy PDF render, shared across any email channels. Rendered
                # via headless Chromium against the live view (charts included);
                # no request cookies here, so render_dashboard_pdf mints an
                # owner cookie for the dashboard's user.
                if rendered_pdf_bytes is None:
                    _pdf_result = await render_dashboard_pdf(
                        db,
                        dashboard,
                        filter_params=filter_params,
                        include_insights=bool(schedule.include_insights),
                    )
                    rendered_pdf_bytes = _pdf_result.pdf_bytes
                results.append(
                    await _send_email_channel(
                        db,
                        schedule,
                        dashboard,
                        ch,
                        rendered_pdf_bytes,
                    )
                )
            elif ch_type == "slack":
                results.append(
                    await _send_slack_channel(
                        db,
                        schedule,
                        dashboard,
                        cards,
                        ch,
                        filter_params,
                    )
                )
            else:
                results.append(_ChannelResult(False, error=f"channel[{i}] unsupported type {ch_type!r}"))
        except Exception as exc:
            logger.exception(
                "channel[%d] (%s) dispatch failed for schedule %s",
                i,
                ch_type,
                schedule.id,
            )
            results.append(_ChannelResult(False, error=f"{ch_type}: {exc}"))

    return results


async def _send_email_channel(
    db: AsyncSession,
    schedule: ReportSchedule,
    dashboard: Dashboard,
    channel: dict[str, Any],
    pdf_bytes: bytes,
) -> _ChannelResult:
    """Send the PDF to one email channel spec."""
    from app.notifications.email.base import (
        EmailAttachment,
        EmailMessage,
        EmailSendError,
    )
    from app.notifications.email.factory import (
        NoDefaultSender,
        build_sender_for_project,
        build_sender_from_row,
    )

    recipients = [str(x).strip() for x in (channel.get("to") or []) if x]
    if not recipients:
        return _ChannelResult(False, error="email channel has no recipients")

    sender_id = channel.get("sender_id")
    try:
        if sender_id:
            row = await db.get(ProjectEmailSender, uuid.UUID(str(sender_id)))
            if row is None or row.project_id != schedule.project_id:
                return _ChannelResult(
                    False,
                    error=f"email sender {sender_id} not found in project",
                )
            sender = build_sender_from_row(row)
        else:
            # No explicit sender → fall back to the project default.
            sender = await build_sender_for_project(db, schedule.project_id)
    except NoDefaultSender as exc:
        return _ChannelResult(False, error=str(exc))
    except EmailSendError as exc:
        return _ChannelResult(False, error=str(exc))
    except Exception as exc:
        return _ChannelResult(False, error=f"email sender build failed: {exc}")

    subject = channel.get("subject") or f"{dashboard.title} — scheduled report"
    intro = channel.get("intro") or f"Automated report from Fluxito — {schedule.name}."

    filename = _pdf_filename(dashboard, schedule)
    attachment = EmailAttachment(
        filename=filename,
        content=pdf_bytes,
        content_type="application/pdf",
    )

    message = EmailMessage(
        to=tuple(recipients),
        subject=subject,
        text_body=intro,
        html_body=f"<p>{intro}</p><p>See the attached PDF for the full report.</p>",
        attachments=(attachment,),
    )

    try:
        result = await sender.send(message)
    except EmailSendError as exc:
        return _ChannelResult(
            False,
            recipient_count=len(recipients),
            error=f"email send failed: {exc}",
        )
    except Exception as exc:
        return _ChannelResult(
            False,
            recipient_count=len(recipients),
            error=f"email send crashed: {exc}",
        )

    if result.success:
        return _ChannelResult(True, recipient_count=len(recipients))
    return _ChannelResult(
        False,
        recipient_count=len(recipients),
        error=result.error or "unknown email send failure",
    )


async def _send_slack_channel(
    db: AsyncSession,
    schedule: ReportSchedule,
    dashboard: Dashboard,
    cards: list[DashboardCard],
    channel: dict[str, Any],
    filter_params: dict[str, Any],
) -> _ChannelResult:
    """Send the dashboard to one Slack webhook channel spec."""
    from app.notifications.slack.base import SlackMessage, SlackSendError
    from app.notifications.slack.blocks import render_dashboard_blocks
    from app.notifications.slack.factory import build_webhook_sender_from_row

    webhook_id = channel.get("webhook_id")
    if not webhook_id:
        return _ChannelResult(False, error="slack channel has no webhook_id")

    row = await db.get(ProjectSlackWebhook, uuid.UUID(str(webhook_id)))
    if row is None or row.project_id != schedule.project_id:
        return _ChannelResult(
            False,
            error=f"slack webhook {webhook_id} not found in project",
        )

    try:
        sender = build_webhook_sender_from_row(row)
    except SlackSendError as exc:
        return _ChannelResult(False, error=str(exc))

    card_payloads = [card_to_payload(c) for c in cards]
    filter_summary = _filter_summary(filter_params)

    blocks = render_dashboard_blocks(
        dashboard_title=dashboard.title,
        cards=card_payloads,
        description=dashboard.description or None,
        filter_summary=filter_summary,
        live_url=None,  # the scheduled message is point-in-time; no live link
        include_insights=False,  # scheduled sends exclude AI insights
        insights_md=None,
    )

    # Slack requires a fallback text for notifications / clients that
    # can't render Block Kit. Use the dashboard title + filter summary.
    fallback_text = f"{dashboard.title} — scheduled report"
    if filter_summary:
        fallback_text += f" ({filter_summary})"

    msg = SlackMessage(text=fallback_text, blocks=blocks)

    try:
        result = await sender.send(msg)
    except SlackSendError as exc:
        return _ChannelResult(False, recipient_count=1, error=f"slack send failed: {exc}")
    except Exception as exc:
        return _ChannelResult(False, recipient_count=1, error=f"slack send crashed: {exc}")

    if result.success:
        return _ChannelResult(True, recipient_count=1)
    return _ChannelResult(False, recipient_count=1, error=result.error or "slack send failure")


# --------------------------------------------------------------------------- #
# Internals — finalize + failure counter
# --------------------------------------------------------------------------- #


async def _finalize_run(
    db: AsyncSession,
    schedule: ReportSchedule,
    run: ReportRun,
    *,
    t_start: float,
    status: str,
    recipient_count: int,
    channels_succeeded: int,
    channels_failed: int,
    error: str | None,
) -> None:
    """Stamp the ReportRun + mirror summary fields onto the schedule."""
    now = datetime.utcnow()
    run.finished_at = now
    run.status = status
    run.recipient_count = recipient_count
    run.channels_succeeded = channels_succeeded
    run.channels_failed = channels_failed
    run.duration_ms = int((time.perf_counter() - t_start) * 1000)
    # Truncate errors so a giant traceback can't blow out the row size.
    if error:
        run.error = error[:4000]

    schedule.last_run_at = now
    schedule.last_status = status if status != RUN_STATUS_RUNNING else None
    schedule.last_error = (error or "")[:4000] if error else None


async def _handle_failure_counter(schedule: ReportSchedule) -> None:
    """Increment ``consecutive_failures``; auto-disable at threshold.

    Does not commit — the caller's ``db.commit()`` picks up the change.
    We also remove the APScheduler job here so a disabled schedule
    stops firing even if an operator forgets to restart the app.
    """
    schedule.consecutive_failures = (schedule.consecutive_failures or 0) + 1
    if schedule.consecutive_failures >= FAILURE_AUTO_DISABLE_THRESHOLD:
        schedule.enabled = False
        logger.warning(
            "Auto-disabling schedule %s after %s consecutive failures",
            schedule.id,
            schedule.consecutive_failures,
        )
        # Best-effort: remove the APScheduler job so no future fires.
        try:
            from app.scheduling.service import remove_schedule_job

            remove_schedule_job(schedule.id)
        except Exception as exc:
            logger.warning(
                "Could not remove APScheduler job for auto-disabled schedule %s: %s",
                schedule.id,
                exc,
            )


# --------------------------------------------------------------------------- #
# Internals — tiny formatting helpers
# --------------------------------------------------------------------------- #


def _pdf_filename(dashboard: Dashboard, schedule: ReportSchedule) -> str:
    """Compact, human-friendly filename for the email attachment."""
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (dashboard.title or "dashboard"))
    safe = safe.strip().replace(" ", "_") or "dashboard"
    stamp = datetime.utcnow().strftime("%Y%m%d")
    return f"{safe}_{stamp}.pdf"


def _filter_summary(filter_params: dict[str, Any]) -> str | None:
    """Return a short human-readable summary of the filters, or None."""
    parts: list[str] = []
    start = filter_params.get("start_date")
    end = filter_params.get("end_date")
    if start and end and start == end:
        parts.append(start)
    elif start and end:
        parts.append(f"{start} → {end}")
    elif start:
        parts.append(f"from {start}")
    elif end:
        parts.append(f"through {end}")
    platforms = filter_params.get("platforms") or []
    if platforms:
        parts.append(", ".join(str(p) for p in platforms))
    return " · ".join(parts) if parts else None
