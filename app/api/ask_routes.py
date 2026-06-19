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
from app.ask.keys import (
    delete_key,
    get_active_key,
    get_default_key,
    list_keys,
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


def _sse_frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _require_user_id(request: Request) -> str | None:
    return get_uid_from_request(request)


def _active_project_id(request: Request) -> str | None:
    # The active_project_id cookie is the source every /api route uses (nav
    # middleware only populates request.state for page renders, not /api POSTs).
    from app.api.project_routes import get_active_project_id

    return get_active_project_id(request) or getattr(request.state, "active_project_id", None)


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
        conv = await _service.create(project_id=pid, user_id=uuid_user, provider=provider_name, model=model)
        # Derive title from first user message (collapse whitespace, truncate to 60 chars).
        raw = " ".join(user_text.split())
        new_conv_title = raw[:60] + ("…" if len(raw) > 60 else "")
        await _service.set_title(conv.id, new_conv_title)

    history = window_history(await _service.load_history(conv.id))

    # Build the system prompt from the active project context.
    project_name = getattr(request.state, "active_project_name", "your project")
    bridge = AskToolBridge(user_id=uid, project_id=project_id)
    specs = bridge.tool_specs()
    connected = _connected_labels(specs)
    role = getattr(request.state, "active_project_role", "member")
    system = build_system_prompt(project_name=project_name, connected=connected, role=role)

    provider = make_provider(provider_name, key.api_key, base_url=key.base_url)
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
    history_with_usage = await _service.load_history_with_usage(conv.id)
    return JSONResponse(
        {
            "id": str(conv.id),
            "title": conv.title,
            "model": conv.model,
            "provider": conv.provider,
            "messages": [
                {
                    "role": m.role,
                    "content": _blocks_for_ui(m),
                    **({"token_usage": usage} if usage is not None else {}),
                }
                for m, usage in history_with_usage
            ],
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
        return JSONResponse({"providers": [], "keys": [], "supported": list(SUPPORTED_PROVIDERS)})
    infos = await list_keys(project_id=uuid.UUID(project_id), user_id=uuid.UUID(uid))
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
                }
                for info in infos
            ],
            "supported": list(SUPPORTED_PROVIDERS),
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
    default_model = body.get("default_model") or default_model_for(provider)
    key_required = provider != "lmstudio"
    if key_required and not api_key:
        # No new key supplied — update meta only if a stored key exists.
        pid = uuid.UUID(project_id)
        uid_uuid = uuid.UUID(uid)
        updated = await update_key_meta(
            project_id=pid,
            user_id=uid_uuid,
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
        user_id=uuid.UUID(uid),
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


# ---- model options (extras from superadmin) --------------------------------


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
    """Superadmin — returns the extras dict."""
    from app.api.admin_routes import require_superadmin

    await require_superadmin(request)
    from app.ask.model_catalog import get_extra_models

    extras = await get_extra_models()
    return JSONResponse(extras)


@router.post("/api/ask/admin/models")
async def admin_set_models(request: Request):
    """Superadmin — replaces the model list for one provider."""
    from app.api.admin_routes import require_superadmin

    await require_superadmin(request)
    body = await request.json()
    provider = body.get("provider")
    models = body.get("models")
    if provider not in SUPPORTED_PROVIDERS:
        return JSONResponse({"error": "Invalid provider."}, status_code=400)
    if not isinstance(models, list):
        return JSONResponse({"error": "models must be a list."}, status_code=400)
    # Strip, dedupe, limit to 50
    cleaned = list(dict.fromkeys(m.strip() for m in models if isinstance(m, str) and m.strip()))[:50]
    from app.ask.model_catalog import get_extra_models, set_extra_models

    extras = await get_extra_models()
    extras[provider] = cleaned
    await set_extra_models(extras)
    return JSONResponse(extras)


@router.get("/settings/ai-models")
async def ai_models_settings(request: Request):
    """Superadmin-only model catalog manager page."""
    from app.api.admin_routes import require_superadmin

    try:
        await require_superadmin(request)
    except Exception:
        return RedirectResponse("/settings", status_code=302)
    from app.api.google_oauth_routes import _load_user_view, _resolve_user_ctx

    user_ctx = await _resolve_user_ctx(request)
    user_view = await _load_user_view(user_ctx) if user_ctx else None
    return render(request, "settings/ai_models.html", {"user": user_view})


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
