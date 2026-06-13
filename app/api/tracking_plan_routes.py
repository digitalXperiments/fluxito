"""HTTP API + page for the structured tracking plan.

Writes reuse the tested run_action core (app/tools/tracking_plan_tools.py); the
route layer only resolves auth + the active project/branch and commits."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import desc, select

import app.app_state as app_state
from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx
from app.api.project_routes import ensure_active_project, set_active_project_cookie
from app.models.project import ProjectMember
from app.models.user import User
from app.models.tracking_plan import TPVersion
from app.services.tracking_plan import (
    activity_to_dict,
    comment_to_dict,
    get_or_create_plan,
    list_activity,
    list_comments,
    plan_to_dict,
    plan_to_markdown,
    plan_to_xlsx,
    validate_plan,
)
from app.services.tracking_plan import branches as _branches
from app.services.tracking_plan.exceptions import NotFoundError, ValidationError
from app.templating import render
from app.tools.tracking_plan_tools import _Ctx, _serialize_branch, resolve_branch, run_action

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
async def api_get_plan(
    project_id: str,
    request: Request,
    branch: str | None = Query(default=None, description="Branch id or name (default: main)"),
):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        target = await resolve_branch(db, plan, branch)
        data = await plan_to_dict(db, plan, target)
        await db.commit()  # persist auto-created plan/branch
        return JSONResponse(data)


@router.get("/api/projects/{project_id}/tracking-plan/validate")
async def api_validate(
    project_id: str,
    request: Request,
    branch: str | None = Query(default=None, description="Branch id or name (default: main)"),
):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        target = await resolve_branch(db, plan, branch)
        report = await validate_plan(db, plan, target)
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
# Exports
# ----------------------------------------------------------------------------
@router.get("/api/projects/{project_id}/tracking-plan/export.md")
async def api_export_md(
    project_id: str,
    request: Request,
    branch: str | None = Query(default=None, description="Branch id or name (default: main)"),
):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        target = await resolve_branch(db, plan, branch)
        data = await plan_to_dict(db, plan, target)
        await db.commit()
        return PlainTextResponse(plan_to_markdown(data))


@router.get("/api/projects/{project_id}/tracking-plan/export.xlsx")
async def api_export_xlsx(
    project_id: str,
    request: Request,
    branch: str | None = Query(default=None, description="Branch id or name (default: main)"),
):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        target = await resolve_branch(db, plan, branch)
        data = await plan_to_dict(db, plan, target)
        await db.commit()
        return Response(
            plan_to_xlsx(data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="tracking-plan.xlsx"'},
        )


# ----------------------------------------------------------------------------
# Branch convenience reads
# ----------------------------------------------------------------------------
@router.get("/api/projects/{project_id}/tracking-plan/branches")
async def api_list_branches(project_id: str, request: Request):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        bs = await _branches.list_branches(db, plan)
        await db.commit()
        return JSONResponse({"branches": [_serialize_branch(b) for b in bs]})


@router.get("/api/projects/{project_id}/tracking-plan/diff")
async def api_diff(
    project_id: str,
    request: Request,
    head: str = Query(..., description="Head branch id or name"),
    base: str | None = Query(default=None, description="Base branch id or name (default: main)"),
):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        try:
            base_branch = await resolve_branch(db, plan, base)
            head_branch = await _branches.get_branch(db, plan, head)
            diff = await _branches.diff_branches(db, plan, base_branch, head_branch)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="Branch not found")
        except ValidationError:
            raise HTTPException(status_code=422, detail="Invalid branch reference")
        await db.commit()
        return JSONResponse(diff)


# ----------------------------------------------------------------------------
# Comments read
# ----------------------------------------------------------------------------
@router.get("/api/projects/{project_id}/tracking-plan/comments")
async def api_list_comments(
    project_id: str,
    request: Request,
    branch: str | None = Query(default=None, description="Branch id or name (default: main)"),
    entity_type: str | None = Query(default=None, description="Filter by entity type"),
    entity_id: str | None = Query(default=None, description="Filter by entity UUID"),
):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        try:
            target = await resolve_branch(db, plan, branch)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="Branch not found")
        except ValidationError:
            raise HTTPException(status_code=422, detail="Invalid branch reference")
        comments = await list_comments(
            db,
            target,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        await db.commit()
        return JSONResponse({"comments": [comment_to_dict(c) for c in comments]})


# ----------------------------------------------------------------------------
# Activity read (per-entity feed + branch timeline)
# ----------------------------------------------------------------------------
@router.get("/api/projects/{project_id}/tracking-plan/activity")
async def api_list_activity(
    project_id: str,
    request: Request,
    branch: str | None = Query(default=None, description="Branch id or name (default: main)"),
    entity_type: str | None = Query(default=None, description="Filter by entity type"),
    entity_id: str | None = Query(default=None, description="Filter by entity UUID"),
):
    user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        try:
            target = await resolve_branch(db, plan, branch)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="Branch not found")
        except ValidationError:
            raise HTTPException(status_code=422, detail="Invalid branch reference")
        rows = await list_activity(db, target, entity_type=entity_type, entity_id=entity_id)
        return JSONResponse({"activity": [activity_to_dict(a) for a in rows]})


# ----------------------------------------------------------------------------
# Project members (for @mention autocomplete)
# ----------------------------------------------------------------------------
@router.get("/api/projects/{project_id}/members")
async def api_list_members(project_id: str, request: Request):
    _user_uuid, proj_id, _role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    async with app_state.db_session_factory() as db:
        rows = (
            await db.execute(
                select(ProjectMember, User)
                .join(User, User.id == ProjectMember.user_id)
                .where(ProjectMember.project_id == proj_id, ProjectMember.is_active.is_(True))
            )
        ).all()
        members = []
        for _m, u in rows:
            label = (getattr(u, "display_name", None) or u.email or "").strip()
            members.append(
                {
                    "id": str(u.id),
                    "display_name": getattr(u, "display_name", None) or u.email,
                    "initials": (label[:2] or "?").upper(),
                }
            )
        return JSONResponse({"members": members})


# ----------------------------------------------------------------------------
# Writes — single action endpoint reusing run_action
# ----------------------------------------------------------------------------
@router.post("/api/projects/{project_id}/tracking-plan/action")
async def api_action(project_id: str, payload: ActionPayload, request: Request):
    user_uuid, proj_id, role = await _resolve(request)
    _check_param_pid(project_id, proj_id)
    # Pop branch selector from params before resolving; run_action receives the
    # already-resolved branch object, not the raw ref string.
    params = dict(payload.params)
    branch_ref = params.pop("branch", None)
    async with app_state.db_session_factory() as db:
        plan = await get_or_create_plan(db, project_id=proj_id, user_id=user_uuid)
        branch = await resolve_branch(db, plan, branch_ref)
        ctx = _Ctx(role=role, user_id=str(user_uuid), project_id=str(proj_id), plan=plan)
        result = await run_action(db, branch, ctx, payload.action, params)
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
