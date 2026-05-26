"""
Automation Library Routes
==========================

Web UI + JSON endpoints for the Cowork-native automation library.

  GET  /automations                     — Library page (curated + project)
  GET  /automations/{slug}              — Detail page (rendered prompt + 3 install paths)
  GET  /automations/new                 — Author a custom automation (Pro/Team)

  GET  /api/automations                 — JSON list (used by library page filters)
  GET  /api/automations/{slug}          — JSON single automation
  POST /api/automations/{slug}/install  — Record an install + return Cowork args
  POST /api/automations                 — Create a custom user automation
  POST /api/automations/{slug}/preview  — Render the prompt with a given variable set
                                          (used by the install modal live preview)

The Web UI offers two install paths for each automation:

  1. **MCP**        — instructions to call `automation_write(action='install', ...)`
                      from Claude in Cowork. Cowork then chains `create_scheduled_task`.
  2. **Copy/paste** — the rendered prompt + cron, ready to paste into Cowork
                      manually as a fallback.

Both end up at the same place: a recurring Cowork task that runs the
automation's prompt against this user's project. We persist the install row
either way so the Web UI can show a list of installed automations per project.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update

import app.app_state as app_state
from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx
from app.api.project_routes import (
    ensure_active_project,
    set_active_project_cookie,
)
from app.config import settings
from app.models.automation import (
    AUTOMATION_TYPE_SYSTEM,
    AUTOMATION_TYPE_USER,
    INSTALL_STATUS_ACTIVE,
    INSTALL_STATUS_PAUSED,
    INSTALL_STATUS_REMOVED,
    THEME_LABELS,
    VALID_THEMES,
    Automation,
    AutomationInstallation,
)
from app.templating import render

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Auth helpers (mirror knowledge_routes)
# --------------------------------------------------------------------------- #


async def _require_user_and_project(request: Request):
    """Resolve auth + active project. Returns (user_ctx, user_uuid, project_id)."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        user_uuid = uuid.UUID(user_ctx.user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized")

    project_id_str = await ensure_active_project(request, user_ctx.user_id)
    if not project_id_str:
        raise HTTPException(
            status_code=400,
            detail="No active project. Create or select a project first.",
        )
    project_id = uuid.UUID(project_id_str)
    return user_ctx, user_uuid, project_id


async def _project_for(project_id: uuid.UUID):
    """Fetch the Project row (for name + plan) given its id."""
    from app.models.project import Project

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(Project).where(Project.id == project_id))
        return result.scalar_one_or_none()


async def _project_connected_platforms(project_id: uuid.UUID) -> list[str]:
    """Return the platform-key list for everything connected to a project.

    Issues 6 connection-type queries concurrently via ``asyncio.gather`` so a
    single call doesn't burn 6 round-trips of DB latency sequentially. Each
    query uses its own session — asyncpg connections aren't safe for
    concurrent use, and ``AsyncSession`` serialises commands on one
    connection.
    """
    from app.models.bq_connection import BQConnection
    from app.models.connection import OAuthConnection
    from app.models.credential_connection import (
        AdobeConnection,
        AmplitudeConnection,
        RedshiftConnection,
        SnowflakeConnection,
    )

    async def _fetch_all(model, filter_col, include_first_only: bool = False):
        """Run a single SELECT in its own session. Returns .scalars().all()
        (or a one-element list if ``include_first_only``)."""
        async with app_state.db_session_factory() as db:
            result = await db.execute(select(model).where(filter_col == project_id, model.is_active == True))
            rows = result.scalars().all()
            if include_first_only:
                return rows[:1]
            return rows

    oauth_rows, bq_rows, amp_rows, rs_rows, sf_rows, adobe_rows = await asyncio.gather(
        _fetch_all(OAuthConnection, OAuthConnection.project_id),
        _fetch_all(BQConnection, BQConnection.fluxito_project_id, include_first_only=True),
        _fetch_all(AmplitudeConnection, AmplitudeConnection.project_id, include_first_only=True),
        _fetch_all(RedshiftConnection, RedshiftConnection.project_id, include_first_only=True),
        _fetch_all(SnowflakeConnection, SnowflakeConnection.project_id, include_first_only=True),
        _fetch_all(AdobeConnection, AdobeConnection.project_id),
    )

    connected: list[str] = []

    for conn in oauth_rows:
        provider = getattr(conn, "provider", "google")
        # ``conn.scopes`` is an ARRAY(String) of full scope URLs like
        # ``https://www.googleapis.com/auth/analytics.readonly`` — join
        # into a single string so substring checks below work.
        scopes_str = " ".join(conn.scopes or [])
        if provider == "google":
            if "analytics" in scopes_str and "ga4" not in connected:
                connected.append("ga4")
            if "tagmanager" in scopes_str and "gtm" not in connected:
                connected.append("gtm")
            if "adwords" in scopes_str and "google_ads" not in connected:
                connected.append("google_ads")
            if "webmasters" in scopes_str and "search_console" not in connected:
                connected.append("search_console")
        elif provider in ("meta", "tiktok", "snap") and provider not in connected:
            connected.append(provider)

    if bq_rows:
        connected.append("bigquery")
    if amp_rows:
        connected.append("amplitude")
    if rs_rows:
        connected.append("redshift")
    if sf_rows:
        connected.append("snowflake")

    for ac in adobe_rows:
        if getattr(ac, "has_analytics", False) and "adobe_analytics" not in connected:
            connected.append("adobe_analytics")
        if getattr(ac, "has_launch", False) and "adobe_launch" not in connected:
            connected.append("adobe_launch")

    return connected


async def _project_channel_options(project_id: uuid.UUID) -> dict:
    """Return the configured Slack webhooks + email senders for the project."""
    from app.models.scheduled_report import ProjectEmailSender, ProjectSlackWebhook

    async with app_state.db_session_factory() as db:
        slack_rows = await db.execute(
            select(ProjectSlackWebhook).where(ProjectSlackWebhook.project_id == project_id)
        )
        email_rows = await db.execute(
            select(ProjectEmailSender).where(ProjectEmailSender.project_id == project_id)
        )
        return {
            "slack": [{"id": str(s.id), "label": s.label} for s in slack_rows.scalars().all()],
            "email": [
                {"id": str(e.id), "label": e.label, "from": e.from_address}
                for e in email_rows.scalars().all()
            ],
        }


# --------------------------------------------------------------------------- #
# Render helpers
# --------------------------------------------------------------------------- #


_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _render_prompt(template_text: str, values: dict) -> str:
    out = template_text or ""
    for key, val in (values or {}).items():
        if val is None:
            continue
        pattern = re.compile(r"\{\{\s*" + re.escape(key) + r"\s*\}\}")
        out = pattern.sub(str(val), out)
    return out


def _resolve_variables(automation: Automation, supplied: dict | None) -> dict:
    resolved: dict = {}
    for var_def in automation.variables or []:
        key = var_def.get("key")
        if not key:
            continue
        if "default" in var_def and var_def["default"] is not None:
            resolved[key] = var_def["default"]
    if supplied:
        for k, v in supplied.items():
            if v is not None and v != "":
                resolved[k] = v
    return resolved


def _format_automation(p: Automation) -> dict:
    return {
        "id": str(p.id),
        "slug": p.slug,
        "title": p.title,
        "description": p.description or "",
        "theme": p.theme,
        "theme_label": p.theme_label(),
        "icon": p.icon or "play",
        "type": p.playbook_type,
        "required_platforms": p.required_platforms or [],
        "variables": p.variables or [],
        "default_cron": p.default_cron,
        "default_schedule_label": p.default_schedule_label,
        "default_task_name": p.default_task_name,
        "cooldown_hours": p.cooldown_hours or 0,
        "channel_hints": p.channel_hints or [],
        "min_tier": p.min_tier or "free",
        "is_featured": bool(p.is_featured),
        "use_count": p.use_count or 0,
        "prompt_template": p.prompt_template or "",
    }


def _build_install_artifacts(
    automation: Automation,
    project,
    rendered_prompt: str,
    cron: str,
    task_name: str,
    channel_label: str,
) -> dict:
    """Build the two install paths for an automation (MCP + copy/paste)."""
    base = settings.APP_BASE_URL

    mcp_snippet = (
        f"automation_write(\n"
        f'  action="install",\n'
        f'  slug="{automation.slug}",\n'
        f'  channel_label="{channel_label}",\n'
        f")"
    )

    copy_paste = (
        f"# Cowork scheduled task — {automation.title}\n"
        f"# Project: {getattr(project, 'name', '')}\n"
        f"# Cron: {cron}\n"
        f"# Task name: {task_name}\n\n"
        f"{rendered_prompt}\n"
    )

    return {
        "mcp": {
            "snippet": mcp_snippet,
            "instructions": (
                "From Claude in Cowork, ask it to install this automation. "
                "Claude will call `automation_write(action='install', ...)` "
                "against Fluxito — our tool returns `scheduled_task_args` "
                "(taskId, prompt, description, cronExpression) which Claude "
                "then passes straight to Cowork's `create_scheduled_task` "
                "tool in the same turn."
            ),
        },
        "copy_paste": {
            "task_name": task_name,
            "cron": cron,
            "prompt": copy_paste,
            "instructions": (
                "Copy the prompt, open Cowork → Scheduled Tasks → New, "
                "paste this prompt and use the cron above."
            ),
        },
        "manage_url": f"{base}/automations",
    }


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #


@router.get("/automations", response_class=HTMLResponse)
async def automations_page(
    request: Request,
    theme: str | None = None,
    mine: str | None = None,
):
    """Library browse page."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/automations", status_code=302)
    user_view = await _load_user_view(user_ctx)

    project_id_str = await ensure_active_project(request, user_ctx.user_id)
    if not project_id_str:
        return RedirectResponse("/projects", status_code=302)
    project_id = uuid.UUID(project_id_str)

    project = await _project_for(project_id)
    connected_platforms = await _project_connected_platforms(project_id)
    show_mine = mine == "1"

    async with app_state.db_session_factory() as db:
        query = select(Automation).where(Automation.is_active == True)
        if show_mine:
            query = query.where(
                Automation.project_id == project_id,
                Automation.playbook_type == AUTOMATION_TYPE_USER,
            )
        else:
            query = query.where(
                (Automation.playbook_type == AUTOMATION_TYPE_SYSTEM) | (Automation.project_id == project_id)
            )
        if theme and theme in VALID_THEMES:
            query = query.where(Automation.theme == theme)
        query = query.order_by(
            Automation.is_featured.desc(),
            Automation.use_count.desc(),
            Automation.created_at.desc(),
        )
        result = await db.execute(query)
        automations = result.scalars().all()

        # Counts for the toggle tabs
        from sqlalchemy import func as sql_func

        sys_count = await db.execute(
            select(sql_func.count())
            .select_from(Automation)
            .where(
                Automation.is_active == True,
                Automation.playbook_type == AUTOMATION_TYPE_SYSTEM,
            )
        )
        user_count = await db.execute(
            select(sql_func.count())
            .select_from(Automation)
            .where(
                Automation.is_active == True,
                Automation.project_id == project_id,
                Automation.playbook_type == AUTOMATION_TYPE_USER,
            )
        )

    items = []
    for p in automations:
        item = _format_automation(p)
        item["all_connected"] = all(plat in connected_platforms for plat in item["required_platforms"])
        item["missing_platforms"] = [
            plat for plat in item["required_platforms"] if plat not in connected_platforms
        ]
        items.append(item)

    response = render(
        request,
        "automations.html",
        {
            "user": user_view,
            "active": "automations",
            "automations": items,
            "themes": [{"key": t, "label": THEME_LABELS[t]} for t in VALID_THEMES],
            "current_theme": theme,
            "show_mine": show_mine,
            "system_count": sys_count.scalar() or 0,
            "user_count": user_count.scalar() or 0,
            "project": {
                "id": str(project_id),
                "name": getattr(project, "name", ""),
                "plan": getattr(project, "plan", "free"),
            },
            "connected_platforms": connected_platforms,
        },
    )
    set_active_project_cookie(response, project_id_str)
    return response


@router.get("/automations/new", response_class=HTMLResponse)
async def automation_author_page(request: Request):
    """Custom automation authoring form (Pro/Team)."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/automations/new", status_code=302)
    user_view = await _load_user_view(user_ctx)

    project_id_str = await ensure_active_project(request, user_ctx.user_id)
    if not project_id_str:
        return RedirectResponse("/projects", status_code=302)
    project_id = uuid.UUID(project_id_str)
    project = await _project_for(project_id)

    response = render(
        request,
        "automation_new.html",
        {
            "user": user_view,
            "active": "automations",
            "themes": [{"key": t, "label": THEME_LABELS[t]} for t in VALID_THEMES],
            "project": {
                "id": str(project_id),
                "name": getattr(project, "name", ""),
                "plan": getattr(project, "plan", "free"),
            },
            "platform_choices": [
                "ga4",
                "gtm",
                "google_ads",
                "search_console",
                "meta",
                "tiktok",
                "snap",
                "bigquery",
                "amplitude",
                "adobe_analytics",
                "adobe_launch",
                "redshift",
                "snowflake",
            ],
        },
    )
    set_active_project_cookie(response, project_id_str)
    return response


@router.get("/automations/{slug}", response_class=HTMLResponse)
async def automation_detail_page(request: Request, slug: str):
    """Detail page: shows prompt, install paths, and per-install configurator."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse(f"/signin?next=/automations/{slug}", status_code=302)
    user_view = await _load_user_view(user_ctx)

    project_id_str = await ensure_active_project(request, user_ctx.user_id)
    if not project_id_str:
        return RedirectResponse("/projects", status_code=302)
    project_id = uuid.UUID(project_id_str)
    project = await _project_for(project_id)

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(Automation).where(
                Automation.slug == slug,
                Automation.is_active == True,
            )
        )
        automation = result.scalar_one_or_none()

    if not automation:
        return render(
            request,
            "error.html",
            {
                "user": user_view,
                "error_title": "Automation Not Found",
                "error_message": "This automation doesn't exist or has been removed.",
            },
            status_code=404,
        )

    connected_platforms = await _project_connected_platforms(project_id)
    channels = await _project_channel_options(project_id)

    # Render a default preview using project_name + first available channel
    default_channel = ""
    if channels["slack"]:
        default_channel = f"Slack {channels['slack'][0]['label']}"
    elif channels["email"]:
        default_channel = channels["email"][0]["from"]

    resolved = _resolve_variables(automation, None)
    resolved.setdefault("project_name", getattr(project, "name", "") or "")
    if default_channel:
        resolved["channel_label"] = default_channel

    rendered_prompt = _render_prompt(automation.prompt_template, resolved)

    cron = automation.default_cron or "0 9 * * *"
    task_name = automation.default_task_name or automation.slug
    install_artifacts = _build_install_artifacts(
        automation,
        project,
        rendered_prompt,
        cron,
        task_name,
        default_channel or "<your channel>",
    )

    item = _format_automation(automation)
    item["all_connected"] = all(p in connected_platforms for p in item["required_platforms"])
    item["missing_platforms"] = [p for p in item["required_platforms"] if p not in connected_platforms]

    # Existing install rows for this automation in this project
    async with app_state.db_session_factory() as db:
        installs = await db.execute(
            select(AutomationInstallation)
            .where(
                AutomationInstallation.playbook_id == automation.id,
                AutomationInstallation.project_id == project_id,
                AutomationInstallation.status != INSTALL_STATUS_REMOVED,
            )
            .order_by(AutomationInstallation.installed_at.desc())
        )
        install_rows = [
            {
                "id": str(i.id),
                "task_name": i.task_name,
                "cron_expression": i.cron_expression,
                "channel_summary": i.channel_summary,
                "status": i.status,
                "installed_at": i.installed_at.isoformat() if i.installed_at else None,
            }
            for i in installs.scalars().all()
        ]

    response = render(
        request,
        "automation_detail.html",
        {
            "user": user_view,
            "active": "automations",
            "automation": item,
            "rendered_prompt": rendered_prompt,
            "resolved_variables": resolved,
            "install_artifacts": install_artifacts,
            "default_channel": default_channel,
            "channels": channels,
            "installs": install_rows,
            "connected_platforms": connected_platforms,
            "project": {
                "id": str(project_id),
                "name": getattr(project, "name", ""),
                "plan": getattr(project, "plan", "free"),
            },
        },
    )
    set_active_project_cookie(response, project_id_str)
    return response


# --------------------------------------------------------------------------- #
# JSON API
# --------------------------------------------------------------------------- #


class InstallPayload(BaseModel):
    channel_label: str = Field(..., min_length=1, max_length=255)
    cron_expression: str | None = Field(None, max_length=128)
    task_name: str | None = Field(None, max_length=160)
    variables: dict | None = None
    notes: str | None = None


class PreviewPayload(BaseModel):
    channel_label: str | None = None
    variables: dict | None = None


class CreateAutomationPayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=2000)
    theme: str
    prompt_template: str = Field(..., min_length=1)
    required_platforms: list[str] = Field(default_factory=list)
    default_cron: str | None = Field(None, max_length=64)
    default_schedule_label: str | None = Field(None, max_length=64)
    cooldown_hours: int = 0
    channel_hints: list[str] = Field(default_factory=list)
    icon: str | None = Field(None, max_length=32)
    variables: list[dict] = Field(default_factory=list)


@router.get("/api/automations")
async def api_list_automations(request: Request, theme: str | None = None, mine: str | None = None):
    _, _, project_id = await _require_user_and_project(request)
    show_mine = mine == "1"

    async with app_state.db_session_factory() as db:
        query = select(Automation).where(Automation.is_active == True)
        if show_mine:
            query = query.where(
                Automation.project_id == project_id,
                Automation.playbook_type == AUTOMATION_TYPE_USER,
            )
        else:
            query = query.where(
                (Automation.playbook_type == AUTOMATION_TYPE_SYSTEM) | (Automation.project_id == project_id)
            )
        if theme and theme in VALID_THEMES:
            query = query.where(Automation.theme == theme)
        query = query.order_by(
            Automation.is_featured.desc(),
            Automation.use_count.desc(),
        )
        result = await db.execute(query)
        automations = result.scalars().all()

    return JSONResponse({"automations": [_format_automation(p) for p in automations]})


@router.get("/api/automations/{slug}")
async def api_get_automation(request: Request, slug: str):
    _, _, _ = await _require_user_and_project(request)
    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(Automation).where(
                Automation.slug == slug,
                Automation.is_active == True,
            )
        )
        automation = result.scalar_one_or_none()
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")
    return JSONResponse(_format_automation(automation))


@router.post("/api/automations/{slug}/preview")
async def api_preview_automation(payload: PreviewPayload, request: Request, slug: str):
    """Render the prompt with the supplied variables — used for live preview."""
    _, _, project_id = await _require_user_and_project(request)
    project = await _project_for(project_id)

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(Automation).where(
                Automation.slug == slug,
                Automation.is_active == True,
            )
        )
        automation = result.scalar_one_or_none()
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")

    resolved = _resolve_variables(automation, payload.variables)
    resolved.setdefault("project_name", getattr(project, "name", ""))
    if payload.channel_label:
        resolved["channel_label"] = payload.channel_label

    rendered = _render_prompt(automation.prompt_template, resolved)
    cron = automation.default_cron or "0 9 * * *"
    task_name = automation.default_task_name or automation.slug

    artifacts = _build_install_artifacts(
        automation,
        project,
        rendered,
        cron,
        task_name,
        payload.channel_label or "<your channel>",
    )

    return JSONResponse(
        {
            "rendered_prompt": rendered,
            "resolved_variables": resolved,
            "install_artifacts": artifacts,
        }
    )


@router.post("/api/automations/{slug}/install")
async def api_install_automation(payload: InstallPayload, request: Request, slug: str):
    _user_ctx, user_uuid, project_id = await _require_user_and_project(request)
    project = await _project_for(project_id)

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(Automation).where(
                Automation.slug == slug,
                Automation.is_active == True,
            )
        )
        automation = result.scalar_one_or_none()
    if not automation:
        raise HTTPException(status_code=404, detail="Automation not found")

    # Tier gate
    user_tier = (getattr(project, "plan", "free") or "free").lower()
    if automation.min_tier == "pro" and user_tier == "free":
        return JSONResponse(
            {
                "error": True,
                "message": f"'{automation.title}' requires the Pro plan.",
                "upgrade_url": f"{settings.APP_BASE_URL}/pricing",
            },
            status_code=403,
        )
    if automation.min_tier == "team" and user_tier not in ("team",):
        return JSONResponse(
            {
                "error": True,
                "message": f"'{automation.title}' requires the Team plan.",
                "upgrade_url": f"{settings.APP_BASE_URL}/pricing",
            },
            status_code=403,
        )

    # Connector compatibility
    connected_platforms = await _project_connected_platforms(project_id)
    missing = [p for p in (automation.required_platforms or []) if p not in connected_platforms]
    if missing:
        return JSONResponse(
            {
                "error": True,
                "message": (
                    f"This automation requires platforms that aren't connected: {', '.join(missing)}."
                ),
                "missing_platforms": missing,
                "connect_url": f"{settings.APP_BASE_URL}/connect",
            },
            status_code=400,
        )

    resolved = _resolve_variables(automation, payload.variables)
    resolved.setdefault("project_name", getattr(project, "name", ""))
    resolved["channel_label"] = payload.channel_label

    rendered = _render_prompt(automation.prompt_template, resolved)
    cron = payload.cron_expression or automation.default_cron or "0 9 * * *"
    task_name = (
        payload.task_name or automation.default_task_name or f"{automation.slug}-{secrets.token_hex(2)}"
    )

    async with app_state.db_session_factory() as db:
        install = AutomationInstallation(
            playbook_id=automation.id,
            project_id=project_id,
            user_id=user_uuid,
            task_name=task_name,
            cron_expression=cron,
            variable_values=resolved,
            channel_summary=payload.channel_label,
            rendered_prompt=rendered,
            status=INSTALL_STATUS_ACTIVE,
            notes=payload.notes,
        )
        db.add(install)
        await db.execute(
            update(Automation)
            .where(Automation.id == automation.id)
            .values(use_count=Automation.use_count + 1)
        )
        await db.commit()
        await db.refresh(install)

    artifacts = _build_install_artifacts(
        automation, project, rendered, cron, task_name, payload.channel_label
    )

    return JSONResponse(
        {
            "success": True,
            "installation_id": str(install.id),
            "automation": _format_automation(automation),
            "rendered_prompt": rendered,
            "cron_expression": cron,
            "task_name": task_name,
            "install_artifacts": artifacts,
        }
    )


@router.post("/api/automations")
async def api_create_automation(payload: CreateAutomationPayload, request: Request):
    _user_ctx, user_uuid, project_id = await _require_user_and_project(request)
    project = await _project_for(project_id)

    if payload.theme not in VALID_THEMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid theme. Must be one of: {', '.join(VALID_THEMES)}",
        )

    user_tier = (getattr(project, "plan", "free") or "free").lower()
    if user_tier == "free":
        return JSONResponse(
            {
                "error": True,
                "message": "Authoring custom automations requires the Pro plan.",
                "upgrade_url": f"{settings.APP_BASE_URL}/pricing",
            },
            status_code=403,
        )

    base_slug = re.sub(r"[^a-z0-9]+", "-", payload.title.lower()).strip("-")[:80] or "automation"
    slug = f"{base_slug}-{secrets.token_urlsafe(4)}"

    async with app_state.db_session_factory() as db:
        automation = Automation(
            project_id=project_id,
            created_by_user_id=user_uuid,
            title=payload.title,
            description=payload.description,
            slug=slug,
            playbook_type=AUTOMATION_TYPE_USER,
            theme=payload.theme,
            icon=payload.icon,
            required_platforms=payload.required_platforms or [],
            prompt_template=payload.prompt_template,
            variables=payload.variables or [],
            default_cron=payload.default_cron,
            default_schedule_label=payload.default_schedule_label,
            cooldown_hours=payload.cooldown_hours or 0,
            channel_hints=payload.channel_hints or [],
            min_tier="pro",
            is_featured=False,
            is_active=True,
        )
        db.add(automation)
        await db.commit()
        await db.refresh(automation)

    return JSONResponse(
        {
            "success": True,
            "automation": _format_automation(automation),
            "view_url": f"/automations/{automation.slug}",
        }
    )


@router.post("/api/automations/installs/{install_id}/status")
async def api_update_install_status(install_id: str, request: Request):
    """Mark an install as active / paused / removed."""
    _, _, project_id = await _require_user_and_project(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    new_status = (body or {}).get("status", "")
    if new_status not in (
        INSTALL_STATUS_ACTIVE,
        INSTALL_STATUS_PAUSED,
        INSTALL_STATUS_REMOVED,
    ):
        raise HTTPException(status_code=400, detail="Invalid status")

    try:
        install_uuid = uuid.UUID(install_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid install_id")

    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(AutomationInstallation).where(
                AutomationInstallation.id == install_uuid,
                AutomationInstallation.project_id == project_id,
            )
        )
        install = result.scalar_one_or_none()
        if not install:
            raise HTTPException(status_code=404, detail="Install not found")
        install.status = new_status
        await db.commit()

    return JSONResponse({"success": True, "status": new_status})
