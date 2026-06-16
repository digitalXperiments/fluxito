"""HTTP surface for Ask Fluxito: page, SSE chat stream, conversation CRUD, key setup."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from app.ask.context import window_history
from app.ask.harness import Harness, HarnessDeps
from app.ask.keys import get_active_key, list_providers, store_key
from app.ask.prompts import build_system_prompt
from app.ask.providers.base import LLMMessage, StreamEvent, TextBlock
from app.ask.providers.registry import SUPPORTED_PROVIDERS, default_model_for, make_provider
from app.ask.service import ConversationService
from app.ask.tools import AskToolBridge
from app.auth.uid_cookie import get_uid_from_request
from app.templating import render

router = APIRouter()
_service = ConversationService()


def _sse_frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _require_user_id(request: Request) -> str | None:
    return get_uid_from_request(request)


def _active_project_id(request: Request) -> str | None:
    return getattr(request.state, "active_project_id", None)


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
    uid = _require_user_id(request)
    if not uid:
        return RedirectResponse("/signin?next=/ask", status_code=302)
    return render(request, "ask.html", {"page_title": "Ask Fluxito", "active": "ask"})


@router.get("/settings/ai")
async def ai_settings(request: Request):
    uid = _require_user_id(request)
    if not uid:
        return RedirectResponse("/signin?next=/settings/ai", status_code=302)
    # The minimal v1 home for provider keys is the dialog on the /ask page.
    return RedirectResponse("/ask#keys", status_code=302)


# ---- chat stream --------------------------------------------------------


@router.post("/api/ask/stream")
async def ask_stream(request: Request):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    project_id = _active_project_id(request)
    if not project_id:
        return JSONResponse({"error": "No active project."}, status_code=400)

    body = await request.json()
    user_text = (body.get("message") or "").strip()
    if not user_text:
        return JSONResponse({"error": "Empty message."}, status_code=400)
    conv_id = body.get("conversation_id")

    pid = uuid.UUID(project_id)
    uuid_user = uuid.UUID(uid)

    # Pick a provider that has a stored key (prefer one named in the body).
    available = await list_providers(project_id=pid, user_id=uuid_user)
    if not available:
        return JSONResponse(
            {"error": "no_key", "message": "Add an AI provider API key in settings first."},
            status_code=400,
        )
    provider_name = body.get("provider") if body.get("provider") in available else available[0]
    key = await get_active_key(project_id=pid, user_id=uuid_user, provider=provider_name)
    if key is None:
        return JSONResponse({"error": "no_key"}, status_code=400)
    model = key.default_model or default_model_for(provider_name)

    # Resolve or create the conversation.
    if conv_id:
        conv_uuid = _parse_uuid(conv_id)
        conv = await _service.get(conv_uuid) if conv_uuid else None
        if conv is None or str(conv.user_id) != uid:
            return JSONResponse({"error": "not_found"}, status_code=404)
    else:
        conv = await _service.create(project_id=pid, user_id=uuid_user, provider=provider_name, model=model)

    history = window_history(await _service.load_history(conv.id))

    # Build the system prompt from the active project context.
    project_name = getattr(request.state, "active_project_name", "your project")
    bridge = AskToolBridge(user_id=uid, project_id=project_id)
    specs = bridge.tool_specs()
    connected = _connected_labels(specs)
    role = getattr(request.state, "active_project_role", "member")
    system = build_system_prompt(project_name=project_name, connected=connected, role=role)

    provider = make_provider(provider_name, key.api_key)
    deps = HarnessDeps(
        provider=provider,
        bridge=bridge,
        service=_service,
        conversation_id=conv.id,
        model=model,
        system=system,
        history=history,
    )
    harness = Harness(deps)
    user_message = LLMMessage(role="user", content=[TextBlock(text=user_text)])

    async def event_stream():
        # First frame carries the conversation id so the client can persist it.
        yield _sse_frame({"type": "conversation", "conversation_id": str(conv.id)})
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
    history = await _service.load_history(conv.id)
    return JSONResponse(
        {
            "id": str(conv.id),
            "title": conv.title,
            "messages": [{"role": m.role, "content": _blocks_for_ui(m)} for m in history],
        }
    )


def _blocks_for_ui(message: LLMMessage) -> list[dict[str, Any]]:
    from app.ask.providers.base import blocks_to_json

    return blocks_to_json(message.content)


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


@router.get("/api/ask/keys")
async def get_keys(request: Request):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    project_id = _active_project_id(request)
    if not project_id:
        return JSONResponse({"providers": [], "supported": list(SUPPORTED_PROVIDERS)})
    providers = await list_providers(project_id=uuid.UUID(project_id), user_id=uuid.UUID(uid))
    return JSONResponse({"providers": providers, "supported": list(SUPPORTED_PROVIDERS)})


@router.post("/api/ask/keys")
async def save_key(request: Request):
    uid = _require_user_id(request)
    if not uid:
        return JSONResponse({"error": "auth"}, status_code=401)
    project_id = _active_project_id(request)
    if not project_id:
        return JSONResponse({"error": "No active project."}, status_code=400)
    body = await request.json()
    provider = body.get("provider")
    api_key = (body.get("api_key") or "").strip()
    if provider not in SUPPORTED_PROVIDERS or not api_key:
        return JSONResponse({"error": "Invalid provider or key."}, status_code=400)
    default_model = body.get("default_model") or default_model_for(provider)
    await store_key(
        project_id=uuid.UUID(project_id),
        user_id=uuid.UUID(uid),
        provider=provider,
        api_key=api_key,
        default_model=default_model,
    )
    return JSONResponse({"ok": True})
