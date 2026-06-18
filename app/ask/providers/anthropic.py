"""Anthropic Messages API adapter — raw httpx, no SDK."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any

import httpx

from app.ask.providers.base import (
    ContentBlock,
    LLMMessage,
    StopReason,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)

_API_URL = "https://api.anthropic.com/v1/messages"
_VERSION = "2023-06-01"

_STOP_MAP = {
    "tool_use": StopReason.TOOL_USE,
    "end_turn": StopReason.END,
    "stop_sequence": StopReason.END,
    "max_tokens": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
    "pause_turn": StopReason.PAUSE,
}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, timeout: float = 120.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    # ---- pure helpers (unit-tested) -------------------------------------

    def _block_to_json(self, b: ContentBlock) -> dict[str, Any]:
        if isinstance(b, TextBlock):
            return {"type": "text", "text": b.text}
        if isinstance(b, ToolUseBlock):
            return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
        if isinstance(b, ToolResultBlock):
            return {
                "type": "tool_result",
                "tool_use_id": b.tool_use_id,
                "content": b.content,
                "is_error": b.is_error,
            }
        raise TypeError(f"Unknown block: {b!r}")

    def build_body(
        self,
        *,
        model: str,
        system: str,
        messages: list[LLMMessage],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> dict[str, Any]:
        wire: list[dict[str, Any]] = []
        for m in messages:
            # Anthropic has no "tool" role: tool_result blocks ride in a user turn.
            role = "user" if m.role == "tool" else m.role
            wire.append({"role": role, "content": [self._block_to_json(b) for b in m.content]})
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": wire,
            "stream": True,
        }
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
            ]
        return body

    def build_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _VERSION,
            "content-type": "application/json",
        }

    def _iter_events(
        self,
        frames: Iterable[str],
        block_types: dict[int, str],
        block_ids: dict[int, str],
    ) -> Iterator[StreamEvent]:
        """Decode and dispatch a sequence of raw SSE frames."""
        for frame in frames:
            ename, data = _frame_parts(frame)
            if data is None:
                continue
            try:
                evt = json.loads(data)
            except json.JSONDecodeError:
                continue
            yield from self._handle_event(ename, evt, block_types, block_ids)

    def parse_sse(self, chunks: Iterable[str]) -> Iterator[StreamEvent]:
        """Parse an Anthropic SSE byte/str stream into normalized StreamEvents."""
        buf = ""
        block_types: dict[int, str] = {}
        block_ids: dict[int, str] = {}
        for chunk in chunks:
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                yield from self._iter_events([frame], block_types, block_ids)

    def _handle_event(
        self,
        ename: str | None,
        evt: dict[str, Any],
        block_types: dict[int, str],
        block_ids: dict[int, str],
    ) -> Iterator[StreamEvent]:
        # Anthropic SSE: the event type is in the `event:` line, not the JSON payload.
        etype = ename or evt.get("type")
        if etype == "content_block_start":
            idx = evt["index"]
            cb = evt.get("content_block", {})
            block_types[idx] = cb.get("type", "")
            if cb.get("type") == "tool_use":
                tool_id = cb.get("id")
                if tool_id:
                    block_ids[idx] = tool_id
                yield StreamEvent(type="tool_call_start", tool_id=tool_id, tool_name=cb.get("name"))
        elif etype == "content_block_delta":
            idx = evt.get("index", 0)
            delta = evt.get("delta", {})
            dt = delta.get("type")
            if dt == "text_delta":
                yield StreamEvent(type="text_delta", text=delta.get("text", ""))
            elif dt == "input_json_delta":
                yield StreamEvent(
                    type="tool_args_delta",
                    tool_id=block_ids.get(idx),
                    args_fragment=delta.get("partial_json", ""),
                )
        elif etype == "content_block_stop":
            idx = evt["index"]
            if block_types.get(idx) == "tool_use":
                yield StreamEvent(type="tool_call_end", tool_id=block_ids.get(idx))
        elif etype == "message_delta":
            reason = (evt.get("delta") or {}).get("stop_reason")
            yield StreamEvent(
                type="message_done",
                stop_reason=_STOP_MAP.get(reason, StopReason.END),
                usage=evt.get("usage"),
            )

    # ---- network --------------------------------------------------------

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[LLMMessage],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        body = self.build_body(
            model=model, system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        async with (
            httpx.AsyncClient(timeout=self._timeout) as client,
            client.stream("POST", _API_URL, headers=self.build_headers(), json=body) as resp,
        ):
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode("utf-8", "replace")
                yield StreamEvent(type="error", error=f"anthropic {resp.status_code}: {detail}")
                yield StreamEvent(type="message_done", stop_reason=StopReason.ERROR)
                return
            # parse_sse is a sync generator over accumulated chunks; drive it
            # via an explicit buffer so we can await network reads.
            buf = ""
            block_types: dict[int, str] = {}
            block_ids: dict[int, str] = {}
            async for chunk in resp.aiter_text():
                buf += chunk
                frames: list[str] = []
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    frames.append(frame)
                for out in self._iter_events(frames, block_types, block_ids):
                    yield out


def _frame_parts(frame: str) -> tuple[str | None, str | None]:
    """Extract (event_name, data_payload) from an SSE frame.

    Anthropic SSE frames carry the event type in the ``event:`` line and the
    JSON payload in the ``data:`` line — the JSON itself does NOT always have a
    top-level ``type`` key.
    """
    ename: str | None = None
    data: str | None = None
    for line in frame.splitlines():
        if line.startswith("event:"):
            ename = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data = line[len("data:") :].strip()
    return ename, data
