"""OpenAI Chat Completions adapter — raw httpx, no SDK."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any

import httpx

from app.ask.providers.base import (
    LLMMessage,
    StopReason,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)

_API_URL = "https://api.openai.com/v1/chat/completions"

_FINISH_MAP = {
    "tool_calls": StopReason.TOOL_USE,
    "stop": StopReason.END,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
}


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, *, timeout: float = 120.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    # ---- pure helpers ---------------------------------------------------

    def build_body(
        self,
        *,
        model: str,
        system: str,
        messages: list[LLMMessage],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> dict[str, Any]:
        wire: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "tool":
                for b in m.content:
                    assert isinstance(b, ToolResultBlock)
                    wire.append({"role": "tool", "tool_call_id": b.tool_use_id, "content": b.content})
                continue
            text_parts = [b.text for b in m.content if isinstance(b, TextBlock)]
            tool_calls = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(b.input)},
                }
                for b in m.content
                if isinstance(b, ToolUseBlock)
            ]
            msg: dict[str, Any] = {"role": m.role}
            if text_parts:
                msg["content"] = "".join(text_parts)
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if "content" not in msg and "tool_calls" not in msg:
                msg["content"] = ""
            wire.append(msg)

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": wire,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto"
        return body

    def build_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "content-type": "application/json"}

    # ---- shared per-frame decoder (used by both parse_sse and stream) ---

    def _iter_frames(
        self,
        frames: Iterable[str],
        started: set[int],
        finish_box: list[str | None],
        usage_box: list[dict[str, Any] | None],
    ) -> Iterator[StreamEvent | str]:
        """Decode and dispatch a sequence of raw SSE data payloads.

        Yields StreamEvent objects normally.  When a ``[DONE]`` sentinel is
        found, yields the string ``"[DONE]"`` so the caller can emit the
        terminal ``message_done`` and stop iteration.

        *started* — mutable set of tool-call indexes already announced.
        *finish_box* — single-element list used as a mutable cell for the
          latest ``finish_reason``.
        *usage_box* — single-element list used as a mutable cell for usage.
        """
        for frame in frames:
            data = _data_payload(frame)
            if data is None:
                continue
            if data == "[DONE]":
                yield "[DONE]"
                return
            try:
                evt = json.loads(data)
            except json.JSONDecodeError:
                continue
            if evt.get("usage"):
                usage_box[0] = evt["usage"]
            for choice in evt.get("choices", []):
                if choice.get("finish_reason"):
                    finish_box[0] = choice["finish_reason"]
                delta = choice.get("delta", {})
                if delta.get("content"):
                    yield StreamEvent(type="text_delta", text=delta["content"])
                for tc in delta.get("tool_calls", []) or []:
                    idx = tc.get("index", 0)
                    fn = tc.get("function", {})
                    if idx not in started:
                        started.add(idx)
                        yield StreamEvent(
                            type="tool_call_start",
                            tool_id=tc.get("id"),
                            tool_name=fn.get("name"),
                        )
                    if fn.get("arguments"):
                        yield StreamEvent(type="tool_args_delta", args_fragment=fn["arguments"])

    # ---- sync SSE parser (unit-tested) ----------------------------------

    def parse_sse(self, chunks: Iterable[str]) -> Iterator[StreamEvent]:
        """Parse an OpenAI SSE byte/str stream into normalized StreamEvents."""
        buf = ""
        started: set[int] = set()
        finish_box: list[str | None] = [None]
        usage_box: list[dict[str, Any] | None] = [None]

        for chunk in chunks:
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                for item in self._iter_frames([frame], started, finish_box, usage_box):
                    if item == "[DONE]":
                        yield StreamEvent(
                            type="message_done",
                            stop_reason=_FINISH_MAP.get(finish_box[0] or "", StopReason.END),
                            usage=usage_box[0],
                        )
                        return
                    yield item  # type: ignore[misc]

        # Stream ended without an explicit [DONE].
        yield StreamEvent(
            type="message_done",
            stop_reason=_FINISH_MAP.get(finish_box[0] or "", StopReason.END),
            usage=usage_box[0],
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
                yield StreamEvent(type="error", error=f"openai {resp.status_code}: {detail}")
                yield StreamEvent(type="message_done", stop_reason=StopReason.ERROR)
                return
            buf = ""
            started: set[int] = set()
            finish_box: list[str | None] = [None]
            usage_box: list[dict[str, Any] | None] = [None]
            async for chunk in resp.aiter_text():
                buf += chunk
                frames: list[str] = []
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    frames.append(frame)
                done = False
                for item in self._iter_frames(frames, started, finish_box, usage_box):
                    if item == "[DONE]":
                        yield StreamEvent(
                            type="message_done",
                            stop_reason=_FINISH_MAP.get(finish_box[0] or "", StopReason.END),
                            usage=usage_box[0],
                        )
                        done = True
                        break
                    yield item  # type: ignore[misc]
                if done:
                    return
            yield StreamEvent(
                type="message_done",
                stop_reason=_FINISH_MAP.get(finish_box[0] or "", StopReason.END),
                usage=usage_box[0],
            )


def _data_payload(frame: str) -> str | None:
    """Extract the ``data:`` line value from an SSE frame, or None."""
    for line in frame.splitlines():
        if line.startswith("data:"):
            return line[len("data:") :].strip()
    return None
