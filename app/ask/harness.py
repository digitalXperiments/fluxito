"""The vendor-neutral agentic loop: reason -> tools -> observe -> iterate -> answer."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from app.ask.providers.base import (
    LLMMessage,
    StopReason,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


class _Provider(Protocol):
    name: str

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[LLMMessage],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]: ...


class _Bridge(Protocol):
    def tool_specs(self) -> list[ToolSpec]: ...

    async def dispatch(self, name: str, params: dict[str, Any]) -> tuple[str, bool]: ...


class _Service(Protocol):
    async def append(
        self,
        conversation_id: Any,
        message: LLMMessage,
        *,
        token_usage: dict | None = ...,
    ) -> Any: ...


class _Drafts(Protocol):
    async def create(
        self,
        *,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID | None,
        created_by: uuid.UUID | None,
        kind: str,
        title: str,
        payload: dict[str, Any],
    ) -> Any: ...

    async def attach_message(self, draft_id: uuid.UUID, message_id: uuid.UUID) -> None: ...


@dataclass
class HarnessDeps:
    provider: _Provider
    bridge: _Bridge
    service: _Service
    conversation_id: Any
    model: str
    system: str
    history: list[LLMMessage] | None = None
    max_tokens: int = 4096
    # Draft wiring — only set when a write-capable section (e.g. `implement`)
    # is active. When drafts is None, no FluxDraft is ever created.
    drafts: _Drafts | None = None
    project_id: uuid.UUID | None = None
    created_by: uuid.UUID | None = None


class _PendingTool:
    __slots__ = ("args", "id", "name")

    def __init__(self, tool_id: str, tool_name: str) -> None:
        self.id: str = tool_id
        self.name: str = tool_name
        self.args: str = ""


class Harness:
    def __init__(self, deps: HarnessDeps, *, max_iterations: int = 12) -> None:
        self._d = deps
        self._max_iter = max_iterations

    async def run(self, user_message: LLMMessage) -> AsyncIterator[StreamEvent]:
        d = self._d
        messages: list[LLMMessage] = list(d.history or [])
        messages.append(user_message)
        await d.service.append(d.conversation_id, user_message)

        tools = d.bridge.tool_specs()

        # Accumulated token usage across all iterations.
        total_input: int = 0
        total_output: int = 0

        # Drafts created mid-turn (e.g. a proposed GTM change) whose message_id
        # isn't known until the follow-up assistant answer is persisted.
        unattached_drafts: list[uuid.UUID] = []

        for _ in range(self._max_iter):
            assistant_blocks: list[Any] = []
            text_buf: list[str] = []
            # Order-preserving dict of pending tools keyed by tool id.
            pending: dict[str, _PendingTool] = {}
            order: list[str] = []
            stop: StopReason | None = None
            usage: dict | None = None

            async for ev in d.provider.stream(
                model=d.model,
                system=d.system,
                messages=messages,
                tools=tools,
                max_tokens=d.max_tokens,
            ):
                if ev.type == "text_delta":
                    text_buf.append(ev.text or "")
                    yield ev
                elif ev.type == "tool_call_start":
                    tool_id = ev.tool_id or f"_auto_{len(order)}"
                    pt = _PendingTool(tool_id=tool_id, tool_name=ev.tool_name or "")
                    pending[tool_id] = pt
                    order.append(tool_id)
                    yield ev
                elif ev.type == "tool_args_delta":
                    # Route by tool_id if present; fall back to the most-recently-started tool.
                    tid = ev.tool_id
                    if tid and tid in pending:
                        pending[tid].args += ev.args_fragment or ""
                    elif order:
                        pending[order[-1]].args += ev.args_fragment or ""
                    yield ev
                elif ev.type == "tool_call_end":
                    # Informational only — pending is already populated at tool_call_start.
                    yield ev
                elif ev.type == "error":
                    yield ev
                elif ev.type == "message_done":
                    stop = ev.stop_reason
                    usage = ev.usage

            # Accumulate token usage across iterations (normalize both provider shapes).
            if usage:
                total_input += usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                total_output += usage.get("output_tokens") or usage.get("completion_tokens") or 0

            # Build + persist the assistant turn.
            if text_buf:
                assistant_blocks.append(TextBlock(text="".join(text_buf)))
            for tid in order:
                pt = pending[tid]
                assistant_blocks.append(ToolUseBlock(id=pt.id, name=pt.name, input=_safe_json(pt.args)))
            if assistant_blocks:
                assistant_msg = LLMMessage(role="assistant", content=assistant_blocks)
                messages.append(assistant_msg)
                # Fold model + provider into stored token_usage so per-model
                # grouping works when the conversation is reloaded.
                tu = dict(usage or {})
                tu["model"] = d.model
                tu["provider"] = d.provider.name
                msg_id = await d.service.append(d.conversation_id, assistant_msg, token_usage=tu)
                # Link any mid-turn drafts to this assistant answer once it's
                # persisted. Drafts are created *after* their tool-call assistant
                # message is stored, so this only ever binds them to the follow-up
                # answer message (the card renders there on history reload).
                if unattached_drafts and text_buf and d.drafts is not None and msg_id is not None:
                    for did in unattached_drafts:
                        await d.drafts.attach_message(did, msg_id)
                    unattached_drafts.clear()

            if stop == StopReason.TOOL_USE and order:
                # Execute all tool calls concurrently, preserving order.
                parsed_args = [_safe_json(pending[tid].args) for tid in order]
                results = await asyncio.gather(
                    *[d.bridge.dispatch(pending[tid].name, parsed_args[i]) for i, tid in enumerate(order)]
                )
                tool_blocks = [
                    ToolResultBlock(tool_use_id=tid, content=content, is_error=is_err)
                    for tid, (content, is_err) in zip(order, results, strict=True)
                ]
                tool_msg = LLMMessage(role="tool", content=tool_blocks)
                messages.append(tool_msg)
                await d.service.append(d.conversation_id, tool_msg)

                # Turn any successful GTM propose_change into a pending FluxDraft
                # and stream a `draft` frame so the client renders the diff card.
                if d.drafts is not None:
                    for i, tid in enumerate(order):
                        content, is_err = results[i]
                        if is_err or pending[tid].name != "tagmanager_write":
                            continue
                        if parsed_args[i].get("action") != "propose_change":
                            continue
                        result = _safe_json(content)
                        if result.get("error"):
                            continue
                        draft = await self._create_gtm_draft(result)
                        if draft is None:
                            continue
                        unattached_drafts.append(draft.id)
                        yield StreamEvent(type="draft", draft=_draft_payload(draft))

                continue  # next iteration: feed results back to the model

            # Terminal frame: use summed token totals.
            summed_usage: dict | None = (
                {"input_tokens": total_input, "output_tokens": total_output}
                if (total_input or total_output)
                else None
            )
            if stop in (
                StopReason.END,
                StopReason.MAX_TOKENS,
                StopReason.REFUSAL,
                StopReason.ERROR,
            ):
                yield StreamEvent(type="message_done", stop_reason=stop, usage=summed_usage)
                return
            if stop == StopReason.PAUSE:
                continue
            # No tool calls but also not a clean terminal — treat as done.
            yield StreamEvent(type="message_done", stop_reason=StopReason.END, usage=summed_usage)
            return

        # Iteration budget exhausted.
        yield StreamEvent(
            type="error",
            error=f"Stopped after {self._max_iter} tool-use rounds without finishing.",
        )
        yield StreamEvent(type="message_done", stop_reason=StopReason.MAX_TOKENS)

    async def _create_gtm_draft(self, result: dict[str, Any]) -> Any:
        """Persist a FluxDraft from a tagmanager_write propose_change result.
        Returns the created draft, or None if we can't (no drafts service /
        project). Never raises — a draft failure must not break the chat turn."""
        d = self._d
        if d.drafts is None or d.project_id is None:
            return None
        try:
            title, payload = _gtm_draft_from_propose(result)
            return await d.drafts.create(
                project_id=d.project_id,
                conversation_id=d.conversation_id,
                message_id=None,  # attached to the follow-up answer once persisted
                created_by=d.created_by,
                kind="gtm_workspace_change",
                title=title,
                payload=payload,
            )
        except Exception:
            return None


def _draft_payload(draft: Any) -> dict[str, Any]:
    from app.ask.drafts import draft_to_stream_payload

    return draft_to_stream_payload(draft)


def _gtm_draft_from_propose(result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Shape a FluxDraft (title, payload) from a propose_change tool result.

    The payload carries both the display fields the client renders (workspace
    label, target, diff lines) and, under ``gtm``, the identifiers
    DraftService.approve needs to run the real publish. Any identifier may be
    None (legacy / underspecified proposal) — approve() falls back to a mock
    version when they're missing.
    """
    entity_type = str(result.get("entity_type") or "entity")
    entity_name = str(result.get("entity_name") or "change")
    change_type = str(result.get("change_type") or "update")
    proposal = str(result.get("proposal") or "")

    title = f"{change_type.capitalize()} {entity_type} '{entity_name}'"
    diff = [{"kind": "context", "text": line} for line in proposal.splitlines() if line.strip()]
    gtm = {
        "connection_id": result.get("connection_id"),
        "account_id": result.get("account_id"),
        "container_id": result.get("container_id"),
        "workspace_id": result.get("workspace_id"),
    }
    container_id = gtm["container_id"]
    workspace_id = gtm["workspace_id"]
    ws_bits = " · ".join(b for b in (container_id, f"workspace: {workspace_id}" if workspace_id else "") if b)
    payload: dict[str, Any] = {
        "workspace_label": ws_bits or "GTM workspace",
        "target": f"{entity_type.upper()}: {entity_name}",
        "diff": diff,
        "proposal": proposal,
        "proposed_config": result.get("proposed_config"),
        "change_type": change_type,
        "entity_type": entity_type,
        "entity_name": entity_name,
        "gtm": gtm,
    }
    return title, payload


def _safe_json(s: str) -> dict[str, Any]:
    s = (s or "").strip()
    if not s:
        return {}
    try:
        val = json.loads(s)
        return val if isinstance(val, dict) else {"value": val}
    except json.JSONDecodeError:
        return {}
