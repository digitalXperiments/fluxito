"""Super-admin instance panel: users + access requests."""

from __future__ import annotations

import logging
import re as _re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select

import app.app_state as app_state
from app.api.google_oauth_routes import _resolve_user_ctx
from app.models.user import User
from app.templating import render

logger = logging.getLogger(__name__)
router = APIRouter()

_ACCENT_RE = _re.compile(r"^#?[0-9a-zA-Z]{3,8}$")


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Super-admin instance panel (page enforces 401/403)."""
    await require_superadmin(request)
    from app.api.google_oauth_routes import _load_user_view
    from app.settings_service import access_approval_required

    user_ctx = await _resolve_user_ctx(request)
    user_view = await _load_user_view(user_ctx)
    gate_enabled = await access_approval_required()
    return render(request, "admin.html", {"user": user_view, "active": "admin", "gate_enabled": gate_enabled})


async def require_superadmin(request: Request) -> dict:
    """Resolve the current user and require the instance super-admin flag.

    Returns a small dict {id, email, is_superadmin}. Raises 401 if not
    authenticated, 403 if not a super-admin.
    """
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        raise HTTPException(401, "Not authenticated")
    async with app_state.db_session_factory() as db:
        u = (
            await db.execute(select(User).where(User.id == uuid.UUID(user_ctx.user_id)))
        ).scalar_one_or_none()
        if not u or not u.is_superadmin:
            raise HTTPException(403, "Super-admin only")
        return {"id": str(u.id), "email": u.email, "is_superadmin": True}


@router.get("/api/admin/users")
async def admin_list_users(request: Request):
    await require_superadmin(request)
    async with app_state.db_session_factory() as db:
        rows = (await db.execute(select(User).order_by(User.created_at.asc()))).scalars().all()
        users = [
            {
                "id": str(u.id),
                "email": u.email,
                "display_name": u.display_name,
                "is_active": u.is_active,
                "is_superadmin": u.is_superadmin,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in rows
        ]
    return JSONResponse({"users": users})


@router.patch("/api/admin/users/{user_id}/active")
async def admin_set_active(request: Request, user_id: str):
    me = await require_superadmin(request)
    body = await request.json()
    is_active = bool(body.get("is_active"))
    if user_id == me["id"] and not is_active:
        raise HTTPException(400, "You cannot deactivate your own account.")
    async with app_state.db_session_factory() as db:
        u = await db.get(User, uuid.UUID(user_id))
        if not u:
            raise HTTPException(404, "User not found")
        if u.is_superadmin and not is_active:
            count = await db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.is_superadmin == True, User.is_active == True)
            )
            if count is not None and count <= 1:
                raise HTTPException(400, "Cannot deactivate the last active super-admin.")
        u.is_active = is_active
        await db.commit()
    return JSONResponse({"success": True})


@router.patch("/api/admin/users/{user_id}/superadmin")
async def admin_set_superadmin(request: Request, user_id: str):
    me = await require_superadmin(request)
    body = await request.json()
    is_superadmin = bool(body.get("is_superadmin"))
    async with app_state.db_session_factory() as db:
        u = await db.get(User, uuid.UUID(user_id))
        if not u:
            raise HTTPException(404, "User not found")
        if u.is_superadmin and not is_superadmin:
            count = await db.scalar(select(func.count()).select_from(User).where(User.is_superadmin == True))
            if count is not None and count <= 1:
                raise HTTPException(400, "Cannot remove the last super-admin.")
        u.is_superadmin = is_superadmin
        await db.commit()
    return JSONResponse({"success": True})


@router.get("/api/admin/access-requests")
async def admin_list_access_requests(request: Request):
    await require_superadmin(request)
    status = request.query_params.get("status", "pending")
    from app.models.access_request import AccessRequest

    async with app_state.db_session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AccessRequest)
                    .where(AccessRequest.status == status)
                    .order_by(AccessRequest.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        items = [
            {
                "id": str(r.id),
                "name": r.name,
                "email": r.email,
                "use_case": r.use_case,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    return JSONResponse({"requests": items})


@router.post("/api/admin/access-requests/{req_id}/approve")
async def admin_approve_access_request(request: Request, req_id: str):
    me = await require_superadmin(request)
    from app.auth.email_auth import generate_temp_password, hash_password
    from app.models.access_request import AccessRequest

    async with app_state.db_session_factory() as db:
        r = await db.get(AccessRequest, uuid.UUID(req_id))
        if not r:
            raise HTTPException(404, "Request not found")
        if r.status != "pending":
            raise HTTPException(400, f"Request already {r.status}.")
        req_email = r.email
        req_name = r.name

        temp_password = None
        existing = (await db.execute(select(User).where(User.email == req_email))).scalar_one_or_none()
        if existing is None:
            temp_password = generate_temp_password()
            new_user = User(
                email=req_email,
                display_name=req_name,
                password_hash=hash_password(temp_password),
                email_verified=True,
                auth_provider="email",
            )
            db.add(new_user)
            await db.flush()
            new_uid = new_user.id
        else:
            # Account already exists — never reset an existing password (takeover guard).
            new_uid = existing.id

        r.status = "approved"
        r.reviewed_by = uuid.UUID(me["id"])
        r.reviewed_at = datetime.now(UTC)
        await db.commit()

    try:
        from app.api.project_routes import ensure_default_project

        await ensure_default_project(new_uid, req_name, req_email)
    except Exception:
        logger.warning("ensure_default_project failed after approval", exc_info=True)

    return JSONResponse({"success": True, "email": req_email, "temp_password": temp_password})


@router.post("/api/admin/access-requests/{req_id}/reject")
async def admin_reject_access_request(request: Request, req_id: str):
    me = await require_superadmin(request)
    from app.models.access_request import AccessRequest

    async with app_state.db_session_factory() as db:
        r = await db.get(AccessRequest, uuid.UUID(req_id))
        if not r:
            raise HTTPException(404, "Request not found")
        r.status = "rejected"
        r.reviewed_by = uuid.UUID(me["id"])
        r.reviewed_at = datetime.now(UTC)
        await db.commit()
    return JSONResponse({"success": True})


@router.get("/api/admin/settings/rate-limits")
async def admin_get_rate_limits(request: Request):
    await require_superadmin(request)
    from app.settings_service import get_runtime_setting

    async with app_state.db_session_factory() as db:
        per_min = int(await get_runtime_setting(db, "rate_limit_per_min", default=60))
        per_hour = int(await get_runtime_setting(db, "rate_limit_per_hour", default=1000))
        per_day = int(await get_runtime_setting(db, "rate_limit_per_day", default=10000))
    return JSONResponse({"per_min": per_min, "per_hour": per_hour, "per_day": per_day})


@router.patch("/api/admin/settings/rate-limits")
async def admin_set_rate_limits(request: Request):
    me = await require_superadmin(request)
    body = await request.json()
    try:
        per_min = int(body.get("per_min"))
        per_hour = int(body.get("per_hour"))
        per_day = int(body.get("per_day"))
    except (TypeError, ValueError):
        raise HTTPException(400, "per_min, per_hour and per_day must be integers.")
    if per_min <= 0 or per_hour <= 0 or per_day <= 0:
        raise HTTPException(400, "Rate limits must be positive.")
    if not (per_min <= per_hour <= per_day):
        raise HTTPException(400, "Limits must be ordered: per-minute ≤ per-hour ≤ per-day.")

    from app.auth.rate_limiter import set_rate_limits
    from app.settings_service import set_setting

    async with app_state.db_session_factory() as db:
        for key, val in (
            ("rate_limit_per_min", per_min),
            ("rate_limit_per_hour", per_hour),
            ("rate_limit_per_day", per_day),
        ):
            await set_setting(db, key=key, value=val, is_secret=False, updated_by_user_id=uuid.UUID(me["id"]))
        await db.commit()
    # Push to the Redis override + bust the in-memory cache so limits apply now.
    try:
        await set_rate_limits({"default": {"per_min": per_min, "per_hour": per_hour, "per_day": per_day}})
    except Exception:
        logger.warning("rate-limit Redis override failed; DB values will apply within 60s", exc_info=True)
    return JSONResponse({"success": True, "per_min": per_min, "per_hour": per_hour, "per_day": per_day})


@router.patch("/api/admin/settings/require-access-approval")
async def admin_toggle_gate(request: Request):
    me = await require_superadmin(request)
    body = await request.json()
    enabled = bool(body.get("enabled"))
    from app.settings_service import set_setting

    async with app_state.db_session_factory() as db:
        await set_setting(
            db,
            key="require_access_approval",
            value=enabled,
            is_secret=False,
            updated_by_user_id=uuid.UUID(me["id"]),
        )
        await db.commit()
    return JSONResponse({"success": True, "enabled": enabled})


@router.get("/api/admin/settings/branding")
async def admin_get_branding(request: Request):
    await require_superadmin(request)
    from app.settings_service import get_runtime_setting

    async with app_state.db_session_factory() as db:
        name = await get_runtime_setting(db, "brand_name", default="Fluxito")
        logo_url = await get_runtime_setting(db, "brand_logo_url", default="")
        accent = await get_runtime_setting(db, "brand_accent", default="")
    return JSONResponse({"name": str(name), "logo_url": str(logo_url or ""), "accent": str(accent or "")})


@router.patch("/api/admin/settings/branding")
async def admin_set_branding(request: Request):
    me = await require_superadmin(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    logo_url = (body.get("logo_url") or "").strip()
    accent = (body.get("accent") or "").strip()
    if not name or len(name) > 120:
        raise HTTPException(400, "App name is required (max 120 chars).")
    if len(logo_url) > 500:
        raise HTTPException(400, "Logo URL is too long.")
    if accent and not _ACCENT_RE.match(accent):
        raise HTTPException(400, "Accent must be a simple colour (e.g. #0B0B0E).")

    from app.branding import refresh_brand
    from app.settings_service import set_setting

    async with app_state.db_session_factory() as db:
        for key, val in (("brand_name", name), ("brand_logo_url", logo_url), ("brand_accent", accent)):
            await set_setting(db, key=key, value=val, is_secret=False, updated_by_user_id=uuid.UUID(me["id"]))
        await db.commit()
    await refresh_brand()
    return JSONResponse({"success": True, "name": name, "logo_url": logo_url, "accent": accent})


# ---------------------------------------------------------------------------
# Instance operations — maintenance mode + announcement banner
# ---------------------------------------------------------------------------


@router.get("/api/admin/settings/operations")
async def admin_get_operations(request: Request):
    await require_superadmin(request)
    from app.settings_service import get_runtime_setting

    async with app_state.db_session_factory() as db:
        maintenance = bool(await get_runtime_setting(db, "maintenance_mode", default=False))
        banner = await get_runtime_setting(db, "announcement_banner", default="")
    return JSONResponse({"maintenance_mode": maintenance, "announcement_banner": str(banner or "")})


@router.patch("/api/admin/settings/operations")
async def admin_set_operations(request: Request):
    me = await require_superadmin(request)
    body = await request.json()
    maintenance = bool(body.get("maintenance_mode"))
    banner = (body.get("announcement_banner") or "").strip()
    if len(banner) > 280:
        raise HTTPException(400, "Announcement banner is too long (max 280 chars).")
    from app.settings_service import set_setting

    async with app_state.db_session_factory() as db:
        await set_setting(
            db,
            key="maintenance_mode",
            value=maintenance,
            is_secret=False,
            updated_by_user_id=uuid.UUID(me["id"]),
        )
        await set_setting(
            db,
            key="announcement_banner",
            value=banner,
            is_secret=False,
            updated_by_user_id=uuid.UUID(me["id"]),
        )
        await db.commit()
    from app.branding import refresh_announcement

    await refresh_announcement()
    return JSONResponse({"success": True, "maintenance_mode": maintenance, "announcement_banner": banner})


# ---------------------------------------------------------------------------
# Sign-in method toggles
# ---------------------------------------------------------------------------


@router.get("/api/admin/settings/auth-methods")
async def admin_get_auth_methods(request: Request):
    await require_superadmin(request)
    from app.settings_service import get_runtime_setting

    async with app_state.db_session_factory() as db:
        google = bool(await get_runtime_setting(db, "auth_google_enabled", default=True))
        password = bool(await get_runtime_setting(db, "auth_password_enabled", default=True))
    return JSONResponse({"google_enabled": google, "password_enabled": password})


@router.patch("/api/admin/settings/auth-methods")
async def admin_set_auth_methods(request: Request):
    me = await require_superadmin(request)
    body = await request.json()
    google = bool(body.get("google_enabled"))
    password = bool(body.get("password_enabled"))
    if not google and not password:
        raise HTTPException(400, "At least one sign-in method must stay enabled.")
    from app.settings_service import set_setting

    async with app_state.db_session_factory() as db:
        await set_setting(
            db,
            key="auth_google_enabled",
            value=google,
            is_secret=False,
            updated_by_user_id=uuid.UUID(me["id"]),
        )
        await set_setting(
            db,
            key="auth_password_enabled",
            value=password,
            is_secret=False,
            updated_by_user_id=uuid.UUID(me["id"]),
        )
        await db.commit()
    return JSONResponse({"success": True, "google_enabled": google, "password_enabled": password})


# ---------------------------------------------------------------------------
# Direct user invite — create an account + temp password without a request
# ---------------------------------------------------------------------------

_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/api/admin/users/invite")
async def admin_invite_user(request: Request):
    me = await require_superadmin(request)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    name = (body.get("name") or "").strip() or email.split("@")[0]
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address.")

    from app.auth.email_auth import generate_temp_password, hash_password

    temp_password = None
    async with app_state.db_session_factory() as db:
        existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(400, "A user with that email already exists.")
        temp_password = generate_temp_password()
        new_user = User(
            email=email,
            display_name=name,
            password_hash=hash_password(temp_password),
            email_verified=True,  # invited by an operator — trusted
            auth_provider="email",
        )
        db.add(new_user)
        await db.flush()
        new_uid = new_user.id
        await db.commit()

    try:
        from app.api.project_routes import ensure_default_project

        await ensure_default_project(new_uid, name, email)
    except Exception:
        logger.warning("ensure_default_project failed after invite", exc_info=True)

    # Best-effort invite email (logs to console if SMTP isn't configured).
    try:
        from app.branding import brand as _brand
        from app.config import settings as _settings
        from app.email_service import send_email

        brand_name = _brand()["name"]
        base_url = _settings.APP_BASE_URL.rstrip("/")
        html_body = (
            f"<p>You've been invited to {brand_name}.</p>"
            f"<p>Sign in at <a href='{base_url}/signin'>{base_url}/signin</a> with:</p>"
            f"<p><b>Email:</b> {email}<br><b>Temporary password:</b> {temp_password}</p>"
            f"<p>Please change your password after your first sign-in.</p>"
        )
        text_body = (
            f"You've been invited to {brand_name}.\n\n"
            f"Sign in at {base_url}/signin\nEmail: {email}\nTemporary password: {temp_password}\n\n"
            "Please change your password after your first sign-in."
        )
        await send_email(email, f"You're invited to {brand_name}", html_body, text_body)
    except Exception:
        logger.warning("invite email failed for %s", email, exc_info=True)

    return JSONResponse({"success": True, "email": email, "temp_password": temp_password})
