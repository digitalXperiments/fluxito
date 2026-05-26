"""
Automation Library MCP Tools
=============================

Cowork-native scheduled-monitor recipes. An automation is a pure prompt + variables
+ cron that Claude in Cowork executes on its own scheduler, so we incur no
server-side compute.

The public MCP surface exposes ``automation_read`` / ``automation_write``
dispatchers; this module registers the underlying ``automation_browse`` and
``automation_action`` handlers that get wired in via ``app/tools/unified.py``.

  automation_browse  — read. No ``slug`` → library listing (filtered by
                       theme + connector compatibility). With ``slug`` →
                       single automation with its rendered prompt preview.
  automation_action  — write dispatcher.
                         action="install" → record an install AND return
                         everything Cowork needs to call ``create_scheduled_task``
                         (rendered prompt + cron + task name). The actual
                         scheduler call still happens on the Cowork side; this
                         action is the bridge.
                         action="save"    → author a custom user automation
                         scoped to the active project.

Design notes
------------
* We never schedule jobs ourselves. Each install row stores the rendered
  prompt + cron at the moment of install, so the Web UI can show users
  exactly what Cowork was asked to run, even if the curated template
  changes upstream.
* Variable rendering is intentionally simple: ``{{ key }}`` and ``{{key}}``
  literal substitution, no expression language, no loops. If you need
  conditionals, write them in the prompt body and let Claude figure it out.
* ``action="install"`` ALWAYS substitutes the two base variables
  ``project_name`` and ``channel_label`` from the active project context +
  user-supplied channel — they are required for every curated automation.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid

from sqlalchemy import select, update

import app.app_state as state
from app.config import settings
from app.models.automation import (
    AUTOMATION_TYPE_SYSTEM,
    AUTOMATION_TYPE_USER,
    INSTALL_STATUS_ACTIVE,
    THEME_LABELS,
    VALID_THEMES,
    Automation,
    AutomationInstallation,
)
from app.tools.shared_helpers import get_current_user

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_PLATFORM_FLAG_MAP = {
    "ga4": "has_ga4",
    "gtm": "has_gtm",
    "google_ads": "has_ads",
    "meta": "has_meta",
    "tiktok": "has_tiktok",
    "snap": "has_snap",
    "linkedin": "has_linkedin",
    "pinterest": "has_pinterest",
    "bigquery": "has_bq",
    "amplitude": "has_amplitude",
    "adobe_analytics": "has_adobe_analytics",
    "adobe_launch": "has_adobe_launch",
    "redshift": "has_redshift",
    "snowflake": "has_snowflake",
}


def _check_platform_compatibility(user, required_platforms: list) -> dict:
    """Return which of the required platforms the active project has."""
    project = None
    try:
        project = state.current_project_ctx.get()
    except LookupError:
        project = None

    src = project or user

    connected: list[str] = []
    missing: list[str] = []
    for p in required_platforms or []:
        attr = _PLATFORM_FLAG_MAP.get(p)
        if attr and getattr(src, attr, False):
            connected.append(p)
        else:
            missing.append(p)
    return {
        "all_connected": len(missing) == 0,
        "connected": connected,
        "missing": missing,
    }


def _render_prompt(template_text: str, values: dict) -> str:
    """Substitute ``{{key}}`` / ``{{ key }}`` placeholders.

    Unrecognised placeholders are left intact so the user (or Claude) can
    spot what wasn't filled in.
    """
    out = template_text or ""
    for key, val in values.items():
        if val is None:
            continue
        pattern = re.compile(r"\{\{\s*" + re.escape(key) + r"\s*\}\}")
        out = pattern.sub(str(val), out)
    return out


def _resolve_variables(automation: Automation, supplied: dict | None) -> dict:
    """Compose default values from the variable definitions + user overrides."""
    resolved: dict = {}
    for var_def in automation.variables or []:
        key = var_def.get("key")
        if not key:
            continue
        if "default" in var_def and var_def["default"] is not None:
            resolved[key] = var_def["default"]
    if supplied:
        for k, v in supplied.items():
            if v is not None:
                resolved[k] = v
    return resolved


def _serialize_automation(p: Automation, user, *, include_prompt: bool = False) -> dict:
    """Shape an Automation row for tool/UI responses."""
    compat = _check_platform_compatibility(user, p.required_platforms)
    base = settings.APP_BASE_URL
    out = {
        "id": str(p.id),
        "slug": p.slug,
        "title": p.title,
        "description": p.description,
        "theme": p.theme,
        "theme_label": p.theme_label(),
        "icon": p.icon,
        "type": p.playbook_type,
        "required_platforms": p.required_platforms,
        "variables": p.variables,
        "default_cron": p.default_cron,
        "default_schedule_label": p.default_schedule_label,
        "default_task_name": p.default_task_name,
        "cooldown_hours": p.cooldown_hours,
        "channel_hints": p.channel_hints,
        "min_tier": p.min_tier,
        "is_featured": p.is_featured,
        "use_count": p.use_count,
        "compatibility": compat,
        "view_url": f"{base}/automations/{p.slug}",
    }
    if include_prompt:
        out["prompt_template"] = p.prompt_template
    return out


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def _err(error_type: str, message: str, **extra) -> dict:
    out = {"error": True, "error_type": error_type, "message": message}
    out.update(extra)
    return out


# Cowork's create_scheduled_task expects taskId in kebab-case. Mirrors the
# safety-net sanitizer documented on the upstream tool — lowercase, ASCII,
# hyphen-separated. We generate the raw candidate from the automation slug and
# a short entropy suffix so the same automation can be installed multiple times
# side-by-side without collision.
_TASK_ID_RE = re.compile(r"[^a-z0-9]+")


def _kebab_task_id(raw: str) -> str:
    cleaned = _TASK_ID_RE.sub("-", raw.lower()).strip("-")
    return cleaned or "automation-task"


async def _do_browse(
    user,
    *,
    slug: str | None,
    theme: str | None,
    channel_label: str | None,
    variables: dict | None,
    show_all: bool,
) -> dict:
    """Read path: list view (no slug) OR single automation with rendered preview."""
    project_id: uuid.UUID | None = None
    proj = None
    try:
        proj = state.current_project_ctx.get()
        if proj:
            project_id = uuid.UUID(proj.project_id)
    except LookupError:
        pass

    # ── Single-automation get ────────────────────────────────────────────
    if slug:
        async with state.db_session_factory() as db:
            result = await db.execute(
                select(Automation).where(
                    Automation.slug == slug,
                    Automation.is_active == True,
                )
            )
            automation = result.scalar_one_or_none()

        if not automation:
            return _err(
                "not_found",
                f"Automation '{slug}' not found.",
                browse_url=f"{settings.APP_BASE_URL}/automations",
            )

        resolved = _resolve_variables(automation, variables)
        if proj and "project_name" not in resolved:
            resolved["project_name"] = proj.project_name
        if channel_label:
            resolved["channel_label"] = channel_label
        rendered = _render_prompt(automation.prompt_template, resolved)

        out = _serialize_automation(automation, user, include_prompt=True)
        out["resolved_variables"] = resolved
        out["rendered_prompt"] = rendered
        out["install_hint"] = (
            "Call automation_write(action='install', params={'slug': ..., 'channel_label': ...}) "
            "to record the install and get the exact arguments to pass to "
            "Cowork's create_scheduled_task tool."
        )
        return out

    # ── List view ──────────────────────────────────────────────────────
    if theme and theme not in VALID_THEMES:
        return _err(
            "bad_request",
            f"Unknown theme '{theme}'. Valid themes: {', '.join(VALID_THEMES)}.",
        )

    async with state.db_session_factory() as db:
        query = select(Automation).where(Automation.is_active == True)
        # System automations are visible everywhere; project automations only
        # surface when the call is made from inside that project.
        if project_id:
            query = query.where(
                (Automation.playbook_type == AUTOMATION_TYPE_SYSTEM) | (Automation.project_id == project_id)
            )
        else:
            query = query.where(Automation.playbook_type == AUTOMATION_TYPE_SYSTEM)

        if theme:
            query = query.where(Automation.theme == theme)

        query = query.order_by(
            Automation.is_featured.desc(),
            Automation.use_count.desc(),
            Automation.created_at.desc(),
        )
        result = await db.execute(query)
        automations = result.scalars().all()

    items: list[dict] = []
    for p in automations:
        data = _serialize_automation(p, user)
        if not show_all and not data["compatibility"]["all_connected"]:
            # Still surface featured curated automations so users see what's
            # possible even if they're missing a connector.
            if not p.is_featured:
                continue
            data["needs_connection"] = True
        items.append(data)

    return {
        "automations": items,
        "total": len(items),
        "themes": [{"key": t, "label": THEME_LABELS[t]} for t in VALID_THEMES],
        "browse_url": f"{settings.APP_BASE_URL}/automations",
    }


async def _do_install(
    user,
    *,
    slug: str | None,
    channel_label: str | None,
    variables: dict | None,
    cron_expression: str | None,
    task_name: str | None,
) -> dict:
    """Write path: action='install'."""
    if not slug:
        return _err("bad_request", "slug is required for action='install'.")
    if not channel_label:
        return _err("bad_request", "channel_label is required for action='install'.")

    try:
        proj = state.current_project_ctx.get()
    except LookupError:
        proj = None
    if not proj:
        return _err(
            "no_active_project",
            "No project is active. Use set_active_project first so the "
            "automation can be scoped to the right project's data.",
        )

    async with state.db_session_factory() as db:
        result = await db.execute(
            select(Automation).where(
                Automation.slug == slug,
                Automation.is_active == True,
            )
        )
        automation = result.scalar_one_or_none()

    if not automation:
        return _err(
            "not_found",
            f"Automation '{slug}' not found.",
            browse_url=f"{settings.APP_BASE_URL}/automations",
        )

    # Platform compatibility gate
    compat = _check_platform_compatibility(user, automation.required_platforms)
    if not compat["all_connected"]:
        return _err(
            "missing_platforms",
            f"This automation requires platforms that aren't connected: {', '.join(compat['missing'])}.",
            connect_url=f"{settings.APP_BASE_URL}/connect",
            missing_platforms=compat["missing"],
        )

    # Resolve variables and render the prompt
    resolved = _resolve_variables(automation, variables)
    resolved.setdefault("project_name", proj.project_name)
    resolved["channel_label"] = channel_label

    rendered = _render_prompt(automation.prompt_template, resolved)

    cron = cron_expression or automation.default_cron or "0 9 * * *"

    # The human-facing task label (used for our own install row + the
    # copy/paste card on the Web UI).
    task = task_name or automation.default_task_name or f"{automation.title} — {proj.project_name}"

    # Cowork's create_scheduled_task expects a kebab-case `taskId`. We
    # suffix with 4 hex chars so the same automation can be installed twice
    # in the same project without a collision.
    task_id = _kebab_task_id(f"{automation.slug}-{secrets.token_hex(2)}")

    # Short one-liner for the skill frontmatter / Cowork sidebar. The full
    # rendered prompt goes into `prompt`, not `description`.
    short_desc = (
        automation.description or f"{automation.title} — runs on {automation.default_schedule_label or cron}"
    )
    if len(short_desc) > 140:
        short_desc = short_desc[:137] + "…"

    # Persist the install row
    install = None
    try:
        async with state.db_session_factory() as db:
            install = AutomationInstallation(
                playbook_id=automation.id,
                project_id=uuid.UUID(proj.project_id),
                user_id=uuid.UUID(user.user_id),
                task_name=task,
                cron_expression=cron,
                variable_values=resolved,
                channel_summary=channel_label,
                rendered_prompt=rendered,
                status=INSTALL_STATUS_ACTIVE,
            )
            db.add(install)
            await db.execute(
                update(Automation)
                .where(Automation.id == automation.id)
                .values(use_count=Automation.use_count + 1)
            )
            await db.commit()
            await db.refresh(install)
    except Exception as exc:
        logger.warning(f"Failed to persist automation install: {exc}")
        install = None

    return {
        "success": True,
        "automation": {
            "slug": automation.slug,
            "title": automation.title,
            "theme": automation.theme,
        },
        "installation_id": str(install.id) if install else None,
        "next_step": (
            "Call Cowork's `create_scheduled_task` tool with the exact "
            "values in `scheduled_task_args` below. Cowork's scheduler "
            "will run the automation prompt on the given cron — Analytics "
            "MCP does not run it."
        ),
        "scheduled_task_args": {
            "taskId": task_id,
            "prompt": rendered,
            "description": short_desc,
            "cronExpression": cron,
        },
        "task_label": task,
        "schedule_label": automation.default_schedule_label,
        "channel_label": channel_label,
        "manage_url": f"{settings.APP_BASE_URL}/automations",
    }


async def _do_save(
    user,
    *,
    title: str | None,
    description: str | None,
    theme: str | None,
    prompt_template: str | None,
    required_platforms: list[str] | None,
    default_cron: str | None,
    default_schedule_label: str | None,
    channel_hints: list[str] | None,
    cooldown_hours: int,
    variables: list[dict] | None,
    icon: str | None,
) -> dict:
    """Write path: action='save'."""
    if not title or not prompt_template or not theme:
        return _err(
            "bad_request",
            "title, theme, and prompt_template are required for action='save'.",
        )

    try:
        proj = state.current_project_ctx.get()
    except LookupError:
        proj = None
    if not proj:
        return _err(
            "no_active_project",
            "Pick a project with set_active_project first.",
        )

    if theme not in VALID_THEMES:
        return _err(
            "bad_request",
            f"Unknown theme '{theme}'. Valid themes: {', '.join(VALID_THEMES)}.",
        )

    # Generate slug
    base_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "automation"
    slug = f"{base_slug}-{secrets.token_urlsafe(4)}"

    async with state.db_session_factory() as db:
        automation = Automation(
            project_id=uuid.UUID(proj.project_id),
            created_by_user_id=uuid.UUID(user.user_id),
            title=title,
            description=description,
            slug=slug,
            playbook_type=AUTOMATION_TYPE_USER,
            theme=theme,
            icon=icon,
            required_platforms=required_platforms or [],
            prompt_template=prompt_template,
            variables=variables or [],
            default_cron=default_cron,
            default_schedule_label=default_schedule_label,
            cooldown_hours=cooldown_hours or 0,
            channel_hints=channel_hints or [],
            min_tier="pro",
            is_featured=False,
            is_active=True,
        )
        db.add(automation)
        await db.commit()
        await db.refresh(automation)

    return {
        "success": True,
        "automation_id": str(automation.id),
        "slug": automation.slug,
        "title": automation.title,
        "theme": automation.theme,
        "view_url": f"{settings.APP_BASE_URL}/automations/{automation.slug}",
        "note": (
            f"Custom automation '{title}' saved. Install it via "
            f"automation_write(action='install', params={{'slug': '{automation.slug}', "
            f"'channel_label': ...}})."
        ),
    }


def register_automation_tools(mcp_server):
    @mcp_server.tool("automation_browse")
    async def automation_browse(
        slug: str | None = None,
        theme: str | None = None,
        channel_label: str | None = None,
        variables: dict | None = None,
        show_all: bool = False,
    ) -> dict:
        """Browse the automation library OR fetch a single automation.

        Read-only tool that combines list + get:

          • Omit ``slug`` to get the library listing for the active project.
            Filter by ``theme`` (one of daily_digest, anomaly, pacing,
            exec_summary, tag_health, launch_monitor). ``show_all=True``
            returns every active automation including ones whose required
            platforms aren't connected.

          • Pass ``slug`` to fetch a single automation, including its rendered
            prompt preview. ``channel_label`` and ``variables`` are folded
            into the preview so you can show the user what will actually run.

        After picking an automation, call ``automation_write(action='install', params={...})``
        to record the install and get the arguments for Cowork's
        ``create_scheduled_task`` tool.
        """
        user = get_current_user()
        if not user:
            return _err("unauthenticated", "No active session.")
        return await _do_browse(
            user,
            slug=slug,
            theme=theme,
            channel_label=channel_label,
            variables=variables,
            show_all=show_all,
        )

    @mcp_server.tool("automation_action")
    async def automation_action(
        action: str,
        # install args
        slug: str | None = None,
        channel_label: str | None = None,
        variables: dict | None = None,
        cron_expression: str | None = None,
        task_name: str | None = None,
        # save args
        title: str | None = None,
        description: str | None = None,
        theme: str | None = None,
        prompt_template: str | None = None,
        required_platforms: list[str] | None = None,
        default_cron: str | None = None,
        default_schedule_label: str | None = None,
        channel_hints: list[str] | None = None,
        cooldown_hours: int = 0,
        icon: str | None = None,
    ) -> dict:
        """Write dispatcher for automations.

        action='install'
            Install an automation into the active project. Records the install
            on the Fluxito side AND returns everything Cowork's
            ``create_scheduled_task`` tool needs (rendered prompt, cron,
            task name). The caller (Claude in Cowork) is expected to chain
            this with ``create_scheduled_task`` to actually schedule the run.

            Required: ``slug``, ``channel_label``.
            Optional: ``variables`` (overrides), ``cron_expression`` (override
            automation default), ``task_name`` (override automation default).
            ``channel_label`` is free-text destination, e.g. "Slack #growth"
            or "alerts@acme.com".

        action='save'
            Author a custom automation scoped to the active project.
            The new automation becomes installable via action='install'.

            Required: ``title``, ``theme``, ``prompt_template``.
            Use ``{{project_name}}`` and ``{{channel_label}}`` in the prompt
            for install-time substitution. ``required_platforms`` is the
            list of platform keys (e.g. ["meta", "ga4"]). ``default_cron``
            is interpreted in the user's local TZ. ``channel_hints`` is
            ["slack"] / ["email"] / both, used by the install UI.
            ``variables`` is reused as additional variable definitions.
        """
        user = get_current_user()
        if not user:
            return _err("unauthenticated", "No active session.")

        action_norm = (action or "").strip().lower()
        if action_norm == "install":
            return await _do_install(
                user,
                slug=slug,
                channel_label=channel_label,
                variables=variables,
                cron_expression=cron_expression,
                task_name=task_name,
            )
        if action_norm == "save":
            return await _do_save(
                user,
                title=title,
                description=description,
                theme=theme,
                prompt_template=prompt_template,
                required_platforms=required_platforms,
                default_cron=default_cron,
                default_schedule_label=default_schedule_label,
                channel_hints=channel_hints,
                cooldown_hours=cooldown_hours,
                variables=variables,  # reused for save's variable defs
                icon=icon,
            )
        return _err(
            "bad_request",
            f"Unknown action '{action}'. Valid actions: 'install', 'save'.",
        )
