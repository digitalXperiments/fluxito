"""
Scheduled Report CRUD Routes

All routes are dashboard-scoped and auth-gated by the dashboard's owning
project (not the dashboard's owning user — any project member with
``CAN_CONNECT_ROLES`` can manage schedules on dashboards that belong to
the project).

Routes:
  GET    /api/dashboards/{dashboard_id}/schedules
    — list schedules on a dashboard

  POST   /api/dashboards/{dashboard_id}/schedules
    — create a new schedule. Body is validated, macros are *not* expanded
      (expansion happens at run time in the worker), the cron expression
      is derived from the cadence fields at save time.

  PATCH  /api/dashboards/{dashboard_id}/schedules/{schedule_id}
    — update subset of fields. Supports enabling/disabling.

  DELETE /api/dashboards/{dashboard_id}/schedules/{schedule_id}
    — delete the schedule (and its ReportRun rows via cascade).

  POST   /api/dashboards/{dashboard_id}/schedules/{schedule_id}/run
    — fire the schedule immediately. Returns the ``ReportRun`` row id.

  GET    /api/dashboards/{dashboard_id}/schedules/{schedule_id}/runs
    — list the most recent ReportRuns for a schedule.

Auth model:
  * Authenticated via the signed ``uid`` cookie (same helper as
    ``dashboard_routes``).
  * The dashboard must belong to a project the user is a member of.
  * Mutations (POST/PATCH/DELETE/run) require
    ``ProjectMember.role in CAN_CONNECT_ROLES`` (owner or admin). Read
    routes allow any active member.

Side effects:
  * POST/PATCH/DELETE call ``sync_schedule_job`` / ``remove_schedule_job``
    so APScheduler stays in lockstep with the DB.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

import app.app_state as app_state
from app.auth.uid_cookie import get_uid_from_request
from app.models.dashboard import Dashboard
from app.models.project import (
    CAN_CONNECT_ROLES,
    ProjectMember,
)
from app.models.scheduled_report import (
    FORMAT_PDF,
    VALID_CADENCES,
    VALID_FORMATS,
    ReportRun,
    ReportSchedule,
)
from app.scheduling.cron import CronValidationError, cadence_to_cron
from app.scheduling.macros import KNOWN_MACROS

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Auth helpers
# --------------------------------------------------------------------------- #


async def _load_dashboard_and_membership(
    request: Request,
    dashboard_id: str,
    *,
    require_manage: bool,
) -> tuple[Dashboard | None, ProjectMember | None, JSONResponse | None]:
    """
    Resolve the dashboard + the caller's membership in the owning project.

    Returns ``(dash, membership, error_response)``. If ``error_response``
    is not None, the route should return it verbatim.

    If ``require_manage`` is True, members without one of
    ``CAN_CONNECT_ROLES`` get a 403 — reads are allowed for any active
    member.
    """
    uid = get_uid_from_request(request)
    if not uid:
        return None, None, JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        dash_uuid = uuid.UUID(dashboard_id)
        uid_uuid = uuid.UUID(uid)
    except ValueError:
        return None, None, JSONResponse({"error": "Bad id"}, status_code=400)

    async with app_state.db_session_factory() as db:
        dash = await db.get(Dashboard, dash_uuid)
        if dash is None:
            return None, None, JSONResponse({"error": "Dashboard not found"}, status_code=404)

        if not dash.project_id:
            # Legacy dashboards without a project can only be managed by
            # the owning user themselves.
            if dash.user_id != uid_uuid:
                return None, None, JSONResponse({"error": "Forbidden"}, status_code=403)
            return dash, None, None

        result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == dash.project_id,
                ProjectMember.user_id == uid_uuid,
                ProjectMember.is_active.is_(True),
            )
        )
        membership = result.scalar_one_or_none()

    if membership is None:
        return None, None, JSONResponse({"error": "Forbidden"}, status_code=403)

    if require_manage and membership.role not in CAN_CONNECT_ROLES:
        return (
            None,
            None,
            JSONResponse(
                {"error": "Only project owners and admins can manage scheduled reports"},
                status_code=403,
            ),
        )

    return dash, membership, None


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def _schedule_dict(s: ReportSchedule) -> dict[str, Any]:
    """Shape of a ReportSchedule in API responses and UI JSON."""
    return {
        "id": str(s.id),
        "dashboard_id": str(s.dashboard_id),
        "project_id": str(s.project_id),
        "name": s.name,
        "description": s.description or "",
        "enabled": bool(s.enabled),
        "cadence": s.cadence,
        "cron_expression": s.cron_expression,
        "timezone": s.timezone or "UTC",
        "filter_params": s.filter_params or {},
        "channels": s.channels or [],
        "format": s.format,
        "include_insights": bool(s.include_insights),
        "next_run_at": _iso(s.next_run_at),
        "last_run_at": _iso(s.last_run_at),
        "last_status": s.last_status or None,
        "last_error": s.last_error or None,
        "consecutive_failures": int(s.consecutive_failures or 0),
        "created_at": _iso(s.created_at),
        "updated_at": _iso(s.updated_at),
    }


def _run_dict(r: ReportRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "schedule_id": str(r.schedule_id),
        "started_at": _iso(r.started_at),
        "finished_at": _iso(r.finished_at),
        "status": r.status,
        "recipient_count": int(r.recipient_count or 0),
        "channels_succeeded": int(r.channels_succeeded or 0),
        "channels_failed": int(r.channels_failed or 0),
        "duration_ms": int(r.duration_ms) if r.duration_ms is not None else None,
        "error": r.error or None,
        "triggered_by": r.triggered_by,
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# --------------------------------------------------------------------------- #
# Payload validation
# --------------------------------------------------------------------------- #


class _BadPayload(Exception):
    """Raised when a POST/PATCH body can't be coerced into valid fields."""


def _parse_channels(raw: Any) -> list[dict[str, Any]]:
    """Validate the ``channels`` JSON array.

    Accepted shapes:
      * ``{"type": "email", "sender_id": "<uuid|null>", "to": ["..."],
           "subject": "...", "intro": "..."}``
      * ``{"type": "slack", "webhook_id": "<uuid>"}``

    ``sender_id`` is optional for email — if absent, the worker uses the
    project's default email sender at send time.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _BadPayload("channels must be an array")
    out: list[dict[str, Any]] = []
    for i, ch in enumerate(raw):
        if not isinstance(ch, dict):
            raise _BadPayload(f"channels[{i}] must be an object")
        t = (ch.get("type") or "").lower()
        if t == "email":
            to = ch.get("to") or []
            if not isinstance(to, list) or not to:
                raise _BadPayload(f"channels[{i}].to must be a non-empty array of addresses")
            clean_to = [str(x).strip() for x in to if str(x).strip()]
            if not clean_to:
                raise _BadPayload(f"channels[{i}].to has no usable addresses")
            spec: dict[str, Any] = {"type": "email", "to": clean_to}
            if ch.get("sender_id"):
                try:
                    uuid.UUID(str(ch["sender_id"]))
                    spec["sender_id"] = str(ch["sender_id"])
                except Exception:
                    raise _BadPayload(f"channels[{i}].sender_id is not a valid uuid")
            if ch.get("subject"):
                spec["subject"] = str(ch["subject"])[:300]
            if ch.get("intro"):
                spec["intro"] = str(ch["intro"])[:1000]
            out.append(spec)
        elif t == "slack":
            wid = ch.get("webhook_id")
            if not wid:
                raise _BadPayload(f"channels[{i}].webhook_id is required")
            try:
                uuid.UUID(str(wid))
            except Exception:
                raise _BadPayload(f"channels[{i}].webhook_id is not a valid uuid")
            out.append({"type": "slack", "webhook_id": str(wid)})
        else:
            raise _BadPayload(f"channels[{i}].type must be 'email' or 'slack'")
    return out


def _parse_filter_params(raw: Any) -> dict[str, Any]:
    """Accept filter_params and reject obviously-bad macros up front."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _BadPayload("filter_params must be an object")
    out: dict[str, Any] = {}
    for key in ("start_date", "end_date", "macro"):
        if key in raw and raw[key] is not None:
            out[key] = str(raw[key])
    if "platforms" in raw and raw["platforms"] is not None:
        if not isinstance(raw["platforms"], list):
            raise _BadPayload("filter_params.platforms must be an array")
        out["platforms"] = [str(p).strip() for p in raw["platforms"] if str(p).strip()]
    # If a macro is given (either top-level or as the start_date value),
    # reject unknown names early so the user gets feedback now rather
    # than at first run.
    macro_candidate = None
    if isinstance(out.get("macro"), str):
        macro_candidate = out["macro"].strip().strip("{}").lower()
    elif isinstance(out.get("start_date"), str) and out["start_date"].startswith("{{"):
        macro_candidate = out["start_date"].strip().strip("{}").lower()
    if macro_candidate and macro_candidate not in KNOWN_MACROS:
        raise _BadPayload(
            f"unknown filter macro '{macro_candidate}'. valid macros: {', '.join(sorted(KNOWN_MACROS))}"
        )
    return out


def _derive_cron(body: dict[str, Any]) -> tuple[str, str]:
    """Return ``(cadence, cron_expression)`` from a create/update payload."""
    cadence = (body.get("cadence") or "").strip().lower()
    if cadence not in VALID_CADENCES:
        raise _BadPayload(f"cadence must be one of: {', '.join(sorted(VALID_CADENCES))}")
    try:
        cron = cadence_to_cron(
            cadence,
            hour=int(body.get("hour", 9)),
            minute=int(body.get("minute", 0)),
            weekday=body.get("weekday"),
            day_of_month=body.get("day_of_month"),
            custom_cron=body.get("custom_cron"),
        )
    except CronValidationError as exc:
        raise _BadPayload(str(exc))
    except (TypeError, ValueError) as exc:
        raise _BadPayload(f"bad cadence fields: {exc}")
    return cadence, cron


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/api/dashboards/{dashboard_id}/schedule-options")
async def list_schedule_options(dashboard_id: str, request: Request):
    """
    Convenience endpoint for the schedule-creation UI.

    Returns everything the "New schedule" form needs in a single call so
    the modal doesn't have to juggle project slug / multiple endpoints:

      * ``senders``  — redacted ProjectEmailSender rows for the dashboard's
                       owning project
      * ``webhooks`` — redacted ProjectSlackWebhook rows for the project
      * ``macros``   — the allow-list of rolling-window macro names
      * ``timezones``— a curated short-list of common IANA zones so users
                       don't have to type one in blindly (they can still
                       hand-edit the field if they need something exotic)
      * ``quota``    — current schedule count + project plan limit + a
                       boolean flag the UI uses to grey out the "New"
                       button when the limit is reached

    Anyone with ``require_manage=True`` can call this (creating a
    schedule is gated the same way, so there's no point showing the form
    to members who can't submit it).
    """
    dash, _membership, err = await _load_dashboard_and_membership(
        request,
        dashboard_id,
        require_manage=True,
    )
    if err:
        return err
    if dash.project_id is None:
        return JSONResponse(
            {
                "senders": [],
                "webhooks": [],
                "macros": sorted(KNOWN_MACROS),
                "timezones": _CURATED_TIMEZONES,
                "quota": {
                    "current": 0,
                    "limit": 0,
                    "can_create": False,
                    "reason": "Dashboard is not attached to a project",
                },
            }
        )

    # Senders + webhooks — reuse the redaction helpers from project_routes
    from app.api.project_routes import _list_email_senders, _list_slack_webhooks

    senders = await _list_email_senders(dash.project_id)
    webhooks = await _list_slack_webhooks(dash.project_id)

    return JSONResponse(
        {
            "senders": senders,
            "webhooks": webhooks,
            "macros": sorted(KNOWN_MACROS),
            "timezones": _CURATED_TIMEZONES,
        }
    )


# A short, opinionated list of common IANA zones the UI presents as a
# dropdown. Keeping this hand-curated (instead of shipping the full 400+
# zone database) keeps the dropdown usable; power users can still type a
# zone manually via the "custom" option in the UI.
_CURATED_TIMEZONES: list[str] = [
    "UTC",
    "US/Pacific",
    "US/Mountain",
    "US/Central",
    "US/Eastern",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Madrid",
    "Europe/Amsterdam",
    "Europe/Stockholm",
    "Europe/Istanbul",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Hong_Kong",
    "Asia/Tokyo",
    "Australia/Sydney",
    "Pacific/Auckland",
    "America/Toronto",
    "America/Sao_Paulo",
    "America/Mexico_City",
]


@router.get("/api/dashboards/{dashboard_id}/schedules")
async def list_schedules(dashboard_id: str, request: Request):
    dash, _membership, err = await _load_dashboard_and_membership(
        request,
        dashboard_id,
        require_manage=False,
    )
    if err:
        return err

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(ReportSchedule)
            .where(ReportSchedule.dashboard_id == dash.id)
            .order_by(ReportSchedule.created_at.desc())
        )
        rows = list(result.scalars().all())

    # Attach next_run_at from the live APScheduler job if available — the
    # DB column is only updated at fire time so it lags behind.
    try:
        from app.scheduling.service import get_next_run_time

        for s in rows:
            nrt = get_next_run_time(s.id)
            if nrt is not None:
                s.next_run_at = nrt.replace(tzinfo=None) if nrt.tzinfo else nrt
    except Exception:
        pass

    return JSONResponse({"schedules": [_schedule_dict(s) for s in rows]})


@router.post("/api/dashboards/{dashboard_id}/schedules")
async def create_schedule(dashboard_id: str, request: Request):
    dash, _membership, err = await _load_dashboard_and_membership(
        request,
        dashboard_id,
        require_manage=True,
    )
    if err:
        return err
    if dash.project_id is None:
        # Schedules are project-billed; legacy dashboards without a
        # project can't get schedules attached.
        return JSONResponse(
            {"error": "This dashboard is not attached to a project — move it first."},
            status_code=400,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Body must be a JSON object"}, status_code=400)

    uid = get_uid_from_request(request) or ""

    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "Schedule name is required"}, status_code=400)

    try:
        cadence, cron = _derive_cron(body)
        channels = _parse_channels(body.get("channels"))
        filter_params = _parse_filter_params(body.get("filter_params"))
    except _BadPayload as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if not channels:
        return JSONResponse(
            {"error": "At least one channel (email or slack) is required"},
            status_code=400,
        )

    fmt = (body.get("format") or FORMAT_PDF).lower()
    if fmt not in VALID_FORMATS:
        return JSONResponse(
            {"error": f"format must be one of: {', '.join(sorted(VALID_FORMATS))}"},
            status_code=400,
        )

    tz = (body.get("timezone") or "UTC").strip() or "UTC"
    # Lightweight timezone sanity check — IANA names only
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
    except Exception:
        return JSONResponse(
            {"error": f"Unknown IANA timezone '{tz}'"},
            status_code=400,
        )

    async with app_state.db_session_factory() as db:
        row = ReportSchedule(
            project_id=dash.project_id,
            dashboard_id=dash.id,
            created_by_user_id=uuid.UUID(uid) if uid else None,
            name=name[:255],
            description=(body.get("description") or "")[:2000] or None,
            enabled=bool(body.get("enabled", True)),
            cadence=cadence,
            cron_expression=cron,
            timezone=tz,
            filter_params=filter_params,
            channels=channels,
            format=fmt,
            include_insights=bool(body.get("include_insights", False)),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

    try:
        from app.scheduling.service import sync_schedule_job

        sync_schedule_job(row)
    except Exception as exc:
        logger.warning("sync_schedule_job failed after create %s: %s", row.id, exc)

    return JSONResponse({"schedule": _schedule_dict(row)}, status_code=201)


@router.patch("/api/dashboards/{dashboard_id}/schedules/{schedule_id}")
async def update_schedule(dashboard_id: str, schedule_id: str, request: Request):
    dash, _membership, err = await _load_dashboard_and_membership(
        request,
        dashboard_id,
        require_manage=True,
    )
    if err:
        return err

    try:
        sid = uuid.UUID(schedule_id)
    except ValueError:
        return JSONResponse({"error": "Bad id"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Body must be a JSON object"}, status_code=400)

    async with app_state.db_session_factory() as db:
        row = await db.get(ReportSchedule, sid)
        if row is None or row.dashboard_id != dash.id:
            return JSONResponse({"error": "Not found"}, status_code=404)

        # Apply allowed updates
        try:
            if "name" in body:
                name = (body.get("name") or "").strip()
                if not name:
                    raise _BadPayload("name cannot be empty")
                row.name = name[:255]
            if "description" in body:
                row.description = (body.get("description") or "")[:2000] or None
            if "enabled" in body:
                row.enabled = bool(body["enabled"])
                # Resetting enabled should also reset the failure counter
                # so the user has a fresh 5-strike budget.
                if row.enabled:
                    row.consecutive_failures = 0
            if (
                "cadence" in body
                or "hour" in body
                or "minute" in body
                or "weekday" in body
                or "day_of_month" in body
                or "custom_cron" in body
            ):
                # Re-derive cron from the merged set of fields
                merged = {
                    "cadence": body.get("cadence", row.cadence),
                    "hour": body.get("hour", 9),
                    "minute": body.get("minute", 0),
                    "weekday": body.get("weekday"),
                    "day_of_month": body.get("day_of_month"),
                    "custom_cron": body.get("custom_cron"),
                }
                row.cadence, row.cron_expression = _derive_cron(merged)
            if "timezone" in body:
                tz = (body.get("timezone") or "UTC").strip() or "UTC"
                try:
                    from zoneinfo import ZoneInfo

                    ZoneInfo(tz)
                except Exception:
                    raise _BadPayload(f"Unknown IANA timezone '{tz}'")
                row.timezone = tz
            if "filter_params" in body:
                row.filter_params = _parse_filter_params(body.get("filter_params"))
            if "channels" in body:
                chans = _parse_channels(body.get("channels"))
                if not chans:
                    raise _BadPayload("At least one channel is required")
                row.channels = chans
            if "format" in body:
                fmt = (body.get("format") or FORMAT_PDF).lower()
                if fmt not in VALID_FORMATS:
                    raise _BadPayload(f"format must be one of: {', '.join(sorted(VALID_FORMATS))}")
                row.format = fmt
            if "include_insights" in body:
                row.include_insights = bool(body["include_insights"])
        except _BadPayload as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        await db.commit()
        await db.refresh(row)

    try:
        from app.scheduling.service import sync_schedule_job

        sync_schedule_job(row)
    except Exception as exc:
        logger.warning("sync_schedule_job failed after update %s: %s", row.id, exc)

    return JSONResponse({"schedule": _schedule_dict(row)})


@router.delete("/api/dashboards/{dashboard_id}/schedules/{schedule_id}")
async def delete_schedule(dashboard_id: str, schedule_id: str, request: Request):
    dash, _membership, err = await _load_dashboard_and_membership(
        request,
        dashboard_id,
        require_manage=True,
    )
    if err:
        return err

    try:
        sid = uuid.UUID(schedule_id)
    except ValueError:
        return JSONResponse({"error": "Bad id"}, status_code=400)

    async with app_state.db_session_factory() as db:
        row = await db.get(ReportSchedule, sid)
        if row is None or row.dashboard_id != dash.id:
            return JSONResponse({"error": "Not found"}, status_code=404)
        await db.execute(sa_delete(ReportSchedule).where(ReportSchedule.id == row.id))
        await db.commit()

    try:
        from app.scheduling.service import remove_schedule_job

        remove_schedule_job(sid)
    except Exception as exc:
        logger.warning("remove_schedule_job failed for %s: %s", sid, exc)

    return JSONResponse({"success": True})


@router.post("/api/dashboards/{dashboard_id}/schedules/{schedule_id}/run")
async def run_schedule_now(dashboard_id: str, schedule_id: str, request: Request):
    dash, _membership, err = await _load_dashboard_and_membership(
        request,
        dashboard_id,
        require_manage=True,
    )
    if err:
        return err

    try:
        sid = uuid.UUID(schedule_id)
    except ValueError:
        return JSONResponse({"error": "Bad id"}, status_code=400)

    # Confirm the schedule belongs to the dashboard before kicking the
    # runner — otherwise a user could trigger any schedule they know the
    # id of if they had *any* manage role.
    async with app_state.db_session_factory() as db:
        row = await db.get(ReportSchedule, sid)
        if row is None or row.dashboard_id != dash.id:
            return JSONResponse({"error": "Not found"}, status_code=404)

    from app.scheduling.runner import run_scheduled_report

    try:
        run_id = await run_scheduled_report(sid, triggered_by="manual")
    except LookupError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.exception("run-now failed for schedule %s", sid)
        return JSONResponse(
            {"error": f"Run failed: {exc}"},
            status_code=500,
        )

    return JSONResponse({"success": True, "run_id": str(run_id)})


@router.get("/api/dashboards/{dashboard_id}/schedules/{schedule_id}/runs")
async def list_schedule_runs(dashboard_id: str, schedule_id: str, request: Request):
    dash, _membership, err = await _load_dashboard_and_membership(
        request,
        dashboard_id,
        require_manage=False,
    )
    if err:
        return err

    try:
        sid = uuid.UUID(schedule_id)
    except ValueError:
        return JSONResponse({"error": "Bad id"}, status_code=400)

    async with app_state.db_session_factory() as db:
        sched = await db.get(ReportSchedule, sid)
        if sched is None or sched.dashboard_id != dash.id:
            return JSONResponse({"error": "Not found"}, status_code=404)
        result = await db.execute(
            select(ReportRun)
            .where(ReportRun.schedule_id == sid)
            .order_by(ReportRun.started_at.desc())
            .limit(50)
        )
        runs = list(result.scalars().all())

    return JSONResponse({"runs": [_run_dict(r) for r in runs]})


# --------------------------------------------------------------------------- #
# Project-wide schedules — page + list JSON
# --------------------------------------------------------------------------- #


@router.get("/api/reports/schedules")
async def list_project_schedules(request: Request):
    """List every ReportSchedule across the caller's active project.

    The per-dashboard CRUD endpoints above stay authoritative for
    mutations; this is the single project-scoped read the /reports/schedules
    page needs so it doesn't have to fan out one request per dashboard.
    Each row carries its ``dashboard_id`` so the page can call the existing
    dashboard-scoped PATCH/DELETE/run endpoints directly.
    """
    from app.api.google_oauth_routes import _resolve_user_ctx
    from app.api.project_routes import ensure_active_project

    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    active_pid = await ensure_active_project(request, user_ctx.user_id)
    if not active_pid:
        return JSONResponse({"schedules": []})
    pid = uuid.UUID(active_pid)

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(ReportSchedule, Dashboard.title, Dashboard.share_slug)
            .join(Dashboard, ReportSchedule.dashboard_id == Dashboard.id)
            .where(ReportSchedule.project_id == pid)
            .order_by(ReportSchedule.created_at.desc())
        )
        rows = result.all()

    # Overlay live next-run times from APScheduler where available.
    try:
        from app.scheduling.service import get_next_run_time

        for sched, _title, _slug in rows:
            nrt = get_next_run_time(sched.id)
            if nrt is not None:
                sched.next_run_at = nrt.replace(tzinfo=None) if nrt.tzinfo else nrt
    except Exception:
        pass

    out = []
    for sched, title, slug in rows:
        d = _schedule_dict(sched)
        d["dashboard_title"] = title or "Untitled dashboard"
        d["dashboard_slug"] = slug or ""
        out.append(d)

    return JSONResponse({"schedules": out})


@router.get("/reports/schedules", response_class=HTMLResponse)
async def reports_schedules_page(request: Request):
    """The standalone Scheduled reports page merged into the Dashboards hub
    (site revamp) — it is now the #schedules tab there. Redirect old links."""
    return RedirectResponse("/live-dashboards#schedules", status_code=302)
