"""Implement hub — HTTP API + page.

Surfaces the implementation coverage read model (plan vs live GTM), lets a
user stage a GTM deploy proposal for a planned event (a pending FluxDraft that
reuses the Ask approve/reject pipeline), lists pending drafts, and re-runs
live-vs-plan drift on demand.

Auth model (mirrors tracking_plan_routes / auditing_routes):
  - page + reads: any active project member
  - deploy-proposal: effective ``tagmanager:write`` permission (403 otherwise)
  - refresh-drift: project membership (same as the tracking-plan action)
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import desc, select

import app.app_state as app_state
from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx
from app.api.project_routes import ensure_active_project, set_active_project_cookie
from app.models.flux_draft import FluxDraft
from app.models.project import ProjectMember
from app.services.implementation import build_coverage, build_deploy_proposal
from app.services.implementation.generate import NoGTMConnectionError
from app.templating import render

logger = logging.getLogger(__name__)

router = APIRouter()


class DeployProposalPayload(BaseModel):
    event_id: str


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
                    ProjectMember.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(status_code=403, detail="Not a project member")
    return user_uuid, project_uuid, member.role


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@router.get("/implement")
async def implement_page(request: Request):
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/implement", status_code=302)
    pid_str = await ensure_active_project(request, user_ctx.user_id)
    if not pid_str:
        return RedirectResponse("/projects", status_code=302)
    user_view = await _load_user_view(user_ctx)
    response = render(
        request,
        "implement/index.html",
        {"user": user_view, "active": "implement", "project_id": pid_str},
    )
    set_active_project_cookie(response, pid_str)
    return response


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@router.get("/api/implement/coverage")
async def api_coverage(request: Request):
    _user_uuid, project_uuid, _role = await _resolve(request)
    async with app_state.db_session_factory() as db:
        data = await build_coverage(db, project_uuid)
    return JSONResponse(data)


@router.post("/api/implement/deploy-proposal")
async def api_deploy_proposal(payload: DeployProposalPayload, request: Request):
    user_uuid, project_uuid, _role = await _resolve(request)

    # Staging a GTM change is a write — gate on effective tagmanager:write.
    from app.auth.permissions import resolve_effective_permissions

    eff = await resolve_effective_permissions(str(user_uuid), str(project_uuid))
    if not eff.allows_tool("tagmanager_write"):
        return JSONResponse(
            {
                "error": True,
                "message": "You don't have permission to propose GTM changes in this project.",
            },
            status_code=403,
        )

    async with app_state.db_session_factory() as db:
        try:
            draft = await build_deploy_proposal(db, project_uuid, payload.event_id, user_uuid)
        except NoGTMConnectionError as exc:
            return JSONResponse({"error": True, "message": str(exc)}, status_code=400)
        except ValueError as exc:
            return JSONResponse({"error": True, "message": str(exc)}, status_code=404)

    from app.ask.drafts import draft_to_stream_payload

    return JSONResponse({"ok": True, "draft": draft_to_stream_payload(draft)})


@router.get("/api/implement/drafts")
async def api_drafts(request: Request):
    user_uuid, project_uuid, _role = await _resolve(request)
    async with app_state.db_session_factory() as db:
        rows = (
            (
                await db.execute(
                    # Owner-scoped to match the approve/reject endpoints, which
                    # route through _owned_draft (conversation-owner only) and
                    # 404 for a teammate's draft. Listing only the caller's own
                    # pending drafts keeps the panel and its actions consistent.
                    select(FluxDraft)
                    .where(
                        FluxDraft.project_id == project_uuid,
                        FluxDraft.status == "pending",
                        FluxDraft.created_by == user_uuid,
                    )
                    .order_by(desc(FluxDraft.created_at))
                )
            )
            .scalars()
            .all()
        )
    return JSONResponse(
        {
            "drafts": [
                {
                    "id": str(d.id),
                    "title": d.title,
                    "kind": d.kind,
                    "status": d.status,
                    "payload": d.payload,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                    "conversation_url": f"/ask?conversation={d.conversation_id}",
                }
                for d in rows
            ]
        }
    )


@router.post("/api/implement/refresh-drift")
async def api_refresh_drift(request: Request):
    # Same gate as the tracking-plan refresh_drift action: project membership.
    _user_uuid, project_uuid, _role = await _resolve(request)
    from app.services.tracking_plan.drift import compute_drift

    async with app_state.db_session_factory() as db:
        summary = await compute_drift(db, project_uuid)
        await db.commit()
    return JSONResponse({"ok": True, "drift": summary})
