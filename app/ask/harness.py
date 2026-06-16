"""The vendor-neutral agentic loop: reason -> tools -> observe -> iterate -> answer."""

from __future__ import annotations

import asyncio
import json
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
    ) -> None: ...


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


class _PendingTool:
    __slots__ = ("args", "id", "name")

    def __init__(self) -> None:
        self.id: str | None = None
        self.name: str | None = None
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

        for _ in range(self._max_iter):
            assistant_blocks: list[Any] = []
            text_buf: list[str] = []
            pending: list[_PendingTool] = []
            cur: _PendingTool | None = None
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
                    cur = _PendingTool()
                    cur.id = ev.tool_id
                    cur.name = ev.tool_name
                    yield ev
                elif ev.type == "tool_args_delta":
                    if cur is not None:
                        cur.args += ev.args_fragment or ""
                    yield ev
                elif ev.type == "tool_call_end":
                    if cur is not None:
                        pending.append(cur)
                        cur = None
                    yield ev
                elif ev.type == "error":
                    yield ev
                elif ev.type == "message_done":
                    stop = ev.stop_reason
                    usage = ev.usage

            # Build + persist the assistant turn.
            if text_buf:
                assistant_blocks.append(TextBlock(text="".join(text_buf)))
            for pt in pending:
                assistant_blocks.append(
                    ToolUseBlock(id=pt.id or "", name=pt.name or "", input=_safe_json(pt.args))
                )
            if assistant_blocks:
                assistant_msg = LLMMessage(role="assistant", content=assistant_blocks)
                messages.append(assistant_msg)
                await d.service.append(d.conversation_id, assistant_msg, token_usage=usage)

            if stop == StopReason.TOOL_USE and pending:
                # Execute all tool calls concurrently, preserving order.
                results = await asyncio.gather(
                    *[d.bridge.dispatch(pt.name or "", _safe_json(pt.args)) for pt in pending]
                )
                tool_blocks = [
                    ToolResultBlock(tool_use_id=pt.id or "", content=content, is_error=is_err)
                    for pt, (content, is_err) in zip(pending, results, strict=True)
                ]
                tool_msg = LLMMessage(role="tool", content=tool_blocks)
                messages.append(tool_msg)
                await d.service.append(d.conversation_id, tool_msg)
                continue  # next iteration: feed results back to the model

            if stop in (
                StopReason.END,
                StopReason.MAX_TOKENS,
                StopReason.REFUSAL,
                StopReason.ERROR,
            ):
                yield StreamEvent(type="message_done", stop_reason=stop, usage=usage)
                return
            if stop == StopReason.PAUSE:
                continue
            # No tool calls but also not a clean terminal — treat as done.
            yield StreamEvent(type="message_done", stop_reason=StopReason.END, usage=usage)
            return

        # Iteration budget exhausted.
        yield StreamEvent(
            type="error",
            error=f"Stopped after {self._max_iter} tool-use rounds without finishing.",
        )
        yield StreamEvent(type="message_done", stop_reason=StopReason.MAX_TOKENS)


def _safe_json(s: str) -> dict[str, Any]:
    s = (s or "").strip()
    if not s:
        return {}
    try:
        val = json.loads(s)
        return val if isinstance(val, dict) else {"value": val}
    except json.JSONDecodeError:
        return {}
