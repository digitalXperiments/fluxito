"""
Solution Design Reference (SDR) Routes — Web UI + JSON API.

The SDR is the canonical document describing what events should fire, when,
with what parameters, to which destinations, and why.  One SDR per project
(MVP).  Markdown is the source of truth.

Routes
------
HTML pages (signed uid cookie auth):
  GET  /solution-design                 — SDR home (viewer or "get started")
  GET  /solution-design/edit             — Markdown editor
  GET  /solution-design/versions         — Version history
  GET  /solution-design/versions/{ver}   — View specific approved version
  GET  /solution-design/diff             — Diff between two versions

JSON API (same auth):
  GET    /api/projects/{project_id}/solution-design            — Fetch SDR (metadata + markdown)
  PUT    /api/projects/{project_id}/solution-design            — Update draft markdown
  POST   /api/projects/{project_id}/solution-design/finalize   — Approve current draft → new version
  GET    /api/projects/{project_id}/solution-design/versions    — List approved versions
  GET    /api/projects/{project_id}/solution-design/versions/{version_id} — Fetch a specific version snapshot
  GET    /api/projects/{project_id}/solution-design/gaps        — Compute current gaps/TODOs
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

import app.app_state as app_state
from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx
from app.api.project_routes import (
    ensure_active_project,
    set_active_project_cookie,
)
from app.models.project import ROLE_ADMIN, ROLE_OWNER, ProjectMember
from app.models.sdr import SDR, SDREvent, SDRVersion
from app.templating import render
from app.tools.sdr_parser import (
    compute_gaps,
    parse_markdown_table,
    parse_sdr_markdown,
    rebuild_projections_async,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth helpers (reuse knowledge_routes pattern)
# ---------------------------------------------------------------------------


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


async def _require_project_admin(user_uuid: uuid.UUID, project_id: uuid.UUID) -> str:
    """
    Verify the user is a project admin (owner or admin role).
    Returns the role string or raises 403.
    """
    async with app_state.db_session_factory() as db:
        result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_uuid,
                ProjectMember.is_active == True,
            )
        )
        member = result.scalar_one_or_none()
        if not member or member.role not in (ROLE_OWNER, ROLE_ADMIN):
            raise HTTPException(
                status_code=403,
                detail="Only project admins can approve SDR versions.",
            )
        return member.role


# ---------------------------------------------------------------------------
# Pydantic payloads
# ---------------------------------------------------------------------------


class SDRUpdatePayload(BaseModel):
    markdown_content: str = Field(..., min_length=1)


class SDRFinalizePayload(BaseModel):
    changelog_note: str = Field("", max_length=2000)


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------


@router.get("/solution-design")
async def sdr_home_page(request: Request):
    """SDR home — viewer if SDR exists, otherwise "get started" empty state."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/solution-design", status_code=302)
    user_view = await _load_user_view(user_ctx)

    project_id_str = await ensure_active_project(request, user_ctx.user_id)
    if not project_id_str:
        return RedirectResponse("/projects", status_code=302)
    project_id = uuid.UUID(project_id_str)

    # Load SDR with events for the viewer
    sdr_data = None
    version_info = None
    gaps = []
    parsed_meta = {}
    has_source_xlsx = False
    source_xlsx_at = None
    async with app_state.db_session_factory() as db:
        stmt = (
            select(SDR)
            .options(
                selectinload(SDR.events).selectinload(SDREvent.parameters),
                selectinload(SDR.events).selectinload(SDREvent.destinations),
                selectinload(SDR.refinement_state),
            )
            .where(SDR.project_id == project_id)
        )
        result = await db.execute(stmt)
        sdr = result.scalar_one_or_none()
        if sdr:
            sdr_data = sdr.to_dict(include_markdown=True)
            sdr_data["events"] = [e.to_dict() for e in (sdr.events or [])]
            sdr_data["refinement_state"] = sdr.refinement_state.to_dict() if sdr.refinement_state else None

            has_source_xlsx = bool(getattr(sdr, "source_xlsx", None))
            source_xlsx_at = sdr.source_xlsx_at.isoformat() if getattr(sdr, "source_xlsx_at", None) else None

            # Current approved version info
            if sdr.current_version_id:
                ver_result = await db.execute(
                    select(SDRVersion).where(SDRVersion.id == sdr.current_version_id)
                )
                ver = ver_result.scalar_one_or_none()
                if ver:
                    version_info = ver.to_dict()

            # Compute gaps + parsed metadata for spreadsheet view
            try:
                parsed = parse_sdr_markdown(sdr.markdown_content)
                gaps = compute_gaps(parsed)
                parsed_meta = {
                    "business_context": parsed.business_context,
                    "user_journeys": parsed.user_journeys,
                    "data_layer_schema": parsed.data_layer_schema,
                    "user_properties": parsed.user_properties,
                    "user_properties_table": parse_markdown_table(parsed.user_properties),
                    "destinations_matrix": parsed.destinations_matrix,
                    "consent_and_privacy": parsed.consent_and_privacy,
                    "consent_table": parse_markdown_table(parsed.consent_and_privacy),
                    "executive_summary": parsed.executive_summary,
                    "executive_summary_table": parse_markdown_table(parsed.executive_summary),
                    "gap_register_table": parse_markdown_table(parsed.gap_register),
                    "conversion_audit_table": parse_markdown_table(parsed.conversion_audit),
                    "remediation_roadmap_table": parse_markdown_table(parsed.remediation_roadmap),
                    "ownership": parsed.ownership,
                    "business_type": parsed.business_type,
                    "sdr_status": parsed.sdr_status,
                    "sdr_version": parsed.sdr_version,
                }
            except Exception:
                logger.debug("Failed to compute SDR gaps for viewer", exc_info=True)

    from app.utils import base_url_from_request

    response = render(
        request,
        "sdr_home.html",
        {
            "user": user_view,
            "sdr": sdr_data,
            "version_info": version_info,
            "gaps": gaps,
            "parsed_meta": parsed_meta,
            "has_source_xlsx": has_source_xlsx,
            "source_xlsx_at": source_xlsx_at,
            "active": "sdr",
            "base_url": base_url_from_request(request),
        },
    )
    set_active_project_cookie(response, project_id_str)
    return response


@router.get("/solution-design/edit")
async def sdr_edit_page(request: Request):
    """Markdown editor for the SDR draft."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/solution-design/edit", status_code=302)
    user_view = await _load_user_view(user_ctx)

    project_id_str = await ensure_active_project(request, user_ctx.user_id)
    if not project_id_str:
        return RedirectResponse("/projects", status_code=302)
    project_id = uuid.UUID(project_id_str)

    sdr_data = None
    async with app_state.db_session_factory() as db:
        result = await db.execute(select(SDR).where(SDR.project_id == project_id))
        sdr = result.scalar_one_or_none()
        if sdr:
            sdr_data = sdr.to_dict(include_markdown=True)

    if not sdr_data:
        return RedirectResponse("/solution-design", status_code=302)

    response = render(
        request,
        "sdr_edit.html",
        {
            "user": user_view,
            "sdr": sdr_data,
            "active": "sdr",
        },
    )
    set_active_project_cookie(response, project_id_str)
    return response


@router.get("/solution-design/versions")
async def sdr_versions_page(request: Request):
    """Version history timeline."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/solution-design/versions", status_code=302)
    user_view = await _load_user_view(user_ctx)

    project_id_str = await ensure_active_project(request, user_ctx.user_id)
    if not project_id_str:
        return RedirectResponse("/projects", status_code=302)
    project_id = uuid.UUID(project_id_str)

    sdr_data = None
    versions = []
    async with app_state.db_session_factory() as db:
        result = await db.execute(select(SDR).where(SDR.project_id == project_id))
        sdr = result.scalar_one_or_none()
        if sdr:
            sdr_data = sdr.to_dict()
            ver_result = await db.execute(
                select(SDRVersion).where(SDRVersion.sdr_id == sdr.id).order_by(SDRVersion.approved_at.desc())
            )
            versions = [v.to_dict() for v in ver_result.scalars().all()]

    if not sdr_data:
        return RedirectResponse("/solution-design", status_code=302)

    response = render(
        request,
        "sdr_versions.html",
        {
            "user": user_view,
            "sdr": sdr_data,
            "versions": versions,
            "active": "sdr",
        },
    )
    set_active_project_cookie(response, project_id_str)
    return response


@router.get("/solution-design/versions/{version_id}")
async def sdr_version_detail_page(version_id: str, request: Request):
    """View a specific approved version (read-only)."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/solution-design/versions", status_code=302)
    user_view = await _load_user_view(user_ctx)

    project_id_str = await ensure_active_project(request, user_ctx.user_id)
    if not project_id_str:
        return RedirectResponse("/projects", status_code=302)
    project_id = uuid.UUID(project_id_str)

    try:
        ver_uuid = uuid.UUID(version_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid version id")

    version_data = None
    sdr_data = None
    async with app_state.db_session_factory() as db:
        # Verify SDR belongs to this project
        result = await db.execute(select(SDR).where(SDR.project_id == project_id))
        sdr = result.scalar_one_or_none()
        if not sdr:
            return RedirectResponse("/solution-design", status_code=302)
        sdr_data = sdr.to_dict()

        ver_result = await db.execute(
            select(SDRVersion).where(
                SDRVersion.id == ver_uuid,
                SDRVersion.sdr_id == sdr.id,
            )
        )
        ver = ver_result.scalar_one_or_none()
        if not ver:
            raise HTTPException(status_code=404, detail="Version not found")
        version_data = ver.to_dict()
        version_data["markdown_snapshot"] = ver.markdown_snapshot

    response = render(
        request,
        "sdr_version_detail.html",
        {
            "user": user_view,
            "sdr": sdr_data,
            "version": version_data,
            "active": "sdr",
        },
    )
    set_active_project_cookie(response, project_id_str)
    return response


@router.get("/solution-design/diff")
async def sdr_diff_page(request: Request):
    """Diff view between two versions (query params: from, to)."""
    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/solution-design/versions", status_code=302)
    user_view = await _load_user_view(user_ctx)

    project_id_str = await ensure_active_project(request, user_ctx.user_id)
    if not project_id_str:
        return RedirectResponse("/projects", status_code=302)
    project_id = uuid.UUID(project_id_str)

    from_id = request.query_params.get("from")
    to_id = request.query_params.get("to")
    if not from_id or not to_id:
        raise HTTPException(status_code=400, detail="Both 'from' and 'to' query params required")

    try:
        from_uuid = uuid.UUID(from_id)
        to_uuid = uuid.UUID(to_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid version id")

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(SDR).where(SDR.project_id == project_id))
        sdr = result.scalar_one_or_none()
        if not sdr:
            return RedirectResponse("/solution-design", status_code=302)

        from_result = await db.execute(
            select(SDRVersion).where(SDRVersion.id == from_uuid, SDRVersion.sdr_id == sdr.id)
        )
        to_result = await db.execute(
            select(SDRVersion).where(SDRVersion.id == to_uuid, SDRVersion.sdr_id == sdr.id)
        )
        from_ver = from_result.scalar_one_or_none()
        to_ver = to_result.scalar_one_or_none()
        if not from_ver or not to_ver:
            raise HTTPException(status_code=404, detail="One or both versions not found")

        from_data = from_ver.to_dict()
        from_data["markdown_snapshot"] = from_ver.markdown_snapshot
        to_data = to_ver.to_dict()
        to_data["markdown_snapshot"] = to_ver.markdown_snapshot

    response = render(
        request,
        "sdr_diff.html",
        {
            "user": user_view,
            "sdr": sdr.to_dict(),
            "from_version": from_data,
            "to_version": to_data,
            "active": "sdr",
        },
    )
    set_active_project_cookie(response, project_id_str)
    return response


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@router.get("/api/projects/{project_id}/solution-design")
async def api_get_sdr(project_id: str, request: Request):
    """Fetch the SDR for a project (metadata + markdown + parsed events)."""
    _, _, proj_id = await _require_user_and_project(request)

    # Validate project_id param matches active project
    try:
        param_pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if param_pid != proj_id:
        raise HTTPException(status_code=403, detail="Project mismatch")

    async with app_state.db_session_factory() as db:
        stmt = (
            select(SDR)
            .options(
                selectinload(SDR.events).selectinload(SDREvent.parameters),
                selectinload(SDR.events).selectinload(SDREvent.destinations),
                selectinload(SDR.refinement_state),
            )
            .where(SDR.project_id == proj_id)
        )
        result = await db.execute(stmt)
        sdr = result.scalar_one_or_none()
        if not sdr:
            return JSONResponse({"sdr": None})

        data = sdr.to_full_dict()
        data["refinement_state"] = sdr.refinement_state.to_dict() if sdr.refinement_state else None

        # Include current version info
        if sdr.current_version_id:
            ver_result = await db.execute(select(SDRVersion).where(SDRVersion.id == sdr.current_version_id))
            ver = ver_result.scalar_one_or_none()
            data["current_version"] = ver.to_dict() if ver else None
        else:
            data["current_version"] = None

        return JSONResponse({"sdr": data})


@router.put("/api/projects/{project_id}/solution-design")
async def api_update_sdr(project_id: str, payload: SDRUpdatePayload, request: Request):
    """Update the SDR draft markdown and rebuild projections."""
    _, user_uuid, proj_id = await _require_user_and_project(request)

    try:
        param_pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if param_pid != proj_id:
        raise HTTPException(status_code=403, detail="Project mismatch")

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(SDR).where(SDR.project_id == proj_id))
        sdr = result.scalar_one_or_none()
        if not sdr:
            raise HTTPException(status_code=404, detail="No SDR exists for this project")

        sdr.markdown_content = payload.markdown_content
        sdr.updated_at = datetime.now(UTC)

        # Rebuild structured projections from the new markdown
        try:
            await rebuild_projections_async(db, sdr)
            sdr.parsed_at = datetime.now(UTC)
        except Exception:
            logger.warning("Projection rebuild failed during SDR update", exc_info=True)

        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Failed to update SDR")
            raise HTTPException(status_code=500, detail="Failed to update SDR")

        return JSONResponse({"ok": True, "updated_at": sdr.updated_at.isoformat()})


@router.post("/api/projects/{project_id}/solution-design/finalize")
async def api_finalize_sdr(project_id: str, payload: SDRFinalizePayload, request: Request):
    """
    Approve the current draft as a new version.

    Requires project admin role. Creates an immutable SDRVersion snapshot,
    updates the SDR's current_version_id, and optionally bumps to a major
    version if changelog contains ``[major]``.
    """
    _, user_uuid, proj_id = await _require_user_and_project(request)

    try:
        param_pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if param_pid != proj_id:
        raise HTTPException(status_code=403, detail="Project mismatch")

    # Admin check
    await _require_project_admin(user_uuid, proj_id)

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(SDR).where(SDR.project_id == proj_id))
        sdr = result.scalar_one_or_none()
        if not sdr:
            raise HTTPException(status_code=404, detail="No SDR exists for this project")

        # Determine version number
        ver_result = await db.execute(
            select(SDRVersion)
            .where(SDRVersion.sdr_id == sdr.id)
            .order_by(SDRVersion.approved_at.desc())
            .limit(1)
        )
        latest_ver = ver_result.scalar_one_or_none()

        if latest_ver:
            version_number = _increment_version(latest_ver.version_number, payload.changelog_note)
        else:
            version_number = "1.0"

        # Changelog required for versions > 1.0
        changelog = payload.changelog_note.strip() or None
        if latest_ver and not changelog:
            changelog = f"Version {version_number}"

        if not latest_ver and not changelog:
            changelog = "Initial SDR"

        # Validate: must have at least business_context or one event
        try:
            parsed = parse_sdr_markdown(sdr.markdown_content)
            if not parsed.events:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot finalize: SDR has no events. Add at least one event before approving.",
                )
        except HTTPException:
            raise
        except Exception:
            logger.warning("SDR parse failed during finalize validation", exc_info=True)

        # Create version snapshot
        new_version = SDRVersion(
            sdr_id=sdr.id,
            version_number=version_number,
            markdown_snapshot=sdr.markdown_content,
            changelog=changelog,
            approved_by=user_uuid,
        )
        db.add(new_version)
        await db.flush()

        # Update SDR to point to new version
        sdr.current_version_id = new_version.id
        sdr.status = "approved"

        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Failed to finalize SDR")
            raise HTTPException(status_code=500, detail="Failed to finalize SDR")

        return JSONResponse(
            {
                "ok": True,
                "version": new_version.to_dict(),
                "message": f"SDR v{version_number} approved. Audits will now validate against this version.",
            }
        )


@router.get("/api/projects/{project_id}/solution-design/versions")
async def api_list_versions(project_id: str, request: Request):
    """List all approved versions for the project's SDR."""
    _, _, proj_id = await _require_user_and_project(request)

    try:
        param_pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if param_pid != proj_id:
        raise HTTPException(status_code=403, detail="Project mismatch")

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(SDR).where(SDR.project_id == proj_id))
        sdr = result.scalar_one_or_none()
        if not sdr:
            return JSONResponse({"versions": []})

        ver_result = await db.execute(
            select(SDRVersion).where(SDRVersion.sdr_id == sdr.id).order_by(SDRVersion.approved_at.desc())
        )
        versions = [v.to_dict() for v in ver_result.scalars().all()]

        return JSONResponse(
            {
                "versions": versions,
                "current_version_id": str(sdr.current_version_id) if sdr.current_version_id else None,
            }
        )


@router.get("/api/projects/{project_id}/solution-design/versions/{version_id}")
async def api_get_version(project_id: str, version_id: str, request: Request):
    """Fetch a specific version snapshot (includes markdown)."""
    _, _, proj_id = await _require_user_and_project(request)

    try:
        param_pid = uuid.UUID(project_id)
        ver_uuid = uuid.UUID(version_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid id")
    if param_pid != proj_id:
        raise HTTPException(status_code=403, detail="Project mismatch")

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(SDR).where(SDR.project_id == proj_id))
        sdr = result.scalar_one_or_none()
        if not sdr:
            raise HTTPException(status_code=404, detail="No SDR exists")

        ver_result = await db.execute(
            select(SDRVersion).where(
                SDRVersion.id == ver_uuid,
                SDRVersion.sdr_id == sdr.id,
            )
        )
        ver = ver_result.scalar_one_or_none()
        if not ver:
            raise HTTPException(status_code=404, detail="Version not found")

        data = ver.to_dict()
        data["markdown_snapshot"] = ver.markdown_snapshot
        return JSONResponse({"version": data})


@router.get("/api/projects/{project_id}/solution-design/gaps")
async def api_get_gaps(project_id: str, request: Request):
    """Compute current [TODO] gaps from the SDR draft markdown."""
    _, _, proj_id = await _require_user_and_project(request)

    try:
        param_pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if param_pid != proj_id:
        raise HTTPException(status_code=403, detail="Project mismatch")

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(SDR).where(SDR.project_id == proj_id))
        sdr = result.scalar_one_or_none()
        if not sdr:
            return JSONResponse({"gaps": [], "total": 0})

        try:
            parsed = parse_sdr_markdown(sdr.markdown_content)
            gaps = compute_gaps(parsed)
        except Exception:
            logger.warning("Failed to compute gaps", exc_info=True)
            gaps = []

        return JSONResponse(
            {
                "gaps": gaps,
                "total": len(gaps),
                "event_count": len(parsed.events) if parsed else 0,
            }
        )


# ---------------------------------------------------------------------------
# Excel Export
# ---------------------------------------------------------------------------


@router.get("/api/projects/{project_id}/solution-design/export.xlsx")
async def api_export_sdr_xlsx(project_id: str, request: Request):
    """Export the SDR as a multi-sheet Excel spreadsheet (.xlsx)."""
    _, _, proj_id = await _require_user_and_project(request)

    try:
        param_pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if param_pid != proj_id:
        raise HTTPException(status_code=403, detail="Project mismatch")

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(SDR).where(SDR.project_id == proj_id))
        sdr = result.scalar_one_or_none()
        if not sdr:
            raise HTTPException(status_code=404, detail="No SDR exists for this project")

        from app.tools.sdr_excel_export import generate_sdr_xlsx

        try:
            xlsx_bytes = generate_sdr_xlsx(sdr.markdown_content, sdr.name)
        except Exception:
            logger.exception("Failed to generate SDR Excel export")
            raise HTTPException(status_code=500, detail="Failed to generate Excel export")

        # Build filename
        safe_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "" for c in sdr.name)
        safe_name = safe_name.strip().replace(" ", "_") or "SDR"
        filename = f"{safe_name}_{datetime.now(UTC).strftime('%Y%m%d')}.xlsx"

        return Response(
            content=xlsx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@router.get("/api/projects/{project_id}/solution-design/source.xlsx")
async def api_download_source_xlsx(project_id: str, request: Request):
    """Download the original Claude-generated source .xlsx, if one was stored."""
    _, _, proj_id = await _require_user_and_project(request)

    try:
        param_pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if param_pid != proj_id:
        raise HTTPException(status_code=403, detail="Project mismatch")

    async with app_state.db_session_factory() as db:
        result = await db.execute(select(SDR).where(SDR.project_id == proj_id))
        sdr = result.scalar_one_or_none()
        if not sdr or not sdr.source_xlsx:
            raise HTTPException(status_code=404, detail="No source file stored for this SDR")

        filename = sdr.source_xlsx_filename or f"{sdr.name or 'SDR'}-source.xlsx"
        return Response(
            content=sdr.source_xlsx,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _increment_version(current: str, changelog_note: str = "") -> str:
    """
    Compute next version number.

    - Default: increment minor (1.0 → 1.1, 1.5 → 1.6)
    - If changelog contains ``[major]``: increment major (1.5 → 2.0)
    """
    try:
        parts = current.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return "1.0"

    if "[major]" in (changelog_note or "").lower():
        return f"{major + 1}.0"
    return f"{major}.{minor + 1}"
