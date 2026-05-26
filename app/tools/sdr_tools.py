"""
SDR MCP Tools — internal generate_sdr + refine_sdr handlers

Standalone internal handlers for creating and refining Solution Design References.
These are Layer 2 (Intelligence) handlers dispatched by the ``tracking_plan``
unified tool (via ``app/tools/unified.py``), operating on SDR markdown documents
and using existing connectors for bootstrap data.

Architecture:
  - generate_sdr: Bootstrap a draft SDR from live GA4/GTM/Ads config + templates
    (dispatched via tracking_plan(action="generate", params={...}))
  - refine_sdr: Section-by-section conversational refinement state machine
    (dispatched via tracking_plan(action="refine", params={...}))
"""

from __future__ import annotations

import asyncio
import logging
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
    SDRRefinementState,
    SDRVersion,
)
from app.tools.sdr_parser import (
    ParsedDestination,
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
        regenerate: bool = False,
    ) -> dict:
        """
        Bootstrap a Solution Design Reference (SDR) from live platform config.

        Reads your connected GA4 properties, GTM containers, and Google Ads
        accounts to discover existing events, tags, and conversions. Merges
        with an industry template to produce a comprehensive draft SDR in
        Markdown format.

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

        Returns a summary with gap analysis and instructions for refinement.
        """
        # --- Auth & project context ---
        project_ctx = require_project_ctx()
        if not project_ctx:
            return no_active_project_response()

        user_ctx = state.current_user_ctx.get()
        if not user_ctx:
            return {"error": True, "error_type": "unauthenticated", "message": "No active session."}

        target_project_id = project_id or project_ctx.project_id
        project_name = project_ctx.project_name
        sdr_name = name or f"{project_name} SDR"

        # --- Check existing SDR ---
        db_factory = state.db_session_factory
        async with db_factory() as db:
            existing = await db.execute(select(SDR).where(SDR.project_id == _uuid.UUID(target_project_id)))
            existing_sdr = existing.scalar_one_or_none()

            if existing_sdr and not regenerate:
                parsed = parse_sdr_markdown(existing_sdr.markdown_content)
                gaps = compute_gaps(parsed)
                return {
                    "sdr_id": str(existing_sdr.id),
                    "status": existing_sdr.status,
                    "message": 'An SDR already exists for this project. Set regenerate=true to overwrite, or use tracking_plan(action="refine") to continue editing.',
                    "summary": {
                        "events_discovered": len(parsed.events),
                        "todo_count": len(parsed.todo_markers),
                    },
                    "gaps": gaps[:10],
                    "instructions_for_claude": (
                        f'An SDR already exists for project "{project_name}" with '
                        f"{len(parsed.events)} events. Ask the user if they want to "
                        "continue refining it (call tracking_plan with action='refine' and params={'sdr_id': '<id>', 'action': 'resume'}) "
                        "or regenerate from scratch (call tracking_plan with action='generate' and params={'regenerate': true})."
                    ),
                }

            # --- Bootstrap: gather data from connectors ---
            discovered_events: list[ParsedEvent] = []
            gtm_tags_mapped = 0
            ga4_custom_events = 0
            ads_conversions = 0
            user_properties_count = 0
            consent_detected = False
            partial_sources: list[str] = []

            # Fetch all three sources in parallel — each is independent network I/O
            _bootstrap_tasks: list = []
            _bootstrap_keys: list[str] = []
            if sources_ga4 and project_ctx.has_ga4:
                _bootstrap_tasks.append(_bootstrap_ga4(project_ctx))
                _bootstrap_keys.append("ga4")
            if sources_gtm and project_ctx.has_gtm:
                _bootstrap_tasks.append(_bootstrap_gtm(project_ctx))
                _bootstrap_keys.append("gtm")
            if sources_ads and project_ctx.has_ads:
                _bootstrap_tasks.append(_bootstrap_ads(project_ctx))
                _bootstrap_keys.append("ads")

            _bootstrap_results: dict = {}
            if _bootstrap_tasks:
                _outcomes = await asyncio.gather(*_bootstrap_tasks, return_exceptions=True)
                _bootstrap_results = dict(zip(_bootstrap_keys, _outcomes, strict=False))

            # Merge in order: GA4 → GTM → Ads (GTM/Ads merge into the GA4 base)
            if "ga4" in _bootstrap_results:
                _r = _bootstrap_results["ga4"]
                if isinstance(_r, Exception):
                    logger.warning(f"GA4 bootstrap partial failure: {_r}")
                    partial_sources.append(f"GA4: {_r}")
                else:
                    ga4_events, ga4_dims, ga4_convs = _r
                    discovered_events.extend(ga4_events)
                    ga4_custom_events = len(ga4_events)
                    user_properties_count = len(ga4_dims)

            if "gtm" in _bootstrap_results:
                _r = _bootstrap_results["gtm"]
                if isinstance(_r, Exception):
                    logger.warning(f"GTM bootstrap partial failure: {_r}")
                    partial_sources.append(f"GTM: {_r}")
                else:
                    gtm_events, gtm_consent = _r
                    discovered_events = _merge_events(discovered_events, gtm_events)
                    gtm_tags_mapped = len(gtm_events)
                    consent_detected = gtm_consent

            if "ads" in _bootstrap_results:
                _r = _bootstrap_results["ads"]
                if isinstance(_r, Exception):
                    logger.warning(f"Ads bootstrap partial failure: {_r}")
                    partial_sources.append(f"Google Ads: {_r}")
                else:
                    ads_events = _r
                    discovered_events = _merge_events(discovered_events, ads_events)
                    ads_conversions = len(ads_events)

            # --- Apply industry template ---
            btype = business_type_hint or _infer_business_type(discovered_events)
            template_events = get_industry_template(btype)
            discovered_events = _merge_with_template(discovered_events, template_events)

            # --- Determine confidence ---
            total_events = len(discovered_events)
            todo_events = sum(1 for e in discovered_events if not e.purpose or "[TODO" in (e.purpose or ""))
            if total_events == 0 or todo_events / max(total_events, 1) > 0.5:
                confidence = "low"
            elif todo_events / max(total_events, 1) > 0.2:
                confidence = "medium"
            else:
                confidence = "high"

            # --- Generate markdown ---
            markdown = generate_sdr_markdown(
                project_name=project_name,
                project_id=target_project_id,
                business_type=btype,
                events=discovered_events,
            )

            # --- Save to DB ---
            user_id = _uuid.UUID(str(user_ctx.user_id))

            if existing_sdr and regenerate:
                existing_sdr.name = sdr_name
                existing_sdr.markdown_content = markdown
                existing_sdr.status = "draft"
                existing_sdr.updated_at = datetime.now(UTC)
                sdr = existing_sdr
            else:
                sdr = SDR(
                    project_id=_uuid.UUID(target_project_id),
                    name=sdr_name,
                    status="draft",
                    markdown_content=markdown,
                    created_by=user_id,
                )
                db.add(sdr)

            await db.flush()

            # Rebuild projections
            await rebuild_projections_async(db, sdr)

            # Create initial refinement state
            existing_ref = await db.execute(
                select(SDRRefinementState).where(SDRRefinementState.sdr_id == sdr.id)
            )
            ref_state = existing_ref.scalar_one_or_none()
            if ref_state:
                ref_state.current_section = "business_context"
                ref_state.sections_completed = []
                ref_state.pending_proposed_changes = None
                ref_state.last_activity_at = datetime.now(UTC)
            else:
                ref_state = SDRRefinementState(
                    sdr_id=sdr.id,
                    current_section="business_context",
                    sections_completed=[],
                    last_activity_at=datetime.now(UTC),
                )
                db.add(ref_state)

            await db.commit()

            # --- Compute gaps ---
            parsed = parse_sdr_markdown(markdown)
            gaps = compute_gaps(parsed)

            # --- Build response ---
            preview = markdown[:1000] + ("..." if len(markdown) > 1000 else "")

            gap_summary = []
            for g in gaps[:10]:
                gap_summary.append(
                    {
                        "category": g["category"],
                        "description": g["description"],
                        "suggested_section": g["suggested_section"],
                    }
                )

            instructions = _build_generate_instructions(
                project_name=project_name,
                sdr_id=str(sdr.id),
                total_events=total_events,
                gtm_tags_mapped=gtm_tags_mapped,
                ga4_custom_events=ga4_custom_events,
                ads_conversions=ads_conversions,
                confidence=confidence,
                gaps=gap_summary,
                partial_sources=partial_sources,
            )

            return {
                "sdr_id": str(sdr.id),
                "version": "0.1-draft",
                "status": "draft",
                "summary": {
                    "events_discovered": total_events,
                    "gtm_tags_mapped": gtm_tags_mapped,
                    "ga4_custom_events": ga4_custom_events,
                    "ads_conversions": ads_conversions,
                    "user_properties": user_properties_count,
                    "consent_behavior_detected": consent_detected,
                    "confidence": confidence,
                    "gaps": gap_summary,
                },
                "document_preview": preview,
                "needs_refinement": True,
                "recommended_next_action": f"tracking_plan(action='refine', params={{'sdr_id': '{sdr.id}', 'action': 'resume'}})",
                "instructions_for_claude": instructions,
                "partial_sources": partial_sources if partial_sources else None,
            }

    # ==================================================================
    # refine_sdr
    # ==================================================================

    @mcp_server.tool("refine_sdr")
    async def refine_sdr(
        sdr_id: str,
        action: str = "resume",
        section: str | None = None,
        user_input: str | None = None,
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
                finalize        — snapshot as new approved version (admin only)
                start_new_draft — begin editing after approval
            section: Target section for goto_section (e.g., "event_catalog.purchase").
            user_input: Natural language user response for submit_answer.
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
# Bootstrap helpers
# ---------------------------------------------------------------------------


async def _bootstrap_ga4(project_ctx: Any) -> tuple[list[ParsedEvent], list[dict], list[dict]]:
    """Bootstrap events from GA4 connector."""
    ga4 = state.ga4_connector
    conn_id = None
    for c in project_ctx.connections:
        if c.provider == "google":
            conn_id = c.id
            break
    if not conn_id:
        return [], [], []

    events: list[ParsedEvent] = []
    custom_dims: list[dict] = []
    conversions: list[dict] = []

    # Get GA4 properties for this project
    for prop in project_ctx.ga4_properties:
        prop_id = prop.get("property_id") or prop.get("id")
        if not prop_id:
            continue

        # Custom events / conversion events
        try:
            conv_result = await ga4.get_conversion_events(conn_id, prop_id)
            conv_events = conv_result.get("conversion_events") or conv_result.get("events") or []
            for ce in conv_events:
                event_name = ce.get("event_name") or ce.get("name", "")
                if event_name:
                    events.append(
                        ParsedEvent(
                            name=event_name,
                            purpose=f"GA4 conversion event (auto-discovered from property {prop_id})",
                            status="implemented",
                            destinations=[
                                ParsedDestination(
                                    platform="ga4",
                                    platform_account_id=str(prop_id),
                                    dest_event_name=event_name,
                                )
                            ],
                        )
                    )
                    conversions.append(ce)
        except Exception as e:
            logger.debug(f"GA4 conversion list failed for {prop_id}: {e}")

        # Custom dimensions
        try:
            dims_result = await ga4.list_custom_dimensions(conn_id, prop_id)
            dims = dims_result.get("custom_dimensions") or dims_result.get("dimensions") or []
            custom_dims.extend(dims)
        except Exception as e:
            logger.debug(f"GA4 custom dimensions failed for {prop_id}: {e}")

    return events, custom_dims, conversions


async def _bootstrap_gtm(project_ctx: Any) -> tuple[list[ParsedEvent], bool]:
    """Bootstrap events from GTM connector."""
    gtm = state.gtm_connector
    conn_id = None
    for c in project_ctx.connections:
        if c.provider == "google":
            conn_id = c.id
            break
    if not conn_id:
        return [], False

    events: list[ParsedEvent] = []
    consent_detected = False

    for container in project_ctx.gtm_containers:
        account_id = container.get("account_id")
        container_id = container.get("container_id")
        if not account_id or not container_id:
            continue

        try:
            tags_result = await gtm.list_tags(conn_id, account_id, container_id)
            tags = tags_result.get("tags") or []

            triggers_result = await gtm.list_triggers(conn_id, account_id, container_id)
            triggers = triggers_result.get("triggers") or []
            trigger_map = {t.get("triggerId"): t for t in triggers if t.get("triggerId")}

            for tag in tags:
                tag_name = tag.get("name", "")
                tag_type = tag.get("type", "")

                # Skip built-in / consent tags
                if tag_type in ("cvt_", "consent") or "consent" in tag_name.lower():
                    consent_detected = True
                    continue

                # Infer event name from tag
                event_name = _infer_event_name_from_tag(tag)
                if not event_name:
                    continue

                # Infer trigger type
                trigger_type = "custom"
                trigger_config = {}
                firing_ids = tag.get("firingTriggerId") or []
                for tid in firing_ids:
                    trig = trigger_map.get(tid)
                    if trig:
                        trig_type = trig.get("type", "").lower()
                        if "pageview" in trig_type:
                            trigger_type = "pageview"
                        elif "click" in trig_type:
                            trigger_type = "click"
                        elif "form" in trig_type:
                            trigger_type = "form_submit"
                        elif "custom" in trig_type or "event" in trig_type:
                            trigger_type = "datalayer_event"
                        trigger_config["trigger_name"] = trig.get("name")
                        break

                # Determine destination platform
                dest_platform = "ga4"
                if (
                    "ads" in tag_type.lower()
                    or "awct" in tag_type.lower()
                    or "floodlight" in tag_type.lower()
                ):
                    dest_platform = "google_ads"

                events.append(
                    ParsedEvent(
                        name=event_name,
                        purpose=f"Discovered from GTM tag '{tag_name}' (type: {tag_type})",
                        trigger_type=trigger_type,
                        trigger_config=trigger_config if trigger_config else None,
                        status="implemented",
                        destinations=[
                            ParsedDestination(
                                platform=dest_platform,
                                dest_event_name=event_name,
                            )
                        ],
                    )
                )
        except Exception as e:
            logger.debug(f"GTM bootstrap failed for container {container_id}: {e}")

    return events, consent_detected


async def _bootstrap_ads(project_ctx: Any) -> list[ParsedEvent]:
    """Bootstrap conversion events from Google Ads connector."""
    events: list[ParsedEvent] = []

    try:
        ads = state.ads_connector
        if not ads:
            return events
    except (AttributeError, LookupError):
        return events

    conn_id = None
    for c in project_ctx.connections:
        if c.provider == "google":
            conn_id = c.id
            break
    if not conn_id:
        return events

    for acct in project_ctx.ads_accounts:
        customer_id = acct.get("customer_id")
        if not customer_id:
            continue

        try:
            conv_result = await ads.get_conversion_actions(conn_id, customer_id)
            conv_actions = conv_result.get("conversion_actions") or conv_result.get("conversions") or []
            for conv in conv_actions:
                conv_name = conv.get("name", "")
                if conv_name:
                    event_name = conv_name.lower().replace(" ", "_").replace("-", "_")
                    events.append(
                        ParsedEvent(
                            name=event_name,
                            purpose=f"Google Ads conversion action '{conv_name}'",
                            status="implemented",
                            destinations=[
                                ParsedDestination(
                                    platform="google_ads",
                                    platform_account_id=str(customer_id),
                                    dest_event_name=conv_name,
                                )
                            ],
                        )
                    )
        except Exception as e:
            logger.debug(f"Ads bootstrap failed for {customer_id}: {e}")

    return events


def _infer_event_name_from_tag(tag: dict) -> str | None:
    """Try to extract a clean event name from a GTM tag config."""
    tag_name = tag.get("name", "")

    # Check parameters for event_name / eventName
    params = tag.get("parameter") or []
    for p in params:
        key = p.get("key", "")
        val = p.get("value", "")
        if key in ("eventName", "event_name", "event") and val:
            return val.lower().replace(" ", "_")

    # Fallback: derive from tag name
    # "GA4 - Purchase Event" → "purchase_event"
    name = tag_name
    # Strip common prefixes
    for prefix in ("GA4 -", "GA4-", "GA4:", "UA -", "UA-", "Ads -", "Ads:"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    name = name.strip().lower().replace(" ", "_").replace("-", "_")
    # Skip very generic names
    if name in ("", "tag", "pixel", "script", "html", "custom_html"):
        return None
    return name


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


def _merge_events(existing: list[ParsedEvent], new: list[ParsedEvent]) -> list[ParsedEvent]:
    """Merge two event lists, combining events with the same name."""
    by_name: dict[str, ParsedEvent] = {e.name: e for e in existing}

    for event in new:
        if event.name in by_name:
            # Merge destinations
            existing_platforms = {d.platform for d in by_name[event.name].destinations}
            for d in event.destinations:
                if d.platform not in existing_platforms:
                    by_name[event.name].destinations.append(d)
            # Merge trigger info if missing
            if not by_name[event.name].trigger_type and event.trigger_type:
                by_name[event.name].trigger_type = event.trigger_type
                by_name[event.name].trigger_config = event.trigger_config
            # Merge parameters
            existing_params = {p.name for p in by_name[event.name].parameters}
            for p in event.parameters:
                if p.name not in existing_params:
                    by_name[event.name].parameters.append(p)
        else:
            by_name[event.name] = event

    return list(by_name.values())


def _merge_with_template(discovered: list[ParsedEvent], template: list[ParsedEvent]) -> list[ParsedEvent]:
    """Merge template events with discovered events. Template fills gaps."""
    discovered_names = {e.name for e in discovered}

    for tmpl_event in template:
        if tmpl_event.name not in discovered_names:
            # Template event not found in live config — add with TODO markers
            tmpl_event.status = "planned"
            tmpl_event.purpose = (
                f"[TODO: confirm] {tmpl_event.purpose or ''} "
                "(added from industry template — not found in live implementation)"
            )
            discovered.append(tmpl_event)
        else:
            # Found in live config — enrich with template data where missing
            for d in discovered:
                if d.name == tmpl_event.name:
                    if not d.parameters and tmpl_event.parameters:
                        d.parameters = tmpl_event.parameters
                    if not d.purpose or "[TODO" in d.purpose:
                        d.purpose = tmpl_event.purpose
                    break

    return discovered


# ---------------------------------------------------------------------------
# generate_sdr instructions builder
# ---------------------------------------------------------------------------


def _build_generate_instructions(
    *,
    project_name: str,
    sdr_id: str,
    total_events: int,
    gtm_tags_mapped: int,
    ga4_custom_events: int,
    ads_conversions: int,
    confidence: str,
    gaps: list[dict],
    partial_sources: list[str],
) -> str:
    """Build the instructions_for_claude field for generate_sdr response."""
    parts = [
        f'The SDR draft has been generated for project "{project_name}".',
        "",
        "Summary to share with the user:",
        f"- Discovered {total_events} events across {gtm_tags_mapped} GTM tags + "
        f"{ga4_custom_events} GA4 custom events + {ads_conversions} Ads conversions",
        f"- Confidence: {confidence}",
    ]

    if partial_sources:
        parts.append(f"- Note: some sources had issues: {'; '.join(partial_sources)}")

    if gaps:
        parts.append("")
        parts.append("Surface these gaps to the user and recommend starting refinement:")
        for i, gap in enumerate(gaps[:5], 1):
            parts.append(f"{i}. {gap['description']} (section: {gap['suggested_section']})")

    parts.extend(
        [
            "",
            f"Say: \"I've drafted your SDR from your live setup. Found {total_events} events "
            f"with {confidence} confidence. There are {len(gaps)} gaps that need your input — "
            'want me to walk through them? I can also start with business context if you prefer."',
            "",
            f"If user agrees to continue, call: tracking_plan(action='refine', params={{'sdr_id': '{sdr_id}', 'action': 'resume'}})",
        ]
    )

    return "\n".join(parts)


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


def _apply_change_to_markdown(markdown: str, section_path: str, new_content: str, change_type: str) -> str:
    """Apply a change to the SDR markdown document."""
    section_headers = {
        "business_context": "## Business Context",
        "user_journeys": "## User Journeys",
        "data_layer_schema": "## Data Layer Schema",
        "user_properties": "## User Properties",
        "destinations_matrix": "## Destinations Matrix",
        "consent_and_privacy": "## Consent & Privacy",
        "ownership": "## Ownership & Governance",
    }

    base_section = section_path.split(".")[0]
    header = section_headers.get(base_section)

    if not header:
        return markdown  # Can't apply without knowing the section

    idx = markdown.find(header)
    if idx == -1:
        return markdown

    # Find the content between this header and the next H2 or ---
    header_end = markdown.find("\n", idx) + 1
    next_h2 = markdown.find("\n## ", header_end)
    # Also look for --- separator
    next_sep = markdown.find("\n---", header_end)

    if next_h2 == -1:
        end_idx = len(markdown)
    elif next_sep != -1 and next_sep < next_h2:
        end_idx = next_sep
    else:
        end_idx = next_h2

    # Replace the section content
    new_markdown = markdown[:header_end] + "\n" + new_content + "\n\n" + markdown[end_idx:]
    return new_markdown


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
