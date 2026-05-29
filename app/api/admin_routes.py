"""Super-admin instance panel: users + access requests."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

import app.app_state as app_state
from app.api.google_oauth_routes import _resolve_user_ctx
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


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
