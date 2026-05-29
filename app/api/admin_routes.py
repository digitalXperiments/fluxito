"""Super-admin instance panel: users + access requests."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

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
