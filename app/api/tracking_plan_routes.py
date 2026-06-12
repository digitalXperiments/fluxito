"""HTTP API + page for the structured tracking plan.

Writes reuse the tested run_action core (app/tools/tracking_plan_tools.py); the
route layer only resolves auth + the active project/branch and commits."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import desc, select

import app.app_state as app_state
from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx
from app.api.project_routes import ensure_active_project, set_active_project_cookie
from app.models.project import ProjectMember
from app.models.tracking_plan import TPVersion
from app.services.tracking_plan import (
    get_main_branch,
    get_or_create_plan,
    plan_to_dict,
    validate_plan,
)
from app.templating import render
from app.tools.tracking_plan_tools import _Ctx, run_action

router = APIRouter()


class ActionPayload(BaseModel):
    action: str
    params: dict = {}


async def _resolve(request: Request) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Return (user_uuid, project_uuid, role). Raises HTTP 401/400/403."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        user_uuid = uuid.UUID(user_ctx.user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized")
    pid_str = await ensure_active_project(request, user_ctx.user_id)
    if not pid_str:
        raise HTTPException(status_code=400, detail="No active project")
    project_uuid = uuid.UUID(pid_str)
    async with app_state.db_session_factory() as db:
        member = (
            await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_uuid,
                    ProjectMember.user_id == user_uuid,
                    ProjectMember.is_active == True,
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(status_code=403, detail="Not a project member")
        return user_uuid, project_uuid, member.role


def _check_param_pid(param_pid: str, active: uuid.UUID) -> None:
    try:
        if uuid.UUID(param_pid) != active:
            raise HTTPException(status_code=403, detail="Project mismatch")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id")


# ----------------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------------
@router.get("/tracking-plan")
async def tracking_plan_page(request: Request):
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/tracking-plan", status_code=302)
    pid_str = await ensure_active_project(request, user_ctx.user_id)
    if not pid_str:
        return RedirectResponse("/projects", status_code=302)
    user_view = await _load_user_view(user_ctx)
    response = render(
        request,
        "tracking_plan.html",
        {"user": user_view, "active": "tracking_plan", "project_id": pid_str},
    )
    set_active_project_cookie(response, pid_str)
    return response


# ----------------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------------
@router.get("/api/projects/{project_id}/tracking-plan")
async def api_get_plan(project_id: str, request: Request):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        branch = await get_main_branch(db, plan)
        data = await plan_to_dict(db, plan, branch)
        await db.commit()  # persist auto-created plan/branch
        return JSONResponse(data)


@router.get("/api/projects/{project_id}/tracking-plan/validate")
async def api_validate(project_id: str, request: Request):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        branch = await get_main_branch(db, plan)
        report = await validate_plan(db, plan, branch)
        await db.commit()
        return JSONResponse(report)


@router.get("/api/projects/{project_id}/tracking-plan/versions")
async def api_versions(project_id: str, request: Request):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        rows = (
            (
                await db.execute(
                    select(TPVersion)
                    .where(TPVersion.plan_id == plan.id)
                    .order_by(desc(TPVersion.published_at))
                )
            )
            .scalars()
            .all()
        )
        await db.commit()
        return JSONResponse(
            {
                "versions": [
                    {
                        "id": str(v.id),
                        "version_number": v.version_number,
                        "changelog": v.changelog,
                        "published_at": v.published_at.isoformat() if v.published_at else None,
                    }
                    for v in rows
                ]
            }
        )


@router.get("/api/projects/{project_id}/tracking-plan/versions/{version_id}")
async def api_version_snapshot(project_id: str, version_id: str, request: Request):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        await db.commit()
        v = await db.get(TPVersion, uuid.UUID(version_id))
        if v is None or v.plan_id != plan.id:
            raise HTTPException(status_code=404, detail="Version not found")
        return JSONResponse({"version_number": v.version_number, "snapshot": v.snapshot})


# ----------------------------------------------------------------------------
# Writes — single action endpoint reusing run_action
# ----------------------------------------------------------------------------
@router.post("/api/projects/{project_id}/tracking-plan/action")
async def api_action(project_id: str, payload: ActionPayload, request: Request):
    user_uuid, proj_id, role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        branch = await get_main_branch(db, plan)
        ctx = _Ctx(role=role, user_id=str(user_uuid), project_id=str(proj_id), plan=plan)
        result = await run_action(db, branch, ctx, payload.action, payload.params)
        if not result.get("error"):
            await db.commit()
        else:
            await db.rollback()
        status = 200 if not result.get("error") else _status_for(result["error_type"])
        return JSONResponse(result, status_code=status)


def _status_for(error_type: str) -> int:
    return {
        "validation_failed": 422,
        "conflict": 409,
        "not_found": 404,
        "permission_denied": 403,
        "missing_param": 400,
        "unknown_action": 400,
    }.get(error_type, 400)
