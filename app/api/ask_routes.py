"""HTTP surface for Ask Fluxito: page, SSE chat stream, conversation CRUD, key setup."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from app import app_state
from app.ask.context import window_history
from app.ask.drafts import DraftPublishError, DraftService, draft_to_stream_payload
from app.ask.harness import Harness, HarnessDeps
from app.ask.keys import (
    delete_key,
    get_active_key,
    get_default_key,
    list_effective_keys,
    list_providers,
    set_default,
    store_key,
    update_key_meta,
)
from app.ask.prompts import build_system_prompt
from app.ask.providers.base import LLMMessage, StreamEvent, TextBlock
from app.ask.providers.registry import SUPPORTED_PROVIDERS, default_model_for, make_provider
from app.ask.service import ConversationService
from app.ask.tools import AskToolBridge
from app.auth.uid_cookie import get_uid_from_request
from app.templating import render

router = APIRouter()
_service = ConversationService()
_drafts = DraftService()


def _sse_frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _require_user_id(request: Request) -> str | None:
    return get_uid_from_request(request)


def _active_project_id(request: Request) -> str | None:
    # The active_project_id cookie is the source every /api route uses (nav
    # middleware only populates request.state for page renders, not /api POSTs).
    from app.api.project_routes import get_active_project_id

    return get_active_project_id(request) or getattr(request.state, "active_project_id", None)


# Sections the chat can be opened from (page_context.section). Anything else is
# ignored — an unknown section falls back to the default read-only surface.
_VALID_SECTIONS: frozenset[str] = frozenset(
    {"home", "plan", "implement", "audit", "report", "context", "settings"}
)


def _parse_page_context(raw: Any) -> dict[str, Any] | None:
    """Validate an optional page_context {section, route, entity} from the
    request body. Returns a normalized dict, or None when absent/invalid.
    An invalid section is dropped rather than rejected."""
    if not isinstance(raw, dict):
        return None
    section = raw.get("section")
    if not isinstance(section, str) or section not in _VALID_SECTIONS:
        section = None
    route = raw.get("route")
    entity = raw.get("entity")
    ctx: dict[str, Any] = {}
    if section:
        ctx["section"] = section
    if isinstance(route, str):
        ctx["route"] = route
    if isinstance(entity, dict):
        ctx["entity"] = entity
    return ctx or None


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    """Parse a user-supplied UUID string; None if missing or malformed."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


# ---- page ---------------------------------------------------------------


@router.get("/ask")
async def ask_page(request: Request):
    from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx

    user_ctx = await _resolve_user_ctx(request)
    if not user_ctx:
        return RedirectResponse("/signin?next=/ask", status_code=302)
    user_view = await _load_user_view(user_ctx)
    return render(request, "ask.html", {"page_title": "Ask Fluxito", "active": "ask", "user": user_view})


@router.get("/settings/ai")
async def ai_settings(request: Request):
    uid = _require_user_id(request)
    if not uid:
        return RedirectResponse("/signin?next=/settings/ai", status_code=302)
    from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx

    user_ctx = await _resolve_user_ctx(request)
    user_view = await _load_user_view(user_ctx) if user_ctx else None
    return render(request, "settings/ai.html", {"user": user_view})


# ---- chat stream --------------------------------------------------------


@router.post("/api/ask/stream")
async def ask_stream(request: Request):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    from app.api.project_routes import ensure_active_project

    project_id = await ensure_active_project(request, uid)
    if not project_id:
        return JSONResponse({"error": "No active project."}, status_code=400)

    body = await request.json()
    user_text = (body.get("message") or "").strip()
    if not user_text:
        return JSONResponse({"error": "Empty message."}, status_code=400)
    conv_id = body.get("conversation_id")
    page_context = _parse_page_context(body.get("page_context"))
    section = (page_context or {}).get("section")

    pid = uuid.UUID(project_id)
    uuid_user = uuid.UUID(uid)

    # Pick a provider that has a stored key (prefer one named in the body).
    available = await list_providers(project_id=pid, user_id=uuid_user)
    if not available:
        return JSONResponse(
            {"error": "no_key", "message": "Add an AI provider API key in settings first."},
            status_code=400,
        )
    explicit_provider = body.get("provider")
    if explicit_provider and explicit_provider in available:
        provider_name = explicit_provider
        key = await get_active_key(project_id=pid, user_id=uuid_user, provider=provider_name)
    else:
        key = await get_default_key(project_id=pid, user_id=uuid_user)
        provider_name = key.provider if key is not None else available[0]
    if key is None:
        return JSONResponse({"error": "no_key"}, status_code=400)
    model = key.default_model or default_model_for(provider_name)

    # Resolve or create the conversation.
    new_conv_title: str | None = None
    if conv_id:
        conv_uuid = _parse_uuid(conv_id)
        conv = await _service.get(conv_uuid) if conv_uuid else None
        if conv is None or str(conv.user_id) != uid:
            return JSONResponse({"error": "not_found"}, status_code=404)
    else:
        conv = await _service.create(
            project_id=pid,
            user_id=uuid_user,
            provider=provider_name,
            model=model,
            origin_section=section,
        )
        # Derive title from first user message (collapse whitespace, truncate to 60 chars).
        raw = " ".join(user_text.split())
        new_conv_title = raw[:60] + ("…" if len(raw) > 60 else "")
        await _service.set_title(conv.id, new_conv_title)

    history = window_history(await _service.load_history(conv.id))

    # Build the system prompt from the active project context.
    project_name = getattr(request.state, "active_project_name", "your project")
    # Resolve the caller's RBAC permissions so the tool surface (and every
    # dispatch) is gated by their role — not merely by the client-supplied
    # section. Without this, any member could unlock write tools by asserting
    # section="implement".
    from app.auth.permissions import resolve_effective_permissions

    eff = await resolve_effective_permissions(uid, project_id)
    bridge = AskToolBridge(user_id=uid, project_id=project_id, section=section, eff=eff)
    specs = bridge.tool_specs()
    connected = _connected_labels(specs)
    role = getattr(request.state, "active_project_role", "member")
    system = build_system_prompt(
        project_name=project_name,
        connected=connected,
        role=role,
        page_context=page_context,
    )

    provider = make_provider(provider_name, key.api_key, base_url=key.base_url)
    deps = HarnessDeps(
        provider=provider,
        bridge=bridge,
        service=_service,
        conversation_id=conv.id,
        model=model,
        system=system,
        history=history,
        drafts=_drafts,
        project_id=pid,
        created_by=uuid_user,
    )
    harness = Harness(deps)
    user_message = LLMMessage(role="user", content=[TextBlock(text=user_text)])

    async def event_stream():
        # First frame carries the conversation id so the client can persist it.
        # For new conversations, title is included so the sidebar can update immediately.
        # model + provider are always included so the client usage rail can display them.
        first_frame: dict = {
            "type": "conversation",
            "conversation_id": str(conv.id),
            "model": model,
            "provider": provider_name,
        }
        if new_conv_title:
            first_frame["title"] = new_conv_title
        yield _sse_frame(first_frame)
        try:
            async for ev in harness.run(user_message):
                yield _sse_frame(_event_to_payload(ev))
        except Exception as exc:  # never leak a stack trace into the stream
            yield _sse_frame({"type": "error", "error": f"{type(exc).__name__}"})
        finally:
            yield _sse_frame({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/ask/confirm-action")
async def confirm_action(request: Request):
    """Human-tap gate leftover from the retired card builder.

    action='discard' still flips a leftover card_preview block's state.
    action='add' is rejected: native JS cards are not deployed. Hosted
    dashboards are authored as Streamlit artifacts and deployed over MCP
    (get_dashboard_authoring_guide → deploy_dashboard → bind_dashboard).
    """
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)

    body = await request.json()
    conv_id = body.get("conversation_id")
    block_id = body.get("block_id")
    action = body.get("action")

    if not conv_id or not block_id or action not in ("add", "discard"):
        return JSONResponse(
            {"error": "conversation_id, block_id and action ('add'|'discard') are required."},
            status_code=400,
        )

    conv_uuid = _parse_uuid(str(conv_id))
    conv = await _service.get(conv_uuid) if conv_uuid else None
    if conv is None or str(conv.user_id) != uid:
        return JSONResponse({"error": "not_found"}, status_code=404)

    block = await _service.find_block(conv.id, str(block_id))
    if block is None or block.get("type") != "card_preview":
        return JSONResponse({"error": "Card preview not found in this conversation."}, status_code=404)

    if action == "discard":
        await _service.set_block_state(conv.id, str(block_id), "discarded")
        return JSONResponse({"status": "discarded"})

    return JSONResponse(
        {
            "error": (
                "Native card dashboards are retired. Deploy a hosted Streamlit "
                "artifact with get_dashboard_authoring_guide → "
                "validate_dashboard_artifact → deploy_dashboard → bind_dashboard."
            ),
            "error_type": "hosted_only",
        },
        status_code=400,
    )


def _event_to_payload(ev: StreamEvent) -> dict[str, Any]:
    payload = {k: v for k, v in asdict(ev).items() if v is not None}
    if ev.stop_reason is not None:
        payload["stop_reason"] = ev.stop_reason.value
    return payload


def _connected_labels(specs: list) -> list[str]:
    # Lightweight: surface which broad tool families are available as a proxy for
    # connected data sources, so the system prompt can mention them.
    fams = []
    names = {s.name for s in specs}
    if "analytics_read" in names:
        fams.append("Analytics")
    if "tagmanager_read" in names:
        fams.append("Tag Manager")
    if "marketing_read" in names:
        fams.append("Marketing")
    if "warehouse_read" in names:
        fams.append("Warehouse")
    if "seo_read" in names:
        fams.append("Search/SEO")
    return fams


# ---- conversation CRUD --------------------------------------------------


@router.get("/api/ask/conversations")
async def list_conversations(request: Request):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    project_id = _active_project_id(request)
    if not project_id:
        return JSONResponse({"conversations": []})
    convs = await _service.list_for(project_id=uuid.UUID(project_id), user_id=uuid.UUID(uid))
    return JSONResponse(
        {
            "conversations": [
                {
                    "id": str(c.id),
                    "title": c.title or "New chat",
                    "last_message_at": c.last_message_at.isoformat(),
                    "origin_section": c.origin_section,
                }
                for c in convs
            ]
        }
    )


@router.get("/api/ask/conversations/{conversation_id}")
async def get_conversation(request: Request, conversation_id: str):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    conv_uuid = _parse_uuid(conversation_id)
    conv = await _service.get(conv_uuid) if conv_uuid else None
    if conv is None or str(conv.user_id) != uid:
        return JSONResponse({"error": "not_found"}, status_code=404)
    history_with_usage = await _service.load_history_with_usage(conv.id)
    # Drafts (GTM diff cards etc.) attached to this conversation, in whatever
    # state (pending/published/rejected) they were left — the client matches
    # each on `message_id` to re-render the card at the right spot.
    drafts = await _drafts.list_for_conversation(conv.id)
    return JSONResponse(
        {
            "id": str(conv.id),
            "title": conv.title,
            "model": conv.model,
            "provider": conv.provider,
            "messages": [
                {
                    "id": str(msg_id),
                    "role": m.role,
                    "content": _blocks_for_ui(m),
                    **({"token_usage": usage} if usage is not None else {}),
                }
                for msg_id, m, usage in history_with_usage
            ],
            "drafts": [draft_to_stream_payload(d) for d in drafts],
        }
    )


def _blocks_for_ui(message: LLMMessage) -> list[dict[str, Any]]:
    from app.ask.providers.base import blocks_to_json

    return blocks_to_json(message.content)


# ---- draft approve / reject (Conversation approve flow) -----------------


async def _owned_draft(request: Request, draft_id: str):
    """Load a draft the current user is allowed to act on: the draft's
    conversation must belong to them. Returns (draft, error_response|None)."""
    uid = _require_user_id(request)
    if not uid:
        return None, JSONResponse({"error": "auth"}, status_code=401)
    draft_uuid = _parse_uuid(draft_id)
    if not draft_uuid:
        return None, JSONResponse({"error": "not_found"}, status_code=404)
    draft = await _drafts.get(draft_uuid)
    if draft is None:
        return None, JSONResponse({"error": "not_found"}, status_code=404)
    conv = await _service.get(draft.conversation_id)
    if conv is None or str(conv.user_id) != uid:
        return None, JSONResponse({"error": "not_found"}, status_code=404)
    return draft, None


@router.post("/api/ask/drafts/{draft_id}/approve")
async def approve_draft(request: Request, draft_id: str):
    draft, err = await _owned_draft(request, draft_id)
    if err:
        return err
    uid = _require_user_id(request)
    if draft.status != "pending":
        return JSONResponse(
            {"error": "not_pending", "draft": draft_to_stream_payload(draft)}, status_code=409
        )
    # Publishing to the live GTM container is a write action: gate it on the
    # caller's RBAC role for THIS draft's project, not just conversation
    # ownership. Otherwise a viewer / read-only member who staged the draft
    # could approve it into a live publish.
    from app.auth.permissions import resolve_effective_permissions

    eff = await resolve_effective_permissions(uid, str(draft.project_id))
    if not eff.allows_tool("tagmanager_write", action="publish_container"):
        return JSONResponse(
            {
                "error": "forbidden",
                "message": "You don't have permission to publish GTM changes in this project.",
            },
            status_code=403,
        )
    try:
        updated = await _drafts.approve(draft.id, user_id=uuid.UUID(uid))
    except DraftPublishError as exc:
        # Publish failed — draft stays pending so the user can retry.
        return JSONResponse(
            {"error": "publish_failed", "message": str(exc), "draft": draft_to_stream_payload(draft)},
            status_code=502,
        )
    return JSONResponse({"ok": True, "draft": draft_to_stream_payload(updated)})


@router.post("/api/ask/drafts/{draft_id}/reject")
async def reject_draft(request: Request, draft_id: str):
    draft, err = await _owned_draft(request, draft_id)
    if err:
        return err
    uid = _require_user_id(request)
    if draft.status != "pending":
        return JSONResponse(
            {"error": "not_pending", "draft": draft_to_stream_payload(draft)}, status_code=409
        )
    updated = await _drafts.reject(draft.id, user_id=uuid.UUID(uid))
    return JSONResponse({"ok": True, "draft": draft_to_stream_payload(updated)})


@router.post("/api/ask/drafts/{draft_id}/reset")
async def reset_draft(request: Request, draft_id: str):
    """Undo a rejection (design's "Undo" link) — back to pending."""
    draft, err = await _owned_draft(request, draft_id)
    if err:
        return err
    uid = _require_user_id(request)
    if draft.status != "rejected":
        return JSONResponse(
            {"error": "not_rejected", "draft": draft_to_stream_payload(draft)}, status_code=409
        )
    updated = await _drafts.reset(draft.id, user_id=uuid.UUID(uid))
    return JSONResponse({"ok": True, "draft": draft_to_stream_payload(updated)})


@router.post("/api/ask/conversations/{conversation_id}/archive")
async def archive_conversation(request: Request, conversation_id: str):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    conv_uuid = _parse_uuid(conversation_id)
    conv = await _service.get(conv_uuid) if conv_uuid else None
    if conv is None or str(conv.user_id) != uid:
        return JSONResponse({"error": "not_found"}, status_code=404)
    await _service.archive(conv.id)
    return JSONResponse({"ok": True})


# ---- minimal key setup --------------------------------------------------


async def _can_manage_project_keys(project_id: str, uid: str) -> bool:
    """Owners/admins may set the project-shared AI key."""
    from app.api.project_routes import _get_membership

    membership = await _get_membership(uuid.UUID(project_id), uuid.UUID(uid))
    return membership is not None and membership.role in ("owner", "admin")


@router.get("/api/ask/keys")
async def get_keys(request: Request):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    project_id = _active_project_id(request)
    if not project_id:
        return JSONResponse({"providers": [], "keys": [], "supported": list(SUPPORTED_PROVIDERS)})
    # Personal keys + project-shared keys the user hasn't overridden — the
    # same effective view the chat resolution uses.
    infos = await list_effective_keys(project_id=uuid.UUID(project_id), user_id=uuid.UUID(uid))
    providers = sorted({info.provider for info in infos})
    return JSONResponse(
        {
            "providers": providers,
            "keys": [
                {
                    "provider": info.provider,
                    "default_model": info.default_model,
                    "base_url": info.base_url,
                    "is_default": info.is_default,
                    "scope": info.scope,
                }
                for info in infos
            ],
            "supported": list(SUPPORTED_PROVIDERS),
            "can_manage_project_keys": await _can_manage_project_keys(project_id, uid),
        }
    )


@router.post("/api/ask/keys")
async def save_key(request: Request):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    # Resolve (and if needed auto-select) the active project the robust way every
    # other API route uses — not just request.state, which isn't set on /api POSTs.
    from app.api.project_routes import ensure_active_project, set_active_project_cookie

    project_id = await ensure_active_project(request, uid)
    if not project_id:
        return JSONResponse(
            {"error": "No active project — create or select a project first."}, status_code=400
        )
    body = await request.json()
    provider = body.get("provider")
    api_key = (body.get("api_key") or "").strip()
    base_url = (body.get("base_url") or "").strip() or None
    if provider not in SUPPORTED_PROVIDERS:
        return JSONResponse({"error": "Invalid provider or key."}, status_code=400)
    # scope "project" writes the shared project-default key (user_id NULL) —
    # owners/admins only. Default scope is the caller's personal key.
    scope = body.get("scope") or "personal"
    if scope == "project":
        if not await _can_manage_project_keys(project_id, uid):
            return JSONResponse(
                {"error": "Only project owners/admins can set the project key."}, status_code=403
            )
        key_user_id = None
    else:
        key_user_id = uuid.UUID(uid)
    default_model = body.get("default_model") or default_model_for(provider)
    key_required = provider != "lmstudio"
    if key_required and not api_key:
        # No new key supplied — update meta only if a stored key exists.
        updated = await update_key_meta(
            project_id=uuid.UUID(project_id),
            user_id=key_user_id,
            provider=provider,
            default_model=default_model,
            base_url=base_url,
        )
        if not updated:
            return JSONResponse({"error": "API key is required."}, status_code=400)
        resp = JSONResponse({"ok": True})
        set_active_project_cookie(resp, project_id)
        return resp
    await store_key(
        project_id=uuid.UUID(project_id),
        user_id=key_user_id,
        provider=provider,
        api_key=api_key,
        default_model=default_model,
        base_url=base_url,
    )
    resp = JSONResponse({"ok": True})
    set_active_project_cookie(resp, project_id)
    return resp


@router.delete("/api/ask/keys/{provider}")
async def remove_key(request: Request, provider: str):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    from app.api.project_routes import ensure_active_project

    project_id = await ensure_active_project(request, uid)
    if not project_id:
        return JSONResponse({"error": "No active project."}, status_code=400)
    if provider not in SUPPORTED_PROVIDERS:
        return JSONResponse({"error": "Invalid provider."}, status_code=400)
    if request.query_params.get("scope") == "project":
        if not await _can_manage_project_keys(project_id, uid):
            return JSONResponse(
                {"error": "Only project owners/admins can remove the project key."}, status_code=403
            )
        await delete_key(project_id=uuid.UUID(project_id), user_id=None, provider=provider)
    else:
        await delete_key(project_id=uuid.UUID(project_id), user_id=uuid.UUID(uid), provider=provider)
    return JSONResponse({"ok": True})


@router.post("/api/ask/keys/default")
async def set_default_key(request: Request):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    from app.api.project_routes import ensure_active_project

    project_id = await ensure_active_project(request, uid)
    if not project_id:
        return JSONResponse({"error": "No active project."}, status_code=400)
    body = await request.json()
    provider = body.get("provider")
    if provider not in SUPPORTED_PROVIDERS:
        return JSONResponse({"error": "Invalid provider."}, status_code=400)
    await set_default(project_id=uuid.UUID(project_id), user_id=uuid.UUID(uid), provider=provider)
    return JSONResponse({"ok": True})


# ---- model options & catalog -----------------------------------------------


@router.get("/api/ask/model-options")
async def get_model_options(request: Request):
    """Any authed user — returns the superadmin-configured extra models dict."""
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    from app.ask.model_catalog import get_extra_models

    extras = await get_extra_models()
    return JSONResponse(extras)


@router.get("/api/ask/admin/models")
async def admin_get_models(request: Request):
    """Superadmin — returns the extras dict (backward compat)."""
    from app.api.admin_routes import require_superadmin

    await require_superadmin(request)
    from app.ask.model_catalog import get_extra_models

    extras = await get_extra_models()
    return JSONResponse(extras)


@router.post("/api/ask/admin/models")
async def admin_set_models(request: Request):
    """Superadmin — replaces the extra model list for one provider."""
    from app.api.admin_routes import require_superadmin

    await require_superadmin(request)
    body = await request.json()
    provider = body.get("provider")
    models = body.get("models")
    if provider not in SUPPORTED_PROVIDERS:
        return JSONResponse({"error": "Invalid provider."}, status_code=400)
    if not isinstance(models, list):
        return JSONResponse({"error": "models must be a list."}, status_code=400)
    cleaned = list(dict.fromkeys(m.strip() for m in models if isinstance(m, str) and m.strip()))[:50]
    from app.ask.model_catalog import get_extra_models, set_extra_models

    extras = await get_extra_models()
    extras[provider] = cleaned
    await set_extra_models(extras)
    return JSONResponse(extras)


@router.get("/api/ask/admin/models/catalog")
async def admin_get_catalog(request: Request):
    """Returns the full merged catalog (builtin + live + extra) for any authed user."""
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    from app.ask.model_catalog import get_merged_catalog

    catalog = await get_merged_catalog()
    return JSONResponse(_catalog_to_json(catalog))


@router.post("/api/ask/admin/models/sync")
async def admin_sync_models(request: Request):
    """Superadmin — trigger a live sync from all configured providers."""
    from app.api.admin_routes import require_superadmin

    await require_superadmin(request)
    from app.ask.model_sync import sync_all_providers

    results = await sync_all_providers()
    return JSONResponse(
        {
            "results": [
                {"provider": r.provider, "model_count": r.model_count, "errors": r.errors} for r in results
            ],
        }
    )


@router.post("/api/ask/admin/models/sync/{provider}")
async def admin_sync_provider(request: Request, provider: str):
    """Superadmin — trigger a live sync for one provider."""
    from app.api.admin_routes import require_superadmin

    await require_superadmin(request)
    if provider not in SUPPORTED_PROVIDERS:
        return JSONResponse({"error": "Invalid provider."}, status_code=400)

    from app.ask.model_sync import sync_provider, find_key_for_provider

    key_info = await find_key_for_provider(provider)
    if not key_info:
        return JSONResponse({"error": f"No active key found for {provider}."}, status_code=400)

    result = await sync_provider(provider, key_info["api_key"], base_url=key_info.get("base_url"))
    return JSONResponse(
        {
            "provider": result.provider,
            "model_count": result.model_count,
            "errors": result.errors,
        }
    )


@router.put("/api/ask/admin/models/{model_id}/toggle")
async def admin_toggle_model(request: Request, model_id: str):
    """Superadmin — enable/disable a catalog model."""
    from app.api.admin_routes import require_superadmin

    await require_superadmin(request)
    body = await request.json()
    is_enabled = body.get("is_enabled")
    if not isinstance(is_enabled, bool):
        return JSONResponse({"error": "is_enabled must be a boolean."}, status_code=400)

    from app.models.ai_catalog import AiCatalogModel
    from sqlalchemy import select, update as sa_update

    async with app_state.db_session_factory() as db:
        row = (
            await db.execute(select(AiCatalogModel).where(AiCatalogModel.id == uuid.UUID(model_id)))
        ).scalar_one_or_none()
        if row is None:
            return JSONResponse({"error": "Model not found."}, status_code=404)
        await db.execute(
            sa_update(AiCatalogModel)
            .where(AiCatalogModel.id == uuid.UUID(model_id))
            .values(is_enabled=is_enabled)
        )
        await db.commit()
    return JSONResponse({"ok": True})


@router.get("/api/ask/admin/models/catalog/{provider}")
async def admin_get_provider_catalog(request: Request, provider: str):
    """Superadmin — merged catalog for a single provider."""
    from app.api.admin_routes import require_superadmin

    await require_superadmin(request)
    if provider not in SUPPORTED_PROVIDERS:
        return JSONResponse({"error": "Invalid provider."}, status_code=400)
    from app.ask.model_catalog import get_catalog_for_provider

    entries = await get_catalog_for_provider(provider)
    return JSONResponse({"provider": provider, "models": _entries_to_json(entries)})


def _catalog_to_json(catalog: dict[str, list]) -> dict[str, list]:
    """Convert CatalogEntry lists to JSON-safe dicts per provider."""
    return {prov: _entries_to_json(entries) for prov, entries in catalog.items()}


def _entries_to_json(entries: list) -> list[dict]:
    return [
        {
            "id": e.id_,
            "provider": e.provider,
            "model_id": e.model_id,
            "display_name": e.display_name,
            "context_window": e.context_window,
            "capabilities": e.capabilities,
            "is_deprecated": e.is_deprecated,
            "source": e.source,
            "is_enabled": e.is_enabled,
        }
        for e in entries
    ]


@router.get("/settings/ai-models")
async def ai_models_settings(request: Request):
    """Superadmin-only model catalog — redirects to unified AI settings."""
    return RedirectResponse("/settings/ai", status_code=302)


@router.post("/api/ask/keys/test")
async def test_key(request: Request):
    import asyncio

    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    from app.api.project_routes import ensure_active_project

    project_id = await ensure_active_project(request, uid)
    if not project_id:
        return JSONResponse({"error": "No active project."}, status_code=400)
    body = await request.json()
    provider = body.get("provider")
    api_key = (body.get("api_key") or "").strip()
    base_url = (body.get("base_url") or "").strip() or None
    default_model = (body.get("default_model") or "").strip() or None
    if provider not in SUPPORTED_PROVIDERS:
        return JSONResponse({"ok": False, "error": "Invalid provider."})
    key_required = provider != "lmstudio"
    if key_required and not api_key:
        # No key in the form — try to load the stored key for this provider.
        stored = await get_active_key(
            project_id=uuid.UUID(project_id),
            user_id=uuid.UUID(uid),
            provider=provider,
        )
        if stored is None:
            return JSONResponse({"ok": False, "error": "Enter an API key to test, or save one first."})
        api_key = stored.api_key
        if not base_url:
            base_url = stored.base_url
    model = default_model or default_model_for(provider)
    try:
        provider_obj = make_provider(provider, api_key, base_url=base_url)
        probe_messages = [LLMMessage(role="user", content=[TextBlock(text="hi")])]

        async def _run_probe() -> bool:
            async for ev in provider_obj.stream(
                model=model,
                system="ping",
                messages=probe_messages,
                tools=[],
                max_tokens=8,
            ):
                ev_type = getattr(ev, "type", None)
                if ev_type in ("text_delta", "tool_call_start", "message_done"):
                    return True
                if ev_type == "error":
                    raise RuntimeError(getattr(ev, "error", "Provider error"))
            return True

        await asyncio.wait_for(_run_probe(), timeout=20.0)
        return JSONResponse({"ok": True})
    except TimeoutError:
        return JSONResponse({"ok": False, "error": "Connection timed out after 20 seconds."})
    except Exception as exc:
        msg = str(exc)[:300]
        return JSONResponse({"ok": False, "error": msg})
