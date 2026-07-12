"""build_deploy_proposal — turn a planned event into a pending GTM FluxDraft.

Produces a GA4 event-tag + custom-event-trigger proposal for a single planned
event and persists it as a ``FluxDraft`` in the *same payload shape* the Ask
``propose_change`` flow emits (see ``app/ask/harness.py:_gtm_draft_from_propose``),
so the resulting draft flows through the existing
``/api/ask/drafts/{id}/approve|reject`` endpoints unchanged.

Because a FluxDraft requires a conversation FK, we anchor the draft to a
lightweight conversation tagged ``origin_section='implement'`` owned by the
requesting user.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

import app.app_state as app_state
from app.ask.drafts import DraftService
from app.ask.service import ConversationService
from app.models.flux_draft import FluxDraft
from app.models.tracking_plan import TPPlan
from app.services.implementation.coverage import resolve_gtm_target
from app.services.tracking_plan.bootstrap import get_main_branch
from app.services.tracking_plan.serializer import plan_to_dict


class NoGTMConnectionError(Exception):
    """Raised when a deploy proposal is requested but the project has no live
    GTM container to target."""


async def _resolve_default_workspace(target: dict) -> str | None:
    """Resolve the default GTM workspace id for a container via the connector.

    Prefers a workspace literally named "Default Workspace"; otherwise the
    first one returned. Returns None when the connector can't be reached (the
    draft is still created; approve() falls back to a mock version)."""
    connector = getattr(app_state, "gtm_connector", None)
    if connector is None:
        return None
    try:
        resp = await connector.list_workspaces(
            target["connection_id"], target["account_id"], target["container_id"]
        )
    except Exception:
        return None
    workspaces = resp.get("workspaces", []) if isinstance(resp, dict) else []
    if not workspaces:
        return None
    for w in workspaces:
        if (w.get("name") or "").strip().lower() == "default workspace":
            return str(w.get("workspace_id"))
    return str(workspaces[0].get("workspace_id"))


def _build_ga4_tag_spec(event: dict, workspace_id: str | None) -> dict:
    """Shape a GA4 event tag + custom-event trigger config from a plan event."""
    event_name = event["name"]
    params = []
    for prop in event.get("properties", []):
        params.append(
            {
                "name": prop["name"],
                "value": prop.get("example") or f"{{{{DLV - {prop['name']}}}}}",
                "required": prop.get("required", False),
                "data_type": prop.get("data_type"),
            }
        )
    return {
        "tag": {
            "type": "gaawe",  # GA4 Event
            "name": f"GA4 Event — {event_name}",
            "event_name": event_name,
            "event_parameters": params,
        },
        "trigger": {
            "type": "customEvent",
            "name": f"CE — {event_name}",
            "event_filter": event_name,
        },
        "workspace_id": workspace_id,
    }


def _build_diff(event: dict, spec: dict) -> list[dict]:
    """A context-only diff describing what will be created."""
    event_name = event["name"]
    lines = [
        {"kind": "added", "text": f"+ GA4 Event tag  'GA4 Event — {event_name}'"},
        {"kind": "context", "text": f"    eventName: {event_name}"},
    ]
    for p in spec["tag"]["event_parameters"]:
        req = " (required)" if p["required"] else ""
        lines.append({"kind": "context", "text": f"    {p['name']}: {p['value']}{req}"})
    lines.append({"kind": "added", "text": f"+ Custom Event trigger  fires on '{event_name}'"})
    return lines


async def build_deploy_proposal(
    session: Any,
    project_id: uuid.UUID,
    event_id: str,
    user_id: uuid.UUID,
) -> FluxDraft:
    """Create a pending GTM FluxDraft deploying ``event_id`` from the plan.

    Raises ``NoGTMConnectionError`` when the project has no live GTM container,
    and ``ValueError`` when the event id isn't in the plan.
    """
    plan = (await session.execute(select(TPPlan).where(TPPlan.project_id == project_id))).scalar_one_or_none()
    if plan is None:
        raise ValueError("Project has no tracking plan.")

    branch = await get_main_branch(session, plan)
    data = await plan_to_dict(session, plan, branch, include_drift=False)

    event = next((e for e in data.get("events", []) if e["id"] == str(event_id)), None)
    if event is None:
        raise ValueError(f"Event {event_id} not found in plan.")

    # Idempotency: if a pending deploy draft already exists for this event
    # (e.g. the user reloaded the page and re-clicked, or a duplicate POST),
    # return it instead of staging a second draft + phantom conversation.
    existing = (
        (
            await session.execute(
                select(FluxDraft).where(
                    FluxDraft.project_id == project_id,
                    FluxDraft.status == "pending",
                    FluxDraft.kind == "gtm_workspace_change",
                )
            )
        )
        .scalars()
        .all()
    )
    for d in existing:
        if (d.payload or {}).get("event_id") == str(event_id):
            return d

    target = await resolve_gtm_target(session, project_id)
    if target is None:
        raise NoGTMConnectionError("No GTM connection — connect Google Tag Manager to deploy events.")

    workspace_id = await _resolve_default_workspace(target)
    spec = _build_ga4_tag_spec(event, workspace_id)
    diff = _build_diff(event, spec)

    event_name = event["name"]
    entity_type = "tag"
    entity_name = f"GA4 Event — {event_name}"
    change_type = "create"

    public_id = target.get("public_id") or target["container_id"]
    ws_bits = " · ".join(b for b in (public_id, f"workspace: {workspace_id}" if workspace_id else "") if b)
    proposal = (
        f"Create a GA4 Event tag firing '{event_name}' on a custom-event trigger, "
        f"with {len(spec['tag']['event_parameters'])} mapped parameter(s) from the tracking plan."
    )

    payload: dict[str, Any] = {
        "event_id": str(event_id),
        "workspace_label": ws_bits or "GTM workspace",
        "target": f"{entity_type.upper()}: {entity_name}",
        "diff": diff,
        "proposal": proposal,
        "proposed_config": spec,
        "change_type": change_type,
        "entity_type": entity_type,
        "entity_name": entity_name,
        "gtm": {
            "connection_id": target["connection_id"],
            "account_id": target["account_id"],
            "container_id": target["container_id"],
            "workspace_id": workspace_id,
        },
    }

    # Anchor the draft to a lightweight conversation so it flows through the
    # existing ask draft approve/reject endpoints (which key on conversation
    # ownership). Tagged origin_section='implement' so it's distinguishable.
    conv = await ConversationService().create(
        project_id=project_id,
        user_id=user_id,
        provider="fluxito",
        model="implement",
        origin_section="implement",
    )

    draft = await DraftService().create(
        project_id=project_id,
        conversation_id=conv.id,
        message_id=None,
        created_by=user_id,
        kind="gtm_workspace_change",
        title=f"{change_type.capitalize()} {entity_type} '{entity_name}'",
        payload=payload,
    )
    return draft
