"""Super-admin instance panel: users + access requests."""

from __future__ import annotations

import logging
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


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Super-admin instance panel (page enforces 401/403)."""
    await require_superadmin(request)
    from app.api.google_oauth_routes import _load_user_view

    user_ctx = await _resolve_user_ctx(request)
    user_view = await _load_user_view(user_ctx)
    return render(request, "admin.html", {"user": user_view, "active": "admin"})


async def require_superadmin(request: Request) -> dict:
    """Resolve the current user and require the instance super-admin flag.

    Returns a small dict {id, email, is_superadmin}. Raises 401 if not
    authenticated, 403 if not a super-admin.
    """
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        raise HTTPException(401, "Not authenticated")
    async with app_state.db_session_factory() as db:
        u = (await db.execute(select(User).where(User.id == uuid.UUID(user_ctx.user_id)))).scalar_one_or_none()
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
                select(func.count()).select_from(User).where(User.is_superadmin == True, User.is_active == True)
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
            await db.execute(
                select(AccessRequest).where(AccessRequest.status == status).order_by(AccessRequest.created_at.asc())
            )
        ).scalars().all()
        items = [
            {"id": str(r.id), "name": r.name, "email": r.email, "use_case": r.use_case,
             "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else None}
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


@router.patch("/api/admin/settings/require-access-approval")
async def admin_toggle_gate(request: Request):
    me = await require_superadmin(request)
    body = await request.json()
    enabled = bool(body.get("enabled"))
    from app.settings_service import set_setting

    async with app_state.db_session_factory() as db:
        await set_setting(db, key="require_access_approval", value=enabled, is_secret=False,
                          updated_by_user_id=uuid.UUID(me["id"]))
        await db.commit()
    return JSONResponse({"success": True, "enabled": enabled})
