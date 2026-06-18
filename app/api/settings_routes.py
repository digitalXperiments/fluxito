"""Consolidated /settings page — role-scoped settings shell.

All individual settings pages (/profile, /project/{slug}/settings,
/settings/integrations, /settings/system, /admin) redirect here unless
``?embed=1`` is present (used by the iframe panels on this page itself).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

import app.app_state as app_state
from app.auth.uid_cookie import get_uid_from_request
from app.models.project import ProjectMember
from app.models.user import User
from app.templating import render

router = APIRouter()


async def _resolve_settings_flags(
    uid_str: str,
) -> tuple[bool, bool]:
    """Return (is_superadmin, is_install_admin) for the given user id string.

    is_superadmin: User.is_superadmin flag on the User row.
    is_install_admin: owner or admin of any active project membership.
    """
    user_uuid = uuid.UUID(uid_str)
    async with app_state.db_session_factory() as db:
        user_row = (await db.execute(select(User).where(User.id == user_uuid))).scalar_one_or_none()

        if user_row is None:
            return False, False

        is_superadmin: bool = bool(user_row.is_superadmin)

        is_install_admin_result = (
            await db.execute(
                select(ProjectMember.id)
                .where(ProjectMember.user_id == user_uuid)
                .where(ProjectMember.role.in_(("owner", "admin")))
                .where(ProjectMember.is_active.is_(True))
                .limit(1)
            )
        ).scalar_one_or_none()

        is_install_admin: bool = is_install_admin_result is not None

    return is_superadmin, is_install_admin


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Consolidated role-scoped settings shell."""
    uid = get_uid_from_request(request)
    if not uid:
        return RedirectResponse("/signin?next=/settings", status_code=302)

    # Load user view for nav/sidebar context.
    from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx

    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/settings", status_code=302)

    user_view = await _load_user_view(user_ctx)

    is_superadmin, is_install_admin = await _resolve_settings_flags(uid)

    # Resolve the active project slug from nav context (set by middleware).
    active_project_slug: str | None = None
    active_project_name: str | None = None
    state = getattr(request, "state", None)
    if state is not None:
        nav_projects = getattr(state, "nav_projects", [])
        active_pid = getattr(state, "active_project_id", None)
        if active_pid and nav_projects:
            for p in nav_projects:
                if p["id"] == active_pid:
                    active_project_slug = p["slug"]
                    active_project_name = p["name"]
                    break
        elif nav_projects:
            active_project_slug = nav_projects[0]["slug"]
            active_project_name = nav_projects[0]["name"]

    return render(
        request,
        "settings/index.html",
        {
            "user": user_view,
            "active": "settings",
            "is_superadmin": is_superadmin,
            "is_install_admin": is_install_admin,
            "active_project_slug": active_project_slug,
            "active_project_name": active_project_name,
        },
    )
