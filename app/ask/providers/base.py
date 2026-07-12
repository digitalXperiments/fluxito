"""Vendor-neutral types shared by the harness and every provider adapter.

No vendor-specific shape ever leaks above the adapter layer. Adapters translate
these types to/from each vendor's raw HTTP JSON.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Protocol


class StopReason(str, Enum):
    END = "end"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    PAUSE = "pause"
    ERROR = "error"


@dataclass
class TextBlock:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentBlock]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class StreamEvent:
    """One normalized event in the unified stream sent to the SSE endpoint."""

    type: Literal[
        "text_delta",
        "thinking_delta",  # reserved for a later increment; never emitted in v1
        "tool_call_start",
        "tool_args_delta",
        "tool_call_end",
        "message_done",
        "error",
        "draft",  # a Flux-proposed change (e.g. GTM diff) — see app/ask/drafts.py
    ]
    text: str | None = None
    tool_id: str | None = None
    tool_name: str | None = None
    args_fragment: str | None = None
    stop_reason: StopReason | None = None
    usage: dict[str, Any] | None = None
    error: str | None = None
    # Populated only for type == "draft"; see app.ask.drafts.draft_to_stream_payload
    # for the exact shape (kind/title/status/payload/published_version/...).
    draft: dict[str, Any] | None = None


def blocks_to_json(blocks: list[ContentBlock]) -> list[dict[str, Any]]:
    """Serialize content blocks to a JSONB-safe list of dicts."""
    out: list[dict[str, Any]] = []
    for b in blocks:
        if isinstance(b, TextBlock):
            out.append({"type": "text", "text": b.text})
        elif isinstance(b, ToolUseBlock):
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif isinstance(b, ToolResultBlock):
            out.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.tool_use_id,
                    "content": b.content,
                    "is_error": b.is_error,
                }
            )
        else:  # pragma: no cover - exhaustive guard
            raise TypeError(f"Unknown content block: {b!r}")
    return out


def blocks_from_json(raw: list[dict[str, Any]]) -> list[ContentBlock]:
    """Inverse of blocks_to_json."""
    out: list[ContentBlock] = []
    for d in raw:
        t = d.get("type")
        if t == "text":
            out.append(TextBlock(text=d["text"]))
        elif t == "tool_use":
            out.append(ToolUseBlock(id=d["id"], name=d["name"], input=d["input"]))
        elif t == "tool_result":
            out.append(
                ToolResultBlock(
                    tool_use_id=d["tool_use_id"],
                    content=d["content"],
                    is_error=bool(d.get("is_error", False)),
                )
            )
        else:
            raise ValueError(f"Unknown content block type: {t!r}")
    return out


class Provider(Protocol):
    """Every adapter implements this. The harness only ever calls .stream()."""

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
