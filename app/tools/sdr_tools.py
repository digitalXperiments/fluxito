"""
SDR MCP Tools — internal generate_sdr + refine_sdr handlers

Standalone internal handlers for creating and refining Solution Design References.
These are Layer 2 (Intelligence) handlers dispatched by the ``tracking_plan``
unified tool (via ``app/tools/unified.py``), operating on SDR markdown documents
and using existing connectors for bootstrap data.

Architecture:
  - generate_sdr: Capture intake and gather structured source data for synthesis
    (dispatched via tracking_plan(action="generate", params={...}))
  - save_sdr: Persist Claude-authored markdown after synthesis
    (dispatched via tracking_plan(action="save", params={...}))
  - refresh_sdr_sources: Re-scan connectors and return structured deltas
    (dispatched via tracking_plan(action="refresh_sources", params={...}))
  - refine_sdr: Section-by-section conversational refinement state machine
    (dispatched via tracking_plan(action="refine", params={...}))
"""

from __future__ import annotations

import logging
import re
import uuid as _uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

import app.app_state as state
from app.auth.mcp_session_manager import (
    no_active_project_response,
    require_project_ctx,
)
from app.models.sdr import (
    SDR,
    SDRIntake,
    SDRRefinementState,
    SDRVersion,
)
from app.tools.sdr_bootstrap.intake import (
    INTAKE_VERSION,
    build_intake_snapshot,
    get_intake_questions,
    intake_interview_instructions,
    missing_required_answers,
)
from app.tools.sdr_bootstrap.registry import (
    SUPPORTED_SOURCE_NAMES,
    compute_source_fingerprint,
    connected_but_unsupported,
    connected_sources_summary,
    get_available_sources,
    merge_scan_events,
    reproducibility_info,
    scan_sources,
    scan_summary,
    scans_to_dict,
)
from app.tools.sdr_parser import (
    ParsedEvent,
    ParsedSDR,
    compute_gaps,
    generate_sdr_markdown,
    parse_sdr_markdown,
    rebuild_projections_async,
)
from app.tools.sdr_templates import get_industry_template

logger = logging.getLogger(__name__)

# Admin roles that can approve/finalize SDR versions
_ADMIN_ROLES = frozenset(("owner", "admin"))


# Refinement section order
SECTION_ORDER = [
    "business_context",
    "user_journeys",
    "data_layer_schema",
    "event_catalog",
    "user_properties",
    "destinations_matrix",
    "consent_and_privacy",
    "ownership",
    "review_and_finalize",
]

# Sections that must be complete before finalization
REQUIRED_FOR_FINALIZE = {"business_context", "event_catalog", "destinations_matrix"}


def register_sdr_tools(mcp_server: Any) -> None:
    """Register SDR MCP tools."""

    # ==================================================================
    # generate_sdr
    # ==================================================================

    @mcp_server.tool("generate_sdr")
    async def generate_sdr(
        project_id: str | None = None,
        name: str | None = None,
        sources_ga4: bool = True,
        sources_gtm: bool = True,
        sources_ads: bool = True,
        business_type_hint: str | None = None,
        intake_answers: dict[str, str] | None = None,
        sources: list[str] | None = None,
        phase: str = "auto",
        regenerate: bool = False,
    ) -> dict:
        """
        Gather source data for a Solution Design Reference (SDR).

        In v2 this tool is the data gatherer, not the markdown writer. Without
        intake answers it returns the fixed intake questions. With intake
        answers it scans supported connected sources and returns structured
        facts, template guidance, reproducibility metadata, and synthesis
        instructions. Persist Claude-authored markdown with save_sdr.

        Args:
            project_id: Target project (defaults to active project).
            name: SDR name (defaults to "{project_name} SDR").
            sources_ga4: Include GA4 events/dimensions in bootstrap.
            sources_gtm: Include GTM tags/triggers in bootstrap.
            sources_ads: Include Google Ads conversions in bootstrap.
            business_type_hint: Industry type for template overlay.
                Options: ecommerce, saas, lead_gen, media, app, marketplace.
            regenerate: If True, overwrite existing draft. If False, return
                existing SDR if one exists.

        Returns interview questions or a structured data-gathering result.
        """
        return await _generate_sdr_v2(
            project_id=project_id,
            name=name,
            sources_ga4=sources_ga4,
            sources_gtm=sources_gtm,
            sources_ads=sources_ads,
            business_type_hint=business_type_hint,
            intake_answers=intake_answers,
            sources=sources,
            phase=phase,
            regenerate=regenerate,
        )

    @mcp_server.tool("capture_sdr_intake")
    async def capture_sdr_intake(
        sdr_id: str | None = None,
        intake_answers: dict[str, str] | None = None,
    ) -> dict:
        """Validate and optionally persist SDR intake answers."""
        return await _capture_sdr_intake(sdr_id=sdr_id, intake_answers=intake_answers)

    @mcp_server.tool("save_sdr")
    async def save_sdr(
        markdown: str,
        name: str | None = None,
        sdr_id: str | None = None,
        intake_snapshot: dict | None = None,
        source_snapshot: dict | None = None,
        source_snapshot_id: str | None = None,
        create_initial_refinement_state: bool = True,
    ) -> dict:
        """Persist a Claude-authored SDR markdown draft.

        Pass the `intake` and `scans` objects returned by generate_sdr as
        `intake_snapshot` and `source_snapshot` for full reproducibility.
        """
        return await _save_sdr_v2(
            markdown=markdown,
            name=name,
            sdr_id=sdr_id,
            intake_snapshot=intake_snapshot,
            source_snapshot=source_snapshot,
            source_snapshot_id=source_snapshot_id,
            create_initial_refinement_state=create_initial_refinement_state,
        )

    @mcp_server.tool("get_sdr_intake")
    async def get_sdr_intake(sdr_id: str) -> dict:
        """Re-surface the persisted intake answers used to synthesize an SDR."""
        return await _get_sdr_intake(sdr_id=sdr_id)

    @mcp_server.tool("list_sdr_sources")
    async def list_sdr_sources(sdr_id: str | None = None) -> dict:
        """List which sources are supported, connected, and were last scanned."""
        return await _list_sdr_sources(sdr_id=sdr_id)

    @mcp_server.tool("refresh_sdr_sources")
    async def refresh_sdr_sources(
        sdr_id: str,
        connector_filter: list[str] | None = None,
        reuse_intake: bool = True,
    ) -> dict:
        """Scan connected SDR sources and return structured deltas without writing."""
        return await _refresh_sdr_sources_v2(
            sdr_id=sdr_id,
            connector_filter=connector_filter,
            reuse_intake=reuse_intake,
        )

    # ==================================================================
    # refine_sdr
    # ==================================================================

    @mcp_server.tool("refine_sdr")
    async def refine_sdr(
        sdr_id: str,
        action: str = "resume",
        section: str | None = None,
        user_input: str | None = None,
        source_delta: dict | None = None,
        changelog_note: str | None = None,
    ) -> dict:
        """
        Conversational refinement of an SDR through a section-by-section
        state machine. Returns instructions that guide Claude's conversation
        with the user. State persists across calls so conversations are
        resumable across sessions.

        Args:
            sdr_id: The SDR to refine.
            action: One of:
                resume          — continue from last section
                goto_section    — jump to specific section
                submit_answer   — user answered a question
                accept_proposed — user approved proposed changes
                reject_proposed — user wants different changes
                skip_section    — skip current section
                show_status     — return state without advancing
                apply_source_delta — turn refresh_sdr_sources output into pending proposed changes
                finalize        — snapshot as new approved version (admin only)
                start_new_draft — begin editing after approval
            section: Target section for goto_section (e.g., "event_catalog.purchase").
            user_input: Natural language user response for submit_answer.
            source_delta: Output payload from refresh_sdr_sources for apply_source_delta.
            changelog_note: Required for finalize on versions >= 1.1.

        Returns instructions_for_claude, progress, proposed changes, and user options.
        """
        # --- Auth ---
        project_ctx = require_project_ctx()
        if not project_ctx:
            return no_active_project_response()

        user_ctx = state.current_user_ctx.get()
        if not user_ctx:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}

        valid_actions = {
            "resume",
            "goto_section",
            "submit_answer",
            "accept_proposed",
            "reject_proposed",
            "skip_section",
            "show_status",
            "apply_source_delta",
            "finalize",
            "start_new_draft",
        }
        if action not in valid_actions:
            return {
                "error": True,
                "error_type": "invalid_action",
                "message": f"Invalid action '{action}'. Valid: {sorted(valid_actions)}",
            }

        # --- Load SDR + refinement state ---
        db_factory = state.db_session_factory
        async with db_factory() as db:
            sdr_result = await db.execute(select(SDR).where(SDR.id == _uuid.UUID(sdr_id)))
            sdr = sdr_result.scalar_one_or_none()
            if not sdr:
                return {"error": True, "error_type": "not_found", "message": f"SDR '{sdr_id}' not found."}

            # Verify project access
            if str(sdr.project_id) != project_ctx.project_id:
                return {
                    "error": True,
                    "error_type": "access_denied",
                    "message": "SDR belongs to a different project.",
                }

            ref_result = await db.execute(
                select(SDRRefinementState).where(SDRRefinementState.sdr_id == sdr.id)
            )
            ref_state = ref_result.scalar_one_or_none()
            if not ref_state:
                ref_state = SDRRefinementState(
                    sdr_id=sdr.id,
                    current_section="business_context",
                    sections_completed=[],
                    last_activity_at=datetime.now(UTC),
                )
                db.add(ref_state)
                await db.flush()

            # --- Dispatch by action ---
            if action == "show_status":
                result = _handle_show_status(sdr, ref_state)

            elif action == "resume":
                result = _handle_resume(sdr, ref_state)

            elif action == "goto_section":
                if not section:
                    return {"error": True, "message": "section parameter required for goto_section."}
                result = _handle_goto_section(sdr, ref_state, section)

            elif action == "submit_answer":
                if not user_input:
                    return {"error": True, "message": "user_input parameter required for submit_answer."}
                result = _handle_submit_answer(sdr, ref_state, user_input)

            elif action == "accept_proposed":
                result = await _handle_accept_proposed(db, sdr, ref_state)

            elif action == "reject_proposed":
                result = _handle_reject_proposed(sdr, ref_state)

            elif action == "skip_section":
                result = _handle_skip_section(sdr, ref_state)

            elif action == "apply_source_delta":
                if not source_delta:
                    return {"error": True, "message": "source_delta parameter required for apply_source_delta."}
                result = _handle_apply_source_delta(sdr, ref_state, source_delta)

            elif action == "finalize":
                if project_ctx.role not in _ADMIN_ROLES:
                    return {
                        "error": True,
                        "error_type": "permission_denied",
                        "message": (
                            "Only project admins (owner/admin) can approve SDR versions. "
                            f"Your role is '{project_ctx.role}'."
                        ),
                        "instructions_for_claude": (
                            "The user tried to finalize the SDR but they are not a project admin. "
                            "Let them know that only owners or admins can approve versions. "
                            "Offer to notify an admin that the SDR is ready for review."
                        ),
                    }
                result = await _handle_finalize(db, sdr, ref_state, user_ctx, changelog_note)

            elif action == "start_new_draft":
                result = await _handle_start_new_draft(db, sdr, ref_state)

            else:
                result = {"error": True, "message": f"Unhandled action: {action}"}

            # Update last_activity_at
            ref_state.last_activity_at = datetime.now(UTC)
            await db.commit()

            return result


# ---------------------------------------------------------------------------
# SDR v2 tool helpers
# ---------------------------------------------------------------------------


async def _generate_sdr_v2(
    *,
    project_id: str | None,
    name: str | None,
    sources_ga4: bool,
    sources_gtm: bool,
    sources_ads: bool,
    business_type_hint: str | None,
    intake_answers: dict[str, str] | None,
    sources: list[str] | None,
    phase: str,
    regenerate: bool,
) -> dict:
    project_ctx = require_project_ctx()
    if not project_ctx:
        return no_active_project_response()

    if not state.current_user_ctx.get():
        return {"error": True, "error_type": "unauthenticated", "message": "No active session."}

    project_name = project_ctx.project_name
    target_project_id = project_id or project_ctx.project_id
    phase = (phase or "auto").lower()

    if not intake_answers and phase != "scan":
        return {
            "status": "awaiting_intake",
            "intake_version": INTAKE_VERSION,
            "questions": get_intake_questions(),
            "message": "Business intake is required before generating a high-fidelity SDR scan.",
            "instructions_for_claude": intake_interview_instructions(project_name),
            "next_action": "tracking_plan(action='generate', params={'intake_answers': {...}})",
        }

    if intake_answers:
        missing = missing_required_answers(intake_answers)
        if missing and phase != "scan":
            return {
                "error": True,
                "error_type": "incomplete_intake",
                "missing_required_keys": missing,
                "questions": get_intake_questions(),
                "instructions_for_claude": (
                    "Ask only for the missing required intake answers, then call generate again with the complete "
                    "intake_answers payload."
                ),
            }

    requested_sources = _normalize_requested_sources(sources, sources_ga4, sources_gtm, sources_ads)
    intake_snapshot = build_intake_snapshot(intake_answers or {})
    scans = await scan_sources(project_ctx, requested_sources)
    scan_events = merge_scan_events(scans)
    business_type = business_type_hint or _infer_business_type_from_intake(intake_snapshot["answers"]) or _infer_business_type(scan_events)
    template_events = get_industry_template(business_type)

    existing_sdr_id: str | None = None
    db_factory = state.db_session_factory
    async with db_factory() as db:
        existing = await db.execute(select(SDR).where(SDR.project_id == _uuid.UUID(target_project_id)))
        existing_sdr = existing.scalar_one_or_none()
        if existing_sdr:
            existing_sdr_id = str(existing_sdr.id)

    industry_template = {
        "business_type": business_type,
        "events": [_event_to_dict(event) for event in template_events],
        "rationale": _template_rationale(business_type, intake_snapshot["answers"], len(scan_events)),
    }

    connected = connected_sources_summary(project_ctx, scans)
    merged_events = _merge_scan_with_template(scan_events, template_events)
    skeleton = _build_markdown_skeleton(
        project_name=project_name,
        project_id=target_project_id,
        business_type=business_type,
        events=merged_events,
        intake_answers=intake_snapshot["answers"],
    )

    return {
        "sdr_id": existing_sdr_id,
        "status": "data_gathered",
        "name": name or f"{project_name} SDR",
        "intake": intake_snapshot,
        "connected_sources": connected,
        "scans": scans_to_dict(scans),
        "industry_template": industry_template,
        "scan_summary": scan_summary(scans),
        "reproducibility": reproducibility_info(project_ctx),
        "regenerate_requested": regenerate,
        # A fully-structured, parse-valid SDR seeded with live events + template
        # gaps + intake context. Claude elevates this in place — it guarantees the
        # saved markdown round-trips through parse_sdr_markdown into projections.
        "markdown_skeleton": skeleton,
        "instructions_for_claude": _build_synthesis_playbook(
            project_name=project_name,
            business_type=business_type,
            sdr_id=existing_sdr_id,
            scanned_event_count=len(scan_events),
            template_event_count=len(template_events),
            connected=connected,
        ),
    }


async def _capture_sdr_intake(
    *,
    sdr_id: str | None,
    intake_answers: dict[str, str] | None,
) -> dict:
    project_ctx = require_project_ctx()
    if not project_ctx:
        return no_active_project_response()

    user_ctx = state.current_user_ctx.get()
    if not user_ctx:
        return {"error": True, "error_type": "unauthenticated", "message": "No active session."}

    missing = missing_required_answers(intake_answers)
    if missing:
        return {
            "error": True,
            "error_type": "incomplete_intake",
            "missing_required_keys": missing,
            "questions": get_intake_questions(),
            "instructions_for_claude": "Ask for the missing intake answers before capture.",
        }

    snapshot = build_intake_snapshot(intake_answers or {})
    if not sdr_id:
        return {
            "status": "captured",
            "intake": snapshot,
            "instructions_for_claude": "Use this intake snapshot in the next generate_sdr or save_sdr call.",
        }

    db_factory = state.db_session_factory
    async with db_factory() as db:
        sdr_result = await db.execute(select(SDR).where(SDR.id == _uuid.UUID(sdr_id)))
        sdr = sdr_result.scalar_one_or_none()
        if not sdr:
            return {"error": True, "error_type": "not_found", "message": f"SDR '{sdr_id}' not found."}
        if str(sdr.project_id) != project_ctx.project_id:
            return {"error": True, "error_type": "access_denied", "message": "SDR belongs to a different project."}
        await _persist_intake(db, sdr, snapshot, _uuid.UUID(str(user_ctx.user_id)))
        await db.commit()

    return {
        "sdr_id": sdr_id,
        "status": "captured",
        "intake": snapshot,
        "instructions_for_claude": "The intake is now persisted and can be reused for refreshes or re-generation.",
    }


async def _save_sdr_v2(
    *,
    markdown: str,
    name: str | None,
    sdr_id: str | None,
    intake_snapshot: dict | None,
    source_snapshot: dict | None = None,
    source_snapshot_id: str | None,
    create_initial_refinement_state: bool,
) -> dict:
    project_ctx = require_project_ctx()
    if not project_ctx:
        return no_active_project_response()

    user_ctx = state.current_user_ctx.get()
    if not user_ctx:
        return {"error": True, "error_type": "unauthenticated", "message": "No active session."}
    if not markdown or not markdown.strip():
        return {"error": True, "error_type": "invalid_markdown", "message": "markdown is required."}

    user_id = _uuid.UUID(str(user_ctx.user_id))
    target_project_id = _uuid.UUID(project_ctx.project_id)
    now = datetime.now(UTC)
    normalized_intake = _normalize_intake_snapshot(intake_snapshot)
    source_fingerprint = (
        _extract_source_fingerprint(intake_snapshot)
        or _extract_source_fingerprint(source_snapshot)
        or compute_source_fingerprint(project_ctx)
    )
    parsed = parse_sdr_markdown(markdown)

    db_factory = state.db_session_factory
    async with db_factory() as db:
        sdr: SDR | None = None
        if sdr_id:
            result = await db.execute(select(SDR).where(SDR.id == _uuid.UUID(sdr_id)))
            sdr = result.scalar_one_or_none()
            if not sdr:
                return {"error": True, "error_type": "not_found", "message": f"SDR '{sdr_id}' not found."}
            if str(sdr.project_id) != project_ctx.project_id:
                return {"error": True, "error_type": "access_denied", "message": "SDR belongs to a different project."}
        else:
            result = await db.execute(select(SDR).where(SDR.project_id == target_project_id))
            sdr = result.scalar_one_or_none()

        if sdr:
            sdr.name = name or sdr.name
            sdr.markdown_content = markdown
            sdr.status = "draft"
            sdr.updated_at = now
            sdr.draft_version = _next_draft_version(sdr.draft_version)
        else:
            sdr = SDR(
                project_id=target_project_id,
                name=name or f"{project_ctx.project_name} SDR",
                status="draft",
                markdown_content=markdown,
                created_by=user_id,
                draft_version="1.0-draft-1",
            )
            db.add(sdr)

        if normalized_intake:
            sdr.intake_answers = normalized_intake["answers"]
            sdr.intake_version = normalized_intake["intake_version"]
        sdr.last_full_source_scan_at = now
        sdr.source_fingerprint = source_fingerprint
        if source_snapshot is not None:
            sdr.last_source_scan = source_snapshot
        await db.flush()

        if normalized_intake:
            await _persist_intake(db, sdr, normalized_intake, user_id)

        await rebuild_projections_async(db, sdr)

        completed_sections: list[str] = []
        if create_initial_refinement_state:
            completed_sections = await _seed_refinement_state(db, sdr, parsed)

        await db.commit()

        gaps = compute_gaps(parsed)
        remaining = [s for s in SECTION_ORDER if s not in completed_sections]
        return {
            "sdr_id": str(sdr.id),
            "status": "draft_saved",
            "draft_version": sdr.draft_version,
            "source_snapshot_id": source_snapshot_id,
            "source_snapshot_saved": source_snapshot is not None,
            "sections_completed": completed_sections,
            "sections_remaining": remaining,
            "summary": {
                "events": len(parsed.events),
                "todo_count": len(parsed.todo_markers),
                "gaps": len(gaps),
                "intake_version": sdr.intake_version,
            },
            "recommended_next_action": f"tracking_plan(action='refine', params={{'sdr_id': '{sdr.id}', 'action': 'resume'}})",
            "instructions_for_claude": _build_save_confirmation_instructions(
                str(sdr.id), len(parsed.events), len(gaps), remaining
            ),
        }


async def _refresh_sdr_sources_v2(
    *,
    sdr_id: str,
    connector_filter: list[str] | None,
    reuse_intake: bool,
) -> dict:
    project_ctx = require_project_ctx()
    if not project_ctx:
        return no_active_project_response()
    if not state.current_user_ctx.get():
        return {"error": True, "error_type": "unauthenticated", "message": "No active session."}

    db_factory = state.db_session_factory
    async with db_factory() as db:
        sdr_result = await db.execute(select(SDR).where(SDR.id == _uuid.UUID(sdr_id)))
        sdr = sdr_result.scalar_one_or_none()
        if not sdr:
            return {"error": True, "error_type": "not_found", "message": f"SDR '{sdr_id}' not found."}
        if str(sdr.project_id) != project_ctx.project_id:
            return {"error": True, "error_type": "access_denied", "message": "SDR belongs to a different project."}
        parsed = parse_sdr_markdown(sdr.markdown_content)
        intake = (
            {"intake_version": sdr.intake_version, "answers": sdr.intake_answers or {}}
            if reuse_intake and sdr.intake_answers
            else None
        )
        previous_fingerprint = sdr.source_fingerprint

    requested = _normalize_source_names(connector_filter) if connector_filter else None
    scans = await scan_sources(project_ctx, requested)
    scanned_events = merge_scan_events(scans)
    deltas = _compute_source_deltas(parsed.events, scanned_events)
    current_fingerprint = compute_source_fingerprint(project_ctx)

    return {
        "sdr_id": sdr_id,
        "status": "delta_ready",
        "reuse_intake": reuse_intake,
        "intake": intake,
        "connected_sources": connected_sources_summary(project_ctx, scans),
        "scans": scans_to_dict(scans),
        "deltas": deltas,
        "reproducibility": {
            "previous_source_fingerprint": previous_fingerprint,
            "current_source_fingerprint": current_fingerprint,
            "scan_timestamp": datetime.now(UTC).isoformat(),
        },
        "instructions_for_claude": _build_delta_review_playbook(sdr_id, deltas),
    }


async def _get_sdr_intake(*, sdr_id: str) -> dict:
    project_ctx = require_project_ctx()
    if not project_ctx:
        return no_active_project_response()
    if not state.current_user_ctx.get():
        return {"error": True, "error_type": "unauthenticated", "message": "No active session."}

    db_factory = state.db_session_factory
    async with db_factory() as db:
        sdr_result = await db.execute(select(SDR).where(SDR.id == _uuid.UUID(sdr_id)))
        sdr = sdr_result.scalar_one_or_none()
        if not sdr:
            return {"error": True, "error_type": "not_found", "message": f"SDR '{sdr_id}' not found."}
        if str(sdr.project_id) != project_ctx.project_id:
            return {"error": True, "error_type": "access_denied", "message": "SDR belongs to a different project."}

        intakes = await db.execute(
            select(SDRIntake).where(SDRIntake.sdr_id == sdr.id).order_by(SDRIntake.answered_at.desc())
        )
        history = [row.to_dict() for row in intakes.scalars().all()]

    if not sdr.intake_answers and not history:
        return {
            "sdr_id": sdr_id,
            "status": "no_intake",
            "instructions_for_claude": (
                "This SDR has no captured intake. Offer to run the 6-question intake via "
                "tracking_plan(action='capture_intake') so future refreshes and audits have business context."
            ),
        }

    return {
        "sdr_id": sdr_id,
        "status": "intake_found",
        "intake": {
            "intake_version": sdr.intake_version,
            "answers": sdr.intake_answers or {},
        },
        "history": history,
        "instructions_for_claude": (
            "Use these persisted intake answers as ground truth for business context, KPIs, conversion "
            "definition, journeys, consent, and ownership when synthesizing or refining this SDR."
        ),
    }


async def _list_sdr_sources(*, sdr_id: str | None) -> dict:
    project_ctx = require_project_ctx()
    if not project_ctx:
        return no_active_project_response()
    if not state.current_user_ctx.get():
        return {"error": True, "error_type": "unauthenticated", "message": "No active session."}

    last_scan_sources: list[str] = []
    last_scanned_at: str | None = None
    if sdr_id:
        db_factory = state.db_session_factory
        async with db_factory() as db:
            sdr_result = await db.execute(select(SDR).where(SDR.id == _uuid.UUID(sdr_id)))
            sdr = sdr_result.scalar_one_or_none()
            if not sdr:
                return {"error": True, "error_type": "not_found", "message": f"SDR '{sdr_id}' not found."}
            if str(sdr.project_id) != project_ctx.project_id:
                return {"error": True, "error_type": "access_denied", "message": "SDR belongs to a different project."}
            if isinstance(sdr.last_source_scan, dict):
                last_scan_sources = sorted(sdr.last_source_scan.keys())
            if sdr.last_full_source_scan_at:
                last_scanned_at = sdr.last_full_source_scan_at.isoformat()

    return {
        "sdr_id": sdr_id,
        "supported_sources": list(SUPPORTED_SOURCE_NAMES),
        "available_now": get_available_sources(project_ctx),
        "connected_but_unsupported": connected_but_unsupported(project_ctx),
        "last_scan_sources": last_scan_sources,
        "last_full_source_scan_at": last_scanned_at,
        "instructions_for_claude": (
            "Report which sources are supported, currently scannable, and connected-but-not-yet-covered. "
            "If a connected source is unsupported, be explicit that the SDR does not yet reflect it."
        ),
    }


async def _persist_intake(db: Any, sdr: SDR, snapshot: dict, user_id: _uuid.UUID) -> None:
    version = snapshot.get("intake_version") or INTAKE_VERSION
    answers = snapshot.get("answers") or {}
    sdr.intake_answers = answers
    sdr.intake_version = version
    existing = await db.execute(
        select(SDRIntake).where(SDRIntake.sdr_id == sdr.id, SDRIntake.intake_version == version)
    )
    intake = existing.scalar_one_or_none()
    if intake:
        intake.answers = answers
        intake.answered_by = user_id
        intake.answered_at = datetime.now(UTC)
    else:
        db.add(
            SDRIntake(
                sdr_id=sdr.id,
                project_id=sdr.project_id,
                intake_version=version,
                answers=answers,
                answered_by=user_id,
            )
        )


def _section_is_filled(text: str | None) -> bool:
    """True when a section has substantive content and no unresolved TODO.

    Conservative on purpose: a false "complete" skips a section the user still
    needs, which is worse than re-walking a section that is already good. Any
    remaining ``[TODO]`` / ``[TODO: ...]`` marker means the section is not done.
    """
    if not text or not text.strip():
        return False
    if re.search(r"\[TODO", text, flags=re.IGNORECASE):
        return False
    meaningful: list[str] = []
    for line in text.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue
        # Skip markdown table separator / empty-cell rows.
        if re.fullmatch(r"\|[\s\-|]*\|?", stripped_line):
            continue
        content = stripped_line.replace("|", " ").strip()
        if content:
            meaningful.append(content)
    return len(" ".join(meaningful)) >= 12


def _synthesis_completed_sections(parsed: ParsedSDR) -> list[str]:
    """Infer which refinement sections a synthesized draft already satisfies.

    Lets save_sdr land the user on the first genuine gap (or review) instead of
    re-walking every section that Claude already wrote well.
    """
    event_done = any(
        e.purpose and "[TODO" not in (e.purpose or "") and (e.status or "") != "planned"
        for e in parsed.events
    )
    section_filled = {
        "business_context": _section_is_filled(parsed.business_context),
        "user_journeys": _section_is_filled(parsed.user_journeys),
        "data_layer_schema": _section_is_filled(parsed.data_layer_schema),
        "event_catalog": event_done,
        "user_properties": _section_is_filled(parsed.user_properties),
        "destinations_matrix": _section_is_filled(parsed.destinations_matrix),
        "consent_and_privacy": _section_is_filled(parsed.consent_and_privacy),
        "ownership": _section_is_filled(parsed.ownership),
    }
    return [s for s in SECTION_ORDER if section_filled.get(s)]


async def _seed_refinement_state(db: Any, sdr: SDR, parsed: ParsedSDR) -> list[str]:
    """Create/reset refinement state, marking already-synthesized sections complete.

    Returns the list of sections marked complete. The current section becomes the
    first genuine gap, or review_and_finalize when the draft is comprehensive.
    """
    completed = _synthesis_completed_sections(parsed)
    current = next((s for s in SECTION_ORDER if s not in completed), "review_and_finalize")

    existing_ref = await db.execute(select(SDRRefinementState).where(SDRRefinementState.sdr_id == sdr.id))
    ref_state = existing_ref.scalar_one_or_none()
    if ref_state:
        ref_state.current_section = current
        ref_state.sections_completed = completed
        ref_state.pending_proposed_changes = None
        ref_state.last_activity_at = datetime.now(UTC)
    else:
        db.add(
            SDRRefinementState(
                sdr_id=sdr.id,
                current_section=current,
                sections_completed=completed,
                last_activity_at=datetime.now(UTC),
            )
        )
    return completed


def _normalize_requested_sources(
    sources: list[str] | None,
    sources_ga4: bool,
    sources_gtm: bool,
    sources_ads: bool,
) -> list[str] | None:
    if sources is not None:
        return _normalize_source_names(sources)
    requested = []
    if sources_ga4:
        requested.append("ga4")
    if sources_gtm:
        requested.append("gtm")
    if sources_ads:
        requested.append("google_ads")
    return requested


def _normalize_source_names(sources: list[str] | None) -> list[str] | None:
    if sources is None:
        return None
    aliases = {"ads": "google_ads", "googleads": "google_ads", "google_ads": "google_ads"}
    normalized = []
    for source in sources:
        key = source.strip().lower()
        normalized.append(aliases.get(key, key))
    return normalized


def _normalize_intake_snapshot(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    answers = snapshot.get("answers") or snapshot.get("intake_answers") or {}
    if not answers:
        return None
    return {
        "intake_version": snapshot.get("intake_version") or INTAKE_VERSION,
        "answered_at": snapshot.get("answered_at") or datetime.now(UTC).isoformat(),
        "answers": answers,
    }


def _extract_source_fingerprint(snapshot: dict | None) -> str | None:
    if not snapshot:
        return None
    repro = snapshot.get("reproducibility") if isinstance(snapshot.get("reproducibility"), dict) else {}
    return snapshot.get("source_fingerprint") or repro.get("source_fingerprint")


def _next_draft_version(current: str | None) -> str:
    if not current or "-draft-" not in current:
        return "1.0-draft-1"
    prefix, _, suffix = current.rpartition("-draft-")
    try:
        return f"{prefix}-draft-{int(suffix) + 1}"
    except ValueError:
        return "1.0-draft-1"


def _event_to_dict(event: ParsedEvent) -> dict:
    return {
        "name": event.name,
        "purpose": event.purpose,
        "trigger_type": event.trigger_type,
        "trigger_config": event.trigger_config,
        "status": event.status,
        "parameters": [p.__dict__ for p in event.parameters],
        "destinations": [d.__dict__ for d in event.destinations],
    }


def _infer_business_type_from_intake(answers: dict[str, str]) -> str | None:
    text = " ".join(str(v).lower() for v in answers.values())
    if any(term in text for term in ("saas", "subscription", "trial", "freemium")):
        return "saas"
    if any(term in text for term in ("ecommerce", "shop", "cart", "checkout", "retail")):
        return "ecommerce"
    if any(term in text for term in ("lead", "demo", "quote", "form")):
        return "lead_gen"
    if any(term in text for term in ("content", "publisher", "media", "article")):
        return "media"
    if "marketplace" in text:
        return "marketplace"
    if "app" in text or "mobile" in text:
        return "app"
    return None


def _template_rationale(business_type: str, answers: dict[str, str], scanned_event_count: int) -> str:
    model = answers.get("business_model")
    if model:
        return f"Selected '{business_type}' from intake business model and {scanned_event_count} scanned events."
    return f"Selected '{business_type}' from scanned event signals; intake did not provide a stronger business model clue."


def _compute_source_deltas(current_events: list[ParsedEvent], scanned_events: list[ParsedEvent]) -> dict:
    """Diff a live source scan against the events already in the SDR.

    The SDR legitimately contains ``planned`` and template events that no
    connector will ever return, so "missing from scan" is scoped to events the
    SDR marks as live (``implemented``/``verified``). Only those disappearing is
    a real signal worth surfacing — everything else would be deletion noise.
    """
    current_by_name = {event.name: event for event in current_events}
    scanned_by_name = {event.name: event for event in scanned_events}

    added = [event for name, event in scanned_by_name.items() if name not in current_by_name]

    live_statuses = {"implemented", "verified"}
    missing = [
        event
        for name, event in current_by_name.items()
        if name not in scanned_by_name and (event.status or "").lower() in live_statuses
    ]

    destination_changes: list[dict] = []
    parameter_changes: list[dict] = []
    for name, event in scanned_by_name.items():
        current = current_by_name.get(name)
        if current is None:
            continue
        current_platforms = {d.platform for d in current.destinations}
        scanned_platforms = {d.platform for d in event.destinations}
        new_platforms = sorted(scanned_platforms - current_platforms)
        if new_platforms:
            destination_changes.append({"event": name, "added_destinations": new_platforms})

        current_params = {p.name for p in current.parameters}
        scanned_params = {p.name for p in event.parameters}
        added_params = sorted(scanned_params - current_params)
        if added_params:
            parameter_changes.append({"event": name, "added_parameters": added_params})

    proposals: list[dict] = []
    if added:
        proposals.append(
            {
                "section_path": "event_catalog",
                "change_type": "append",
                "to_value": "\n".join(
                    f"- `{event.name}` — discovered in connected source(s); confirm purpose and destinations."
                    for event in added
                ),
                "rationale": f"{len(added)} new event(s) appeared in connected sources since the last scan.",
            }
        )
    if missing:
        proposals.append(
            {
                "section_path": "event_catalog",
                "change_type": "append",
                "to_value": "\n".join(
                    f"- `{event.name}` was marked live but no longer appears in any scan — verify it was intentionally removed."
                    for event in missing
                ),
                "rationale": f"{len(missing)} previously-live event(s) are missing from the latest scan.",
            }
        )

    return {
        "added_events": [_event_to_dict(event) for event in added],
        "removed_or_missing_from_scan": [_event_to_dict(event) for event in missing],
        "destination_changes": destination_changes,
        "parameter_changes": parameter_changes,
        "proposals": proposals,
    }


# The exact markdown contract Claude must follow. It mirrors
# app.tools.sdr_parser.generate_sdr_markdown / parse_sdr_markdown so that the
# saved document round-trips into sdr_events / sdr_parameters / sdr_destinations.
# Drift here = events silently not projected = broken audits downstream.
_SDR_MARKDOWN_SCHEMA = """\
The document MUST use this exact structure so it parses into the event database.

YAML frontmatter first, then one H1 title, then these H2 sections IN ORDER, each
followed by a `---` separator line:

  ## Business Context
  ## User Journeys
  ## Data Layer Schema
  ## Event Catalog
  ## User Properties / Custom Dimensions
  ## Destinations Matrix
  ## Consent & Privacy
  ## Ownership & Governance
  ## Changelog

Each event in the Event Catalog is an H3 block in EXACTLY this shape (the parser
keys off these literal labels — keep them verbatim):

  ### `event_name`

  *Status:* `implemented` | *Last verified:* `never`

  **Business Purpose:** <one or two sentences tying the event to a KPI/journey>

  **Triggers:**
  - Type: `datalayer_event`        (one of: pageview, click, form_submit, datalayer_event, scroll, timer, custom)
  - Configuration: <where/how it fires>
  - Conditions: <edge cases — refunds, renewals, internal traffic — when relevant>

  **Parameters:**

  | Name | Type | Required | Source | Example | Validation |
  |---|---|---|---|---|---|
  | `transaction_id` | string | yes | dataLayer.ecommerce.transaction_id | `T-12345` | unique per order |

  **Destinations:**

  - **GA4**: event name `purchase`
  - **GOOGLE_ADS** (`AW-123`): event name `purchase`
  - **META**: event name `Purchase`

  **Consent Requirements:** `analytics_storage` | `ad_storage`

  **Owners:** Business: <team> · Technical: <team>

  **Related KPIs:** <comma-separated KPI names from the intake>

  **Edge Cases & Notes:** <anything a smart analyst would want flagged>

Status values: planned | implemented | verified | deprecated. Mark an event
`implemented` only when a live source scan actually showed it; otherwise `planned`.
Leave a `[TODO: ...]` marker anywhere you genuinely lack information — do NOT invent
facts to remove a TODO."""


def _build_synthesis_playbook(
    project_name: str,
    business_type: str,
    sdr_id: str | None,
    *,
    scanned_event_count: int = 0,
    template_event_count: int = 0,
    connected: dict | None = None,
) -> str:
    connected = connected or {}
    save_target = (
        f"the existing draft (pass sdr_id='{sdr_id}')" if sdr_id else "a new draft (omit sdr_id)"
    )
    unsupported = connected.get("connected_but_unsupported") or []
    failures = connected.get("partial_failures") or []

    coverage_note = ""
    if unsupported:
        coverage_note += (
            f"\nHONESTY ON COVERAGE: these platforms are connected but the scanner cannot read them yet: "
            f"{', '.join(unsupported)}. Do NOT claim they were analysed — add an explicit "
            "'Not yet covered' note for each in the relevant section.\n"
        )
    if failures:
        failed_names = ", ".join(f.get("source", "?") for f in failures)
        coverage_note += (
            f"\nPARTIAL/FAILED SCANS: {failed_names} returned errors or partial data. State the caveat "
            "in the SDR and base nothing on missing data.\n"
        )

    ecommerce_extra = ""
    if business_type in ("ecommerce", "marketplace"):
        ecommerce_extra = (
            "\nECOMMERCE GOLD-STANDARD CHECKLIST (this is an ecommerce property):\n"
            "- Model the full GA4 funnel: view_item_list → select_item → view_item → add_to_cart → "
            "view_cart → begin_checkout → add_shipping_info → add_payment_info → purchase, plus refund.\n"
            "- `purchase` MUST carry transaction_id (unique), value (>0), currency (ISO 4217) and an items[] array; "
            "flag deduplication (transaction_id) and whether refunds/renewals fire it.\n"
            "- Map revenue events cross-platform with the platform's own names: Meta uses ViewContent / AddToCart / "
            "InitiateCheckout / AddPaymentInfo / Purchase; Google Ads uses conversion actions.\n"
            "- Tie every event to a Business Context KPI (revenue, AOV, conversion rate, ROAS) and to a User Journey.\n"
        )

    return f"""You are now the senior analytics architect for "{project_name}". You have the raw \
source scans, the intake answers, the '{business_type}' industry template, and a parse-valid \
`markdown_skeleton` in this same response. Your job is to turn that material into an \
industry gold-standard Solution Design Reference. The server gathered the facts; the intelligence is yours.

WORKFLOW:
1. Start from `markdown_skeleton` — it already has the correct structure, the {scanned_event_count} \
live-scanned event(s), and {template_event_count} template event(s) as `planned` gaps. Edit it in place; do not restructure it.
2. Cross-reference relentlessly. The point of this tool is synthesis the server cannot do:
   - "Intake says the primary conversion is X, but no scanned event carries the parameters that prove X — that is the most important gap."
   - "GTM fires `purchase` but the GA4 property has no matching conversion event / no value parameter — data-quality gap."
   - Reconcile the same event seen from multiple sources into one authoritative entry.
3. Use the intake answers as ground truth for Business Context, Primary KPIs, conversion definition, key journeys, \
consent posture, and ownership. Prefer the user's real words over generic template prose.
4. Prioritise: conversion + revenue events first, then the events on the stated key journeys, then everything else.
5. Be honest. Mark events `implemented` only if a scan proved them; otherwise `planned`. Keep `[TODO: ...]` where you truly lack info.
{coverage_note}{ecommerce_extra}
MARKDOWN CONTRACT (non-negotiable — the document is parsed back into the event database):
{_SDR_MARKDOWN_SCHEMA}

WHEN DONE:
Call save_sdr(markdown=<full document>, intake_snapshot=<the `intake` object from this response>, \
source_snapshot=<the `scans` object from this response>) to persist {save_target}. Then offer the user a \
walkthrough of remaining TODOs/gaps via tracking_plan(action='refine')."""


def _build_save_confirmation_instructions(
    sdr_id: str, event_count: int, gap_count: int, remaining_sections: list[str] | None = None
) -> str:
    remaining = remaining_sections or []
    landing = remaining[0] if remaining else "review_and_finalize"
    return (
        f"The SDR draft was saved as {sdr_id} with {event_count} parsed events and {gap_count} gaps/TODOs. "
        f"Sections Claude already completed are marked done; refinement will resume at '{landing}'. "
        "Tell the user the draft is saved, summarize the headline gaps, then offer to continue. "
        f"If they agree, call tracking_plan(action='refine', params={{'sdr_id': '{sdr_id}', 'action': 'resume'}})."
    )


def _build_delta_review_playbook(sdr_id: str, deltas: dict) -> str:
    return (
        "Review this source refresh as a senior analytics architect. Lead with material additions, destination changes, and anything that affects "
        "the persisted intake conversion definition or primary KPIs. Do not auto-write the SDR. Ask the user to accept, reject, or adjust the proposed "
        f"changes. If they accept the generated proposals, call tracking_plan(action='refine', params={{'sdr_id': '{sdr_id}', "
        "'action': 'apply_source_delta', 'source_delta': <delta payload>}}). "
        f"Delta counts: {len(deltas.get('added_events', []))} added events, "
        f"{len(deltas.get('destination_changes', []))} destination changes, "
        f"{len(deltas.get('parameter_changes', []))} parameter changes."
    )


def _merge_scan_with_template(
    scan_events: list[ParsedEvent], template_events: list[ParsedEvent]
) -> list[ParsedEvent]:
    """Combine live-scanned events with template events.

    Scanned events win (they are real, status ``implemented``); template events
    not present in the scan are appended as ``planned`` so the skeleton models the
    full ideal funnel while making clear which parts are aspirational.
    """
    by_name: dict[str, ParsedEvent] = {e.name: e for e in scan_events}
    merged: list[ParsedEvent] = list(scan_events)
    for tmpl in template_events:
        if tmpl.name not in by_name:
            tmpl.status = tmpl.status or "planned"
            merged.append(tmpl)
            by_name[tmpl.name] = tmpl
    return merged


def _build_markdown_skeleton(
    *,
    project_name: str,
    project_id: str,
    business_type: str,
    events: list[ParsedEvent],
    intake_answers: dict[str, str],
) -> str:
    """Produce a parse-valid SDR skeleton seeded with events + intake context.

    Reuses generate_sdr_markdown so the structure is guaranteed to round-trip.
    Intake answers pre-seed the prose sections; missing pieces stay as [TODO]
    markers for Claude to resolve during synthesis.
    """
    answers = intake_answers or {}

    business_context = None
    model = (answers.get("business_model") or "").strip()
    conversion = (answers.get("conversion_definition") or "").strip()
    if model or conversion:
        parts = []
        if model:
            parts.append(model)
        if conversion:
            parts.append(f"*Conversion definition (from intake):* {conversion}")
        business_context = "\n\n".join(parts)

    user_journeys = (answers.get("key_journeys") or "").strip() or None
    consent_md = (answers.get("privacy_consent") or "").strip() or None
    ownership_md = (answers.get("ownership_complexity") or "").strip() or None

    return generate_sdr_markdown(
        project_name=project_name,
        project_id=project_id,
        business_type=business_type,
        events=events,
        business_context=business_context,
        user_journeys=user_journeys,
        consent_md=consent_md,
        ownership_md=ownership_md,
    )


def _infer_business_type(events: list[ParsedEvent]) -> str:
    """Infer business type from discovered events."""
    event_names = {e.name.lower() for e in events}

    ecommerce_signals = {"purchase", "add_to_cart", "view_item", "begin_checkout", "refund"}
    saas_signals = {"sign_up", "trial_start", "subscribe", "feature_used"}
    lead_gen_signals = {"form_submit", "form_view", "lead_qualified"}
    media_signals = {"content_view", "scroll_depth", "video_play"}

    scores = {
        "ecommerce": len(event_names & ecommerce_signals),
        "saas": len(event_names & saas_signals),
        "lead_gen": len(event_names & lead_gen_signals),
        "media": len(event_names & media_signals),
    }

    best = max(scores, key=scores.get)  # type: ignore
    return best if scores[best] > 0 else "ecommerce"  # default to ecommerce


# ---------------------------------------------------------------------------
# refine_sdr action handlers
# ---------------------------------------------------------------------------


def _build_progress(ref_state: SDRRefinementState) -> dict:
    """Build progress info from refinement state."""
    completed = ref_state.sections_completed or []
    total = len(SECTION_ORDER)
    done = len(completed)
    return {
        "sections_completed": done,
        "sections_total": total,
        "percent": round((done / total) * 100, 1) if total else 0,
        "remaining_sections": [s for s in SECTION_ORDER if s not in completed],
        "completed_sections": completed,
    }


def _can_finalize(ref_state: SDRRefinementState, sdr: SDR) -> bool:
    """Check if minimum sections are complete for finalization."""
    completed = set(ref_state.sections_completed or [])
    # event_catalog counts if any event_catalog.* sub-section is complete
    has_event = any(s.startswith("event_catalog") for s in completed) or "event_catalog" in completed
    has_biz = "business_context" in completed
    has_dest = "destinations_matrix" in completed
    return has_biz and has_event and has_dest


def _next_section(ref_state: SDRRefinementState) -> str:
    """Get the next incomplete section in order."""
    completed = set(ref_state.sections_completed or [])
    for section in SECTION_ORDER:
        if section not in completed:
            return section
    return "review_and_finalize"


def _handle_show_status(sdr: SDR, ref_state: SDRRefinementState) -> dict:
    """Return current state without advancing."""
    parsed = parse_sdr_markdown(sdr.markdown_content)
    gaps = compute_gaps(parsed)

    return {
        "sdr_id": str(sdr.id),
        "current_section": ref_state.current_section,
        "progress": _build_progress(ref_state),
        "status_after_call": "in_progress",
        "can_finalize": _can_finalize(ref_state, sdr),
        "can_skip": ref_state.current_section != "review_and_finalize",
        "summary": {
            "events": len(parsed.events),
            "todo_count": len(parsed.todo_markers),
            "gaps": len(gaps),
        },
        "current_draft_excerpt": sdr.markdown_content[:3000],
        "instructions_for_claude": (
            f"The user asked for status. The SDR has {len(parsed.events)} events, "
            f"{len(parsed.todo_markers)} TODOs remaining, and is on section "
            f"'{ref_state.current_section}'. Share a concise summary and ask what "
            "they'd like to work on next."
        ),
        "user_options": [
            {"label": "Continue refining", "action_args": {"action": "resume"}},
            {"label": "Jump to a section", "action_args": {"action": "goto_section"}},
            {"label": "Finalize", "action_args": {"action": "finalize"}},
        ],
    }


def _handle_resume(sdr: SDR, ref_state: SDRRefinementState) -> dict:
    """Continue from current section."""
    current = ref_state.current_section
    parsed = parse_sdr_markdown(sdr.markdown_content)

    # Check for long gap
    gap_message = ""
    if ref_state.last_activity_at:
        days_since = (datetime.now(UTC) - ref_state.last_activity_at.replace(tzinfo=UTC)).days
        if days_since > 7:
            gap_message = (
                f"It's been {days_since} days since last activity. "
                "Offer a quick recap of where we left off before continuing."
            )

    instructions = _get_section_instructions(current, sdr, parsed)

    return {
        "sdr_id": str(sdr.id),
        "current_section": current,
        "progress": _build_progress(ref_state),
        "status_after_call": "awaiting_user_input",
        "can_finalize": _can_finalize(ref_state, sdr),
        "can_skip": current != "review_and_finalize",
        "current_draft_excerpt": _get_section_excerpt(sdr.markdown_content, current),
        "instructions_for_claude": (gap_message + "\n\n" if gap_message else "") + instructions,
        "proposed_changes": None,
        "user_options": _get_section_options(str(sdr.id), current),
    }


def _handle_goto_section(sdr: SDR, ref_state: SDRRefinementState, section: str) -> dict:
    """Jump to a specific section."""
    # Validate section
    valid = set(SECTION_ORDER)
    # Also allow event_catalog.{event_name} sub-sections
    if section.startswith("event_catalog."):
        valid.add(section)
    elif section not in valid:
        return {
            "error": True,
            "message": f"Unknown section '{section}'. Valid sections: {SECTION_ORDER}",
        }

    ref_state.current_section = section
    parsed = parse_sdr_markdown(sdr.markdown_content)
    instructions = _get_section_instructions(section, sdr, parsed)

    return {
        "sdr_id": str(sdr.id),
        "current_section": section,
        "progress": _build_progress(ref_state),
        "status_after_call": "awaiting_user_input",
        "can_finalize": _can_finalize(ref_state, sdr),
        "can_skip": True,
        "current_draft_excerpt": _get_section_excerpt(sdr.markdown_content, section),
        "instructions_for_claude": instructions,
        "proposed_changes": None,
        "user_options": _get_section_options(str(sdr.id), section),
    }


def _handle_submit_answer(sdr: SDR, ref_state: SDRRefinementState, user_input: str) -> dict:
    """Process user input and generate proposed changes."""
    current = ref_state.current_section
    parsed = parse_sdr_markdown(sdr.markdown_content)

    # Generate proposed changes based on section and user input
    proposed = _generate_proposed_changes(current, user_input, sdr, parsed)

    # Store pending changes
    ref_state.pending_proposed_changes = {
        "section": current,
        "user_input": user_input,
        "changes": [
            {
                "section_path": c["section_path"],
                "change_type": c["change_type"],
                "to_value": c["to_value"],
                "rationale": c["rationale"],
            }
            for c in proposed
        ],
    }

    return {
        "sdr_id": str(sdr.id),
        "current_section": current,
        "progress": _build_progress(ref_state),
        "status_after_call": "awaiting_user_input",
        "can_finalize": _can_finalize(ref_state, sdr),
        "can_skip": True,
        "proposed_changes": proposed,
        "instructions_for_claude": (
            f"Based on the user's input, I've proposed the following changes to the "
            f"'{current}' section. Show the user what will change and ask them to "
            f"confirm. If they approve, call tracking_plan(action='refine', params={{'sdr_id': '{sdr.id!s}', 'action': 'accept_proposed'}}). "
            f"If they want something different, call tracking_plan(action='refine', params={{'sdr_id': '{sdr.id!s}', 'action': 'reject_proposed'}})."
        ),
        "user_options": [
            {"label": "Accept changes", "action_args": {"action": "accept_proposed"}},
            {"label": "Reject & try again", "action_args": {"action": "reject_proposed"}},
            {"label": "Skip this section", "action_args": {"action": "skip_section"}},
        ],
    }


def _handle_apply_source_delta(sdr: SDR, ref_state: SDRRefinementState, source_delta: dict) -> dict:
    """Turn a refresh_sdr_sources delta into the normal accept/reject loop."""
    deltas = source_delta.get("deltas") if "deltas" in source_delta else source_delta
    proposals = deltas.get("proposals") or []
    if not proposals:
        added = deltas.get("added_events") or []
        proposals = [
            {
                "section_path": "event_catalog",
                "change_type": "append",
                "to_value": "\n".join(f"- Add `{event.get('name')}` from refreshed sources." for event in added),
                "rationale": "New events were discovered by refresh_sdr_sources.",
            }
        ] if added else []
    if not proposals:
        return {
            "sdr_id": str(sdr.id),
            "current_section": ref_state.current_section,
            "progress": _build_progress(ref_state),
            "status_after_call": "in_progress",
            "proposed_changes": [],
            "instructions_for_claude": "The source refresh did not produce any concrete proposed SDR changes. Summarize that no write is needed.",
            "user_options": _get_section_options(str(sdr.id), ref_state.current_section),
        }

    ref_state.pending_proposed_changes = {
        "section": "source_delta",
        "user_input": "Source refresh delta",
        "changes": [
            {
                "section_path": proposal.get("section_path", "event_catalog"),
                "change_type": proposal.get("change_type", "append"),
                "to_value": proposal.get("to_value", ""),
                "rationale": proposal.get("rationale", "Accepted source refresh change."),
            }
            for proposal in proposals
        ],
    }
    ref_state.current_section = "event_catalog"

    return {
        "sdr_id": str(sdr.id),
        "current_section": ref_state.current_section,
        "progress": _build_progress(ref_state),
        "status_after_call": "awaiting_user_input",
        "can_finalize": _can_finalize(ref_state, sdr),
        "can_skip": True,
        "proposed_changes": proposals,
        "instructions_for_claude": (
            "Source-refresh proposals are now staged in the normal SDR refinement accept/reject flow. "
            f"Show the user the proposed changes and, if approved, call tracking_plan(action='refine', "
            f"params={{'sdr_id': '{sdr.id!s}', 'action': 'accept_proposed'}})."
        ),
        "user_options": [
            {"label": "Accept changes", "action_args": {"action": "accept_proposed"}},
            {"label": "Reject & try again", "action_args": {"action": "reject_proposed"}},
        ],
    }


async def _handle_accept_proposed(db: Any, sdr: SDR, ref_state: SDRRefinementState) -> dict:
    """Apply pending proposed changes to markdown and advance."""
    pending = ref_state.pending_proposed_changes
    if not pending:
        return {
            "error": True,
            "message": "No pending changes to accept. Submit an answer first.",
        }

    # Apply changes to markdown
    changes = pending.get("changes", [])
    markdown = sdr.markdown_content

    for change in changes:
        to_value = change.get("to_value", "")
        section_path = change.get("section_path", "")

        # Apply the change based on section_path
        markdown = _apply_change_to_markdown(
            markdown, section_path, to_value, change.get("change_type", "modify")
        )

    sdr.markdown_content = markdown
    sdr.updated_at = datetime.now(UTC)

    # Rebuild projections
    await rebuild_projections_async(db, sdr)

    # Mark section complete and advance
    current = ref_state.current_section
    completed = list(ref_state.sections_completed or [])
    if current not in completed:
        completed.append(current)
    ref_state.sections_completed = completed
    ref_state.pending_proposed_changes = None

    # Advance to next section
    next_sec = _next_section(ref_state)
    ref_state.current_section = next_sec

    parsed = parse_sdr_markdown(markdown)
    instructions = _get_section_instructions(next_sec, sdr, parsed)

    return {
        "sdr_id": str(sdr.id),
        "current_section": next_sec,
        "progress": _build_progress(ref_state),
        "status_after_call": "awaiting_user_input"
        if next_sec != "review_and_finalize"
        else "awaiting_approval",
        "can_finalize": _can_finalize(ref_state, sdr),
        "can_skip": next_sec != "review_and_finalize",
        "instructions_for_claude": (
            f"Changes to '{current}' have been applied and saved. Moving to '{next_sec}'.\n\n{instructions}"
        ),
        "proposed_changes": None,
        "user_options": _get_section_options(str(sdr.id), next_sec),
    }


def _handle_reject_proposed(sdr: SDR, ref_state: SDRRefinementState) -> dict:
    """Drop pending changes and re-ask."""
    ref_state.pending_proposed_changes = None
    current = ref_state.current_section

    return {
        "sdr_id": str(sdr.id),
        "current_section": current,
        "progress": _build_progress(ref_state),
        "status_after_call": "awaiting_user_input",
        "can_finalize": _can_finalize(ref_state, sdr),
        "can_skip": True,
        "instructions_for_claude": (
            "The user rejected the proposed changes. Ask them what they'd like "
            "different — re-phrase the question or gather more specific input, "
            "then call submit_answer again with their revised response."
        ),
        "proposed_changes": None,
        "user_options": [
            {"label": "Try different answer", "action_args": {"action": "submit_answer"}},
            {"label": "Skip this section", "action_args": {"action": "skip_section"}},
        ],
    }


def _handle_skip_section(sdr: SDR, ref_state: SDRRefinementState) -> dict:
    """Skip current section and advance."""
    current = ref_state.current_section
    ref_state.pending_proposed_changes = None

    # Don't mark as complete — just advance
    next_sec = _next_section_after(current)
    ref_state.current_section = next_sec

    parsed = parse_sdr_markdown(sdr.markdown_content)
    instructions = _get_section_instructions(next_sec, sdr, parsed)

    return {
        "sdr_id": str(sdr.id),
        "current_section": next_sec,
        "progress": _build_progress(ref_state),
        "status_after_call": "awaiting_user_input",
        "can_finalize": _can_finalize(ref_state, sdr),
        "can_skip": next_sec != "review_and_finalize",
        "instructions_for_claude": (
            f"Skipped '{current}'. You can come back to it anytime. Moving to '{next_sec}'.\n\n{instructions}"
        ),
        "proposed_changes": None,
        "user_options": _get_section_options(str(sdr.id), next_sec),
    }


async def _handle_finalize(
    db: Any,
    sdr: SDR,
    ref_state: SDRRefinementState,
    user_ctx: Any,
    changelog_note: str | None,
) -> dict:
    """Snapshot current draft as a new approved version."""
    # Check minimum sections
    if not _can_finalize(ref_state, sdr):
        missing = REQUIRED_FOR_FINALIZE - set(ref_state.sections_completed or [])
        return {
            "error": True,
            "error_type": "incomplete",
            "message": f"Cannot finalize — required sections incomplete: {sorted(missing)}",
            "instructions_for_claude": (
                f"The SDR can't be finalized yet. These sections must be completed first: "
                f"{', '.join(sorted(missing))}. Guide the user to complete them."
            ),
        }

    # Determine version number
    latest_version = await db.execute(
        select(SDRVersion).where(SDRVersion.sdr_id == sdr.id).order_by(SDRVersion.approved_at.desc()).limit(1)
    )
    latest = latest_version.scalar_one_or_none()

    if latest:
        # Require changelog for versions > 1.0
        if not changelog_note:
            return {
                "error": True,
                "message": "changelog_note is required for versions after 1.0.",
                "instructions_for_claude": (
                    "Ask the user to provide a brief changelog note describing what changed in this version."
                ),
            }
        # Auto-increment version
        version_num = _increment_version(latest.version_number, changelog_note)
    else:
        version_num = "1.0"
        changelog_note = changelog_note or "Initial SDR"

    # Create version snapshot
    user_id = _uuid.UUID(str(user_ctx.user_id))
    version = SDRVersion(
        sdr_id=sdr.id,
        version_number=version_num,
        markdown_snapshot=sdr.markdown_content,
        changelog=changelog_note,
        approved_by=user_id,
    )
    db.add(version)
    await db.flush()

    # Update SDR
    sdr.current_version_id = version.id
    sdr.status = "approved"

    # Update markdown frontmatter with version info
    sdr.markdown_content = _update_frontmatter(
        sdr.markdown_content,
        sdr_version=version_num,
        sdr_status="approved",
        last_approved_by=str(user_ctx.email),
        last_approved_at=datetime.now(UTC).isoformat(),
    )

    return {
        "sdr_id": str(sdr.id),
        "version": version_num,
        "status": "finalized",
        "current_section": "review_and_finalize",
        "progress": _build_progress(ref_state),
        "status_after_call": "finalized",
        "can_finalize": False,
        "can_skip": False,
        "instructions_for_claude": (
            f"SDR v{version_num} has been approved and snapshotted. "
            f"From now on, tag audits will validate against SDR v{version_num}. "
            "Any drift between live implementation and this spec will show up in "
            "audit reports.\n\n"
            'Say: "SDR v' + version_num + " is now live. Audits will validate "
            "against this version. Want to start working on changes for the "
            'next version, or are we done for now?"'
        ),
        "user_options": [
            {"label": "Start next version", "action_args": {"action": "start_new_draft"}},
            {"label": "Done for now", "action_args": {}},
        ],
    }


async def _handle_start_new_draft(db: Any, sdr: SDR, ref_state: SDRRefinementState) -> dict:
    """Begin editing for next version after approval."""
    # Reset status to draft (markdown stays as-is from last approved)
    sdr.status = "draft"
    sdr.markdown_content = _update_frontmatter(
        sdr.markdown_content,
        sdr_status="draft",
        sdr_version=None,  # will be set on next finalize
    )

    # Reset refinement state
    ref_state.current_section = "business_context"
    ref_state.sections_completed = list(SECTION_ORDER[:-1])  # all complete from prior version
    ref_state.pending_proposed_changes = None

    return {
        "sdr_id": str(sdr.id),
        "current_section": "business_context",
        "progress": _build_progress(ref_state),
        "status_after_call": "awaiting_user_input",
        "can_finalize": True,  # prior version was complete
        "can_skip": True,
        "instructions_for_claude": (
            "A new draft has been started from the last approved version. "
            "All prior sections are marked complete. Ask the user what they "
            "want to change — they can jump to any section or make specific edits."
        ),
        "user_options": [
            {"label": "Jump to a section", "action_args": {"action": "goto_section"}},
            {"label": "Show current status", "action_args": {"action": "show_status"}},
        ],
    }


# ---------------------------------------------------------------------------
# Section instructions (conversation scripts)
# ---------------------------------------------------------------------------


def _get_section_instructions(section: str, sdr: SDR, parsed: ParsedSDR) -> str:
    """Get the instruction script for a given section."""
    sdr_id = str(sdr.id)

    if section == "business_context":
        return _instructions_business_context(sdr_id, parsed)
    elif section == "user_journeys":
        return _instructions_user_journeys(sdr_id, parsed)
    elif section == "data_layer_schema":
        return _instructions_data_layer(sdr_id, parsed)
    elif section.startswith("event_catalog"):
        event_name = section.split(".", 1)[1] if "." in section else None
        return _instructions_event_catalog(sdr_id, parsed, event_name)
    elif section == "user_properties":
        return _instructions_user_properties(sdr_id, parsed)
    elif section == "destinations_matrix":
        return _instructions_destinations(sdr_id, parsed)
    elif section == "consent_and_privacy":
        return _instructions_consent(sdr_id, parsed)
    elif section == "ownership":
        return _instructions_ownership(sdr_id, parsed)
    elif section == "review_and_finalize":
        return _instructions_review(sdr_id, parsed)
    else:
        return f"Unknown section '{section}'. Ask the user which section to work on."


def _instructions_business_context(sdr_id: str, parsed: ParsedSDR) -> str:
    current_content = parsed.business_context or "[empty — all TODOs]"
    btype = parsed.business_type or "unknown"
    return f"""SECTION: Business Context

You're helping the user fill in the Business Context section of their SDR. This is \
the "why" that grounds every event in the catalog. Without it, an SDR is just a \
technical manifest — with it, every event has a reason and every gap has a priority.

WHAT'S IN THE DOC NOW:
{current_content[:500]}

WHAT BOOTSTRAP INFERRED: business_type_hint = "{btype}"

YOUR JOB: Have a brief conversation to capture:
1. Business model in one sentence (what the business sells, to whom, how it makes money)
2. Top 2-3 KPIs the analytics are meant to support
3. What "conversion" means to them (specific user action, not jargon)
4. Any non-obvious business context (multi-brand, marketplace two-sidedness, \
   freemium mechanics, regional differences, etc.)

HOW TO ASK:
Don't ask all four at once. Open with a warm one-liner and ask 1-2 at a time.
Example opening:

  "Let's ground the SDR with some business context — makes every event in the \
  catalog more meaningful. In one sentence, how does the business make money?"

After they answer, follow up with KPIs:
  "Got it. What are the top 2-3 KPIs the analytics stack is supposed to move?"

Then conversion definition:
  "And when we say 'conversion' in this business — what specifically counts? A \
  purchase, a signup, a qualified lead? Be as concrete as you can — 'subscriber \
  with verified email' is better than 'signup'."

HOW TO USE ANSWERS:
After each answer, call:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "submit_answer", "user_input": "<their words>"}})

The tool will generate proposed changes to the Business Context markdown. Show them \
the diff, ask for confirmation. If they approve:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "accept_proposed"}})

If they want to adjust:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "reject_proposed"}})
  Then re-ask and try again.

IF USER RESISTS: Some users want to skip context and jump to events. That's fine. \
Business Context can be filled later. If they say "can we come back to this", call:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "skip_section"}})

KB CROSS-REFERENCE: If the project's Knowledge Base already has a business \
description, reference it instead of re-asking. Say: "I see your KB already \
describes the business — should I pull that into the SDR, or is this SDR-specific?"

TONE: Conversational, concise, subject-matter-expert but not condescending. You're a \
senior analytics consultant helping a colleague document their setup.

DONE CRITERIA: At least business_model sentence AND 1 KPI captured. Conversion \
definition and extra context nice-to-have but not required to advance."""


def _instructions_user_journeys(sdr_id: str, parsed: ParsedSDR) -> str:
    event_names = [e.name for e in parsed.events[:15]]
    # Try to infer likely journeys from event clusters
    ecom_flow = [
        n
        for n in event_names
        if n in ("view_item_list", "view_item", "add_to_cart", "begin_checkout", "purchase")
    ]
    saas_flow = [n for n in event_names if n in ("sign_up", "trial_start", "subscribe", "feature_used")]
    lead_flow = [n for n in event_names if n in ("form_view", "form_start", "form_submit", "lead_qualified")]

    inferred = []
    if len(ecom_flow) >= 2:
        inferred.append(f"Ecommerce purchase: {' → '.join(ecom_flow)}")
    if len(saas_flow) >= 2:
        inferred.append(f"SaaS trial-to-paid: {' → '.join(saas_flow)}")
    if len(lead_flow) >= 2:
        inferred.append(f"Lead generation: {' → '.join(lead_flow)}")
    inferred_str = (
        "\n".join(f"  {i + 1}. {j}" for i, j in enumerate(inferred))
        if inferred
        else "  (none confidently inferred)"
    )

    return f"""SECTION: User Journeys

Now we're mapping the key user journeys. Each journey is a named flow that your \
event catalog supports — "first-time purchase", "trial-to-paid conversion", \
"lead-form submission", etc. Journeys give the SDR narrative — without them events \
are disconnected points; with them events are steps in a flow.

WHAT BOOTSTRAP INFERRED:
Based on discovered events, likely journeys include:
{inferred_str}

Discovered events: {", ".join(event_names) if event_names else "none yet"}

YOUR JOB: Name 2-5 core journeys with brief descriptions and entry/completion \
markers. Don't be exhaustive — we want the load-bearing flows.

HOW TO ASK:
If bootstrap produced inferences, present them and let the user confirm/edit:

  "Based on your events I'm seeing a few likely flows:
   {chr(10).join(f"   {i + 1}. {j}" for i, j in enumerate(inferred)) if inferred else "   (none clearly inferred — let me ask)"}

   Does that match how you think about your users? Any flows missing or named \
   differently?"

If bootstrap didn't produce inferences:
  "Let's name the 2-3 most important user flows on your site. Think: what's the \
  path a user takes when they do the thing you most want them to do? Start there."

HOW TO USE ANSWERS:
For each journey the user confirms or proposes, extract:
- Name
- Entry points (URLs, triggers)
- Completion marker (which event signals success)
- Events involved (from the bootstrapped catalog)

Then:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "submit_answer", "user_input": "<their journeys>"}})

Tool proposes markdown for each. Confirm before committing.

NOTE: If the user names events in journeys that aren't in the catalog yet, flag \
them for the event catalog section. This is great — it means refinement is \
surfacing gaps bootstrap missed.

DONE CRITERIA: At least 1 named journey with entry + completion."""


def _instructions_data_layer(sdr_id: str, parsed: ParsedSDR) -> str:
    return f"""SECTION: Data Layer Schema

Document the conventions used across the site for dataLayer pushes. This section \
helps anyone reading the SDR understand the technical contract between the site \
and the tag management layer.

YOUR JOB: Capture:
1. Naming convention — snake_case for event names? camelCase for parameters? Something else?
2. Standard push shape — what does a typical dataLayer.push look like?
3. Ecommerce schema — does the site use GA4's standard ecommerce dataLayer? Custom?
4. Any global parameters always included (page_type, user_status, etc.)?

HOW TO ASK:
Start with naming:
  "What naming convention do you use for dataLayer event names and parameters? \
  snake_case like 'add_to_cart', camelCase like 'addToCart', or something else?"

Then structure:
  "Do you use the standard GA4 ecommerce dataLayer schema (with the ecommerce \
  object and items array), or a custom structure?"

If they don't know or aren't technical:
  "No worries — I can infer a lot of this from the GTM config. Let me document \
  what I see and you can confirm. Want to skip to events instead?"

HOW TO USE ANSWERS:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "submit_answer", "user_input": "<response>"}})

DONE CRITERIA: Naming convention captured. Ecommerce shape nice-to-have. \
If user is non-technical, skip gracefully."""


def _instructions_event_catalog(sdr_id: str, parsed: ParsedSDR, event_name: str | None) -> str:
    events = parsed.events
    total = len(events)

    # Sort events: conversions first, then by TODO count
    def _priority(e: ParsedEvent) -> int:
        score = 0
        if e.status == "implemented":
            score += 10
        if any(d.platform == "google_ads" for d in e.destinations):
            score += 20  # conversion events are highest priority
        if not e.purpose or "[TODO" in (e.purpose or ""):
            score += 5  # needs work = higher priority
        return -score

    if event_name:
        target = next((e for e in events if e.name == event_name), None)
        if not target:
            return f"Event '{event_name}' not found in catalog. Ask which event to work on."
        idx = next((i for i, e in enumerate(events) if e.name == event_name), 0)
    else:
        # Find first event with TODOs, prioritized
        sorted_events = sorted(events, key=_priority)
        target = None
        idx = 0
        for e in sorted_events:
            if not e.purpose or "[TODO" in (e.purpose or ""):
                target = e
                idx = next((i for i, ev in enumerate(events) if ev.name == e.name), 0)
                break
        if not target and events:
            target = events[0]
            idx = 0

    if not target:
        return (
            "No events in the catalog yet. Ask the user what events they track on "
            "their site. Start with conversions — purchases, signups, form submissions."
        )

    params_list = (
        ", ".join(f"`{p.name}`" for p in target.parameters) if target.parameters else "none discovered"
    )
    dest_list = (
        ", ".join(d.platform.upper() for d in target.destinations) if target.destinations else "none mapped"
    )

    # Determine priority reason
    priority = "medium"
    priority_reason = "standard event"
    if any(d.platform == "google_ads" for d in target.destinations):
        priority = "high"
        priority_reason = "it's an Ads conversion event"
    elif target.name in ("purchase", "sign_up", "subscribe", "form_submit"):
        priority = "high"
        priority_reason = "it's a key conversion event"

    return f"""SECTION: Event Catalog → {target.name}

This is event {idx + 1} of {total}. Priority: {priority} (reason: {priority_reason}).

CURRENT STATE IN DOC:
- Name: `{target.name}`
- Business Purpose: {target.purpose or "[TODO — needs input]"}
- Trigger: {target.trigger_type or "[TODO]"} {("— config: " + str(target.trigger_config)) if target.trigger_config else ""}
- Parameters: {params_list}
- Destinations: {dest_list}
- Status: {target.status or "unknown"}

YOUR JOB: Fill the gaps for this event. Key questions:

1. BUSINESS PURPOSE — Why do we track this? What business question does it answer?
   If bootstrap has NO purpose, this is required.
   If bootstrap has a template purpose, confirm and enrich with business-specific context.

2. TRIGGER EDGE CASES — Does the trigger fire in ways that might not match intent?
   For `purchase`: do refunds count? Subscription renewals? Free trials?
   For `sign_up`: does social login fire this? Password reset?

3. REQUIRED vs OPTIONAL PARAMETERS — For each bootstrapped param, is it required \
   or nice-to-have? If a param is missing on a firing, should it fail validation?

4. PARAMETER VALIDATION RULES — For value: must be > 0? For currency: ISO 4217? \
   For items: non-empty array?

5. MISSING PARAMETERS — Any business-specific params bootstrap couldn't know? \
   ("is_first_purchase", "customer_tier", "promo_code", etc.)

6. DESTINATION MAPPING DETAILS — For cross-platform destinations, any \
   transformations? (Meta expects "ViewContent" not "view_item"; Ads needs \
   currency in specific format, etc.)

HOW TO ASK:
Go one topic at a time. Start with highest-leverage:

  "Let's lock in the `{target.name}` event. First, purpose — bootstrap inferred \
  '{(target.purpose or "nothing")[:100]}'. Does that capture why the business \
  tracks this, or is there more to it?"

Then triggers/edge cases:
  "And on triggering — does this fire every time [describe trigger], or are there \
  cases where it shouldn't? Think refunds, cancellations, bot traffic, internal testing."

Then parameters:
  "Here are the parameters we see it carrying: {params_list}. Which of these are \
  required (must always be present), and are any missing that you care about?"

HOW TO USE ANSWERS:
For each answer, propose structured changes:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "submit_answer", "user_input": "<response>"}})

Tool returns diff. Confirm:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "accept_proposed"}})

SPECIAL CASES:
- If user says "this event shouldn't exist / is deprecated" — call submit_answer \
  with that info; the tool will mark status=deprecated and add note.
- If user describes a new event not in catalog — submit and the tool will create \
  it as an adjacent event for later refinement.
- If user says "this event needs another parameter called X" — ask for X's type, \
  source, and required status, then submit the full answer.

DONE CRITERIA for this event: Business purpose filled + at least trigger type \
confirmed. Parameters can have remaining [TODO]s marked "low-priority".

AFTER COMPLETING THIS EVENT: The tool advances to the next event in queue. If the \
user wants to jump to a specific event, they can say "let's do [event_name]" and \
you call goto_section("event_catalog.[event_name]")."""


def _instructions_user_properties(sdr_id: str, parsed: ParsedSDR) -> str:
    existing_props = parsed.user_properties or ""
    return f"""SECTION: User Properties / Custom Dimensions

Document the custom user properties and dimensions tracked across platforms. These \
are the traits that describe your users beyond what events capture — things like \
user_type, subscription_tier, customer_lifetime_value, acquisition_channel.

WHAT'S IN THE DOC NOW:
{existing_props[:300] if existing_props else "[empty]"}

YOUR JOB: For each user property, capture:
- Name (exact as used in dataLayer/GA4)
- Scope: user-level (persists), event-level (per-hit), or session-level
- Source: where the value comes from (dataLayer, backend API, CRM sync, etc.)
- Example values
- Which platforms receive it (GA4, Meta, etc.)

HOW TO ASK:
  "Do you track any custom user properties or dimensions? Common ones include \
  user_type (new vs returning), customer_tier (free/pro/enterprise), \
  subscription_status, or acquisition_channel. Any of those ring a bell?"

If they confirm:
  "Great — for each one, I need: the exact name as it appears in your analytics, \
  whether it's user-scoped or event-scoped, and where the value comes from."

If they say "I don't think so" or aren't sure:
  "No worries. I'll mark this section as 'none configured' and we can add them \
  later if you discover any. Want to move on?"

HOW TO USE ANSWERS:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "submit_answer", "user_input": "<response>"}})

DONE CRITERIA: At least acknowledge user properties exist or don't. If they exist, \
capture at least name + scope for each."""


def _instructions_destinations(sdr_id: str, parsed: ParsedSDR) -> str:
    total = len(parsed.events)
    # Find events missing destinations or with only one
    sparse_events = [
        e.name
        for e in parsed.events
        if len(e.destinations) <= 1
        and e.name in ("purchase", "sign_up", "subscribe", "form_submit", "add_to_cart")
    ]
    sparse_str = ", ".join(f"`{n}`" for n in sparse_events[:5]) if sparse_events else "none flagged"

    return f"""SECTION: Destinations Matrix

Cross-reference which events fire to which platforms. This matrix is the heart of \
multi-platform tag management — it answers "where does each event go?"

The SDR has {total} events. Events with sparse destination mappings: {sparse_str}

YOUR JOB: For each major event, confirm which platforms receive it:
- GA4 (always? same event name or mapped?)
- Google Ads (which events are Ads conversions?)
- Meta Pixel (which events? what's the Meta event name mapping?)
- TikTok, LinkedIn, Snap (if applicable)
- Any custom destinations (Segment, mParticle, etc.)

HOW TO ASK:
  "Let's make sure each event reaches the right platforms. I see some events only \
  going to GA4 — for instance, {sparse_str if sparse_events else "your conversion events"}. \
  Should any of those also fire to Google Ads, Meta, or other ad platforms?"

Then for each platform the user adds:
  "For the events going to [platform], do they use the same event name, or does \
  the platform expect a different name? For example, Meta uses 'Purchase' not \
  'purchase', and 'ViewContent' not 'view_item'."

HOW TO USE ANSWERS:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "submit_answer", "user_input": "<response>"}})

DONE CRITERIA: Major conversion events have at least 1 destination confirmed. \
The full matrix can be refined later."""


def _instructions_consent(sdr_id: str, parsed: ParsedSDR) -> str:
    consent_content = parsed.consent_and_privacy or ""
    return f"""SECTION: Consent & Privacy

Document consent management: what CMP is used, which consent categories exist, \
and which tags are gated by which consent signal. This is critical for GDPR/CCPA \
compliance and ensures audits can verify consent-gating is correct.

WHAT'S IN THE DOC NOW:
{consent_content[:300] if consent_content else "[empty]"}

YOUR JOB: Capture:
1. CMP identity — Cookiebot, OneTrust, Google Consent Mode, custom, or "none"
2. Consent categories — analytics_storage, ad_storage, functionality_storage, etc.
3. Default consent state — granted or denied by default? Regional variations?
4. Gating rules — which specific tags are conditional on which consent?

HOW TO ASK:
  "Do you have a Consent Management Platform (CMP) in place? Common ones are \
  Cookiebot, OneTrust, or Google's built-in Consent Mode."

If yes:
  "Which consent categories do you use? The standard GA4 ones are analytics_storage \
  and ad_storage — do you use those, or custom categories?"

  "What's the default consent state? Is everything denied until the user opts in \
  (common in EU), or granted by default with opt-out (common in US)?"

If no CMP:
  "Good to know. I'll note that in the SDR. Do you have plans to implement one, \
  or is consent management not a priority right now?"

HOW TO USE ANSWERS:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "submit_answer", "user_input": "<response>"}})

DONE CRITERIA: CMP identified (or "none") + default consent state documented. \
Detailed gating rules are nice-to-have for MVP."""


def _instructions_ownership(sdr_id: str, parsed: ParsedSDR) -> str:
    return f"""SECTION: Ownership & Governance

Define who owns different parts of the analytics implementation. This is the \
accountability layer — when something breaks or needs changing, this section \
answers "who do I talk to?"

YOUR JOB: Capture:
1. Overall SDR owner (who's the single accountable person?)
2. Business owners per area (ecommerce events → marketing team, etc.)
3. Technical owners per area (tag implementation → dev team, etc.)
4. Change process — how do changes to the SDR get proposed and approved?
5. Review cadence — quarterly? On major releases? Ad hoc?

HOW TO ASK:
  "Who's responsible for maintaining the analytics setup? Is there a dedicated \
  analytics team, or does it live with marketing/engineering?"

Follow up:
  "And who should approve changes to this SDR — is that the same person, or \
  does it need sign-off from someone else?"

Then cadence:
  "How often should this SDR be reviewed? Some teams do quarterly reviews, \
  others update it with every major release."

HOW TO USE ANSWERS:
  tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "submit_answer", "user_input": "<response>"}})

DONE CRITERIA: At least overall SDR owner identified + review cadence set."""


def _instructions_review(sdr_id: str, parsed: ParsedSDR) -> str:
    total_events = len(parsed.events)
    todo_count = len(parsed.todo_markers)
    verified = sum(1 for e in parsed.events if e.status == "verified")
    implemented = sum(1 for e in parsed.events if e.status == "implemented")
    planned = sum(1 for e in parsed.events if e.status == "planned")
    dest_count = sum(len(e.destinations) for e in parsed.events)

    return f"""SECTION: Review and Finalize

The user has reached the review stage. Here's the current state:

SDR SUMMARY:
- {total_events} events documented ({implemented} implemented, {verified} verified, {planned} planned)
- {dest_count} destination mappings across all events
- {todo_count} [TODO]s remaining

YOUR JOB: Walk the user through the full doc and let them finalize.

HOW TO PRESENT:
1. Summarize what's in the doc:
   "Your SDR now has {total_events} events, covers your key user journeys, maps to \
   {dest_count} destinations, and documents your consent setup. {todo_count} TODOs \
   remain in areas you skipped or deferred."

2. Offer review paths:
   "Want to review the whole doc, jump to the TODOs, or go straight to approval? \
   Heads up — only project admins can approve a version."

3. If they pick "review":
   Call tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "show_status"}}) and present the \
   document nicely. Let them ask questions, propose edits, then loop back here.

4. If they pick "approve":
   Confirm they understand what approval means: "Approving snapshots this as v1.0 — \
   audit tools will start validating against it. Ready?"
   Then call:
     tracking_plan(action="refine", params={{"sdr_id": "{sdr_id}", "action": "finalize", "changelog_note": "<summary of changes>"}})

   After success, announce: "SDR v[N] approved and snapshotted. From now on, tag \
   audits will validate against this version."

5. If they want to defer:
   Say: "No rush. I'll hold the draft at its current state. Just say 'continue SDR' \
   anytime to pick up."

FINALIZATION GUARDRAILS:
- If user is not a project admin, the finalize call will error. Catch gracefully:
  "Looks like you're not a project admin — approval requires admin role. Want me to \
  let an admin know the SDR is ready for review?"
- If remaining TODOs are in required sections (business_context, at least 1 event, \
  destinations), the tool blocks finalization. Explain which TODOs must be resolved first.
- changelog_note is required for versions after 1.0. Ask the user to describe what changed.

POST-APPROVAL:
After successful finalize, say:
  "From now on, tag audits will validate against SDR v[N]. Any drift between \
  live implementation and this spec will show up in audit reports."
  Then offer: "Want to start working on changes for the next version, or are we done for now?"
"""


# ---------------------------------------------------------------------------
# Section excerpt extraction
# ---------------------------------------------------------------------------


def _get_section_excerpt(markdown: str, section: str) -> str:
    """Extract the relevant section from markdown for display."""
    section_headers = {
        "business_context": "## Business Context",
        "user_journeys": "## User Journeys",
        "data_layer_schema": "## Data Layer Schema",
        "event_catalog": "## Event Catalog",
        "user_properties": "## User Properties",
        "destinations_matrix": "## Destinations Matrix",
        "consent_and_privacy": "## Consent",
        "ownership": "## Ownership",
        "review_and_finalize": "## Changelog",
    }

    header = section_headers.get(section.split(".")[0], "")
    if not header:
        return markdown[:1000]

    idx = markdown.find(header)
    if idx == -1:
        return markdown[:1000]

    # Find next H2
    next_h2 = markdown.find("\n## ", idx + len(header))
    if next_h2 == -1:
        excerpt = markdown[idx:]
    else:
        excerpt = markdown[idx:next_h2]

    return excerpt[:2000]


def _get_section_options(sdr_id: str, section: str) -> list[dict]:
    """Build user_options for a section."""
    return [
        {"label": "Answer question", "action_args": {"action": "submit_answer"}},
        {"label": "Skip this section", "action_args": {"action": "skip_section"}},
        {"label": "Show full status", "action_args": {"action": "show_status"}},
        {"label": "Jump to another section", "action_args": {"action": "goto_section"}},
    ]


# ---------------------------------------------------------------------------
# Change application helpers
# ---------------------------------------------------------------------------


def _generate_proposed_changes(
    section: str,
    user_input: str,
    sdr: SDR,
    parsed: ParsedSDR,
) -> list[dict]:
    """
    Generate proposed markdown changes from user input.

    This is a structured change proposal — the actual markdown editing
    is done by Claude (the LLM) using this as guidance. The tool provides
    the change structure; Claude interprets user_input semantically.
    """
    changes = []

    if section == "business_context":
        changes.append(
            {
                "section_path": "business_context",
                "change_type": "modify",
                "from_value": parsed.business_context or "[TODO]",
                "to_value": user_input,
                "rationale": "User provided business context",
            }
        )
    elif section == "user_journeys":
        changes.append(
            {
                "section_path": "user_journeys",
                "change_type": "modify",
                "from_value": parsed.user_journeys or "[TODO]",
                "to_value": user_input,
                "rationale": "User defined user journeys",
            }
        )
    elif section == "data_layer_schema":
        changes.append(
            {
                "section_path": "data_layer_schema",
                "change_type": "modify",
                "from_value": parsed.data_layer_schema or "[TODO]",
                "to_value": user_input,
                "rationale": "User documented data layer conventions",
            }
        )
    elif section.startswith("event_catalog"):
        event_name = section.split(".", 1)[1] if "." in section else None
        changes.append(
            {
                "section_path": section,
                "change_type": "modify",
                "from_value": f"Event: {event_name or 'current'}",
                "to_value": user_input,
                "rationale": f"User provided details for event {event_name or 'catalog'}",
            }
        )
    elif section == "consent_and_privacy":
        changes.append(
            {
                "section_path": "consent_and_privacy",
                "change_type": "modify",
                "from_value": parsed.consent_and_privacy or "[TODO]",
                "to_value": user_input,
                "rationale": "User documented consent management",
            }
        )
    elif section == "ownership":
        changes.append(
            {
                "section_path": "ownership",
                "change_type": "modify",
                "from_value": parsed.ownership or "[TODO]",
                "to_value": user_input,
                "rationale": "User defined ownership and governance",
            }
        )
    else:
        changes.append(
            {
                "section_path": section,
                "change_type": "modify",
                "from_value": "[current content]",
                "to_value": user_input,
                "rationale": "User input for section",
            }
        )

    return changes


# Canonical H2 headers, keyed by refinement section. Must stay aligned with the
# headers emitted by app.tools.sdr_parser.generate_sdr_markdown and recognised by
# parse_sdr_markdown — otherwise applied changes silently fail to round-trip.
_SECTION_H2 = {
    "business_context": "## Business Context",
    "user_journeys": "## User Journeys",
    "data_layer_schema": "## Data Layer Schema",
    "event_catalog": "## Event Catalog",
    "user_properties": "## User Properties",
    "destinations_matrix": "## Destinations Matrix",
    "consent_and_privacy": "## Consent & Privacy",
    "ownership": "## Ownership & Governance",
}


def _find_section_body_span(markdown: str, header: str) -> tuple[int, int] | None:
    """Return (body_start, body_end) for an H2 section, excluding any trailing
    ``---`` separator and the next H2. Returns None if the header is absent."""
    idx = markdown.find(header)
    if idx == -1:
        return None
    body_start = markdown.find("\n", idx) + 1
    next_h2 = markdown.find("\n## ", body_start)
    next_sep = markdown.find("\n---", body_start)
    candidates = [pos for pos in (next_h2, next_sep) if pos != -1]
    body_end = min(candidates) if candidates else len(markdown)
    return body_start, body_end


def _find_event_block_span(catalog_body: str, event_name: str) -> tuple[int, int] | None:
    """Locate an ``### `event_name` `` block within an event-catalog body."""
    name = event_name.strip().strip("`")
    pattern = re.compile(r"^### +`?([^`\n]+)`?\s*$", re.MULTILINE)
    matches = list(pattern.finditer(catalog_body))
    for i, match in enumerate(matches):
        if match.group(1).strip() == name:
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(catalog_body)
            return start, end
    return None


def _apply_change_to_markdown(markdown: str, section_path: str, new_content: str, change_type: str) -> str:
    """Apply a structured change to the SDR markdown document.

    Honours ``change_type`` ("modify"/"replace", "append", "prepend") and supports
    targeting a single event via ``event_catalog.<event_name>``. Returns the
    document unchanged only when the target section genuinely cannot be located.
    """
    base_section = section_path.split(".")[0]
    header = _SECTION_H2.get(base_section)
    if not header:
        return markdown

    span = _find_section_body_span(markdown, header)
    if span is None:
        return markdown
    body_start, body_end = span
    body = markdown[body_start:body_end]
    change_type = (change_type or "modify").lower()

    # Event-scoped edit: event_catalog.<event_name>
    if base_section == "event_catalog" and "." in section_path:
        event_name = section_path.split(".", 1)[1]
        block_span = _find_event_block_span(body, event_name)
        if block_span is not None and change_type in ("modify", "replace"):
            bs, be = block_span
            new_body = body[:bs] + new_content.strip() + "\n\n" + body[be:].lstrip("\n")
            return markdown[:body_start] + new_body.rstrip("\n") + "\n" + markdown[body_end:]
        # New event (or append/prepend on a known one) → fall through to section append.
        change_type = "append"

    stripped = body.strip()
    placeholder_only = stripped == "" or (stripped.startswith("[TODO") and stripped.endswith("]"))

    if change_type == "append" and not placeholder_only:
        new_body = body.rstrip("\n") + "\n\n" + new_content.strip() + "\n"
    elif change_type == "prepend" and not placeholder_only:
        new_body = "\n" + new_content.strip() + "\n\n" + body.lstrip("\n")
    else:  # modify / replace, or writing over a placeholder
        new_body = "\n" + new_content.strip() + "\n"

    return markdown[:body_start] + new_body + "\n" + markdown[body_end:]


def _next_section_after(current: str) -> str:
    """Get the section after the given one in order."""
    base = current.split(".")[0]
    try:
        idx = SECTION_ORDER.index(base)
        if idx + 1 < len(SECTION_ORDER):
            return SECTION_ORDER[idx + 1]
    except ValueError:
        pass
    return "review_and_finalize"


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def _increment_version(current: str, changelog_note: str | None = None) -> str:
    """Increment version number. Major bump if changelog contains [major]."""
    parts = current.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return "1.0"

    if changelog_note and "[major]" in changelog_note.lower():
        return f"{major + 1}.0"
    return f"{major}.{minor + 1}"


def _update_frontmatter(markdown: str, **updates: Any) -> str:
    """Update specific frontmatter fields in the markdown."""
    import frontmatter as fm

    try:
        post = fm.loads(markdown)
        for key, value in updates.items():
            if value is not None:
                post.metadata[key] = value
        return fm.dumps(post)
    except Exception:
        return markdown
