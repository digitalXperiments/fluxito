"""Conversation history assembly for the harness.

v1 strategy: keep the most recent N messages, but never let the kept window begin
with an orphaned tool result (which would violate vendor threading rules). Summarization
/ compaction is a later increment.
"""

from __future__ import annotations

from app.ask.providers.base import LLMMessage

DEFAULT_MAX_MESSAGES = 40


def window_history(
    messages: list[LLMMessage], *, max_messages: int = DEFAULT_MAX_MESSAGES
) -> list[LLMMessage]:
    kept = messages[-max_messages:] if max_messages > 0 else list(messages)
    # Drop a leading tool turn whose matching assistant tool_use was cut off.
    while kept and kept[0].role == "tool":
        kept = kept[1:]
    return kept
