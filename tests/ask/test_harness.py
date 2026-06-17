import pytest

from app.ask.harness import Harness, HarnessDeps
from app.ask.providers.base import (
    LLMMessage,
    StopReason,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class FakeProvider:
    """Emits a scripted list of event-batches, one batch per .stream() call."""

    name = "fake"

    def __init__(self, scripts):
        self._scripts = list(scripts)

    async def stream(self, **_):
        for ev in self._scripts.pop(0):
            yield ev


class FakeBridge:
    def __init__(self):
        self.calls = []

    def tool_specs(self):
        return []

    async def dispatch(self, name, params):
        self.calls.append((name, params))
        return ('{"rows": 1}', False)


class RecordingService:
    def __init__(self):
        self.appended = []

    async def append(self, conv_id, message, token_usage=None):
        self.appended.append(message)


@pytest.mark.asyncio
async def test_text_only_turn_streams_and_persists():
    provider = FakeProvider(
        [
            [
                StreamEvent(type="text_delta", text="Hello"),
                StreamEvent(type="message_done", stop_reason=StopReason.END),
            ]
        ]
    )
    svc = RecordingService()
    deps = HarnessDeps(
        provider=provider, bridge=FakeBridge(), service=svc, conversation_id="c1", model="m", system="SYS"
    )
    h = Harness(deps, max_iterations=5)
    out = [e async for e in h.run(LLMMessage(role="user", content=[TextBlock(text="hi")]))]
    assert "".join(e.text for e in out if e.type == "text_delta") == "Hello"
    # user message + assistant message persisted
    roles = [m.role for m in svc.appended]
    assert roles == ["user", "assistant"]


@pytest.mark.asyncio
async def test_tool_call_then_final_answer():
    """Single tool call — tool_args_delta has no tool_id, must work via fallback."""
    provider = FakeProvider(
        [
            [  # iteration 1: a tool call
                StreamEvent(type="tool_call_start", tool_id="t1", tool_name="analytics_read"),
                StreamEvent(type="tool_args_delta", args_fragment='{"action":"list"}'),
                StreamEvent(type="tool_call_end"),
                StreamEvent(type="message_done", stop_reason=StopReason.TOOL_USE),
            ],
            [  # iteration 2: final text
                StreamEvent(type="text_delta", text="Done"),
                StreamEvent(type="message_done", stop_reason=StopReason.END),
            ],
        ]
    )
    bridge = FakeBridge()
    svc = RecordingService()
    deps = HarnessDeps(
        provider=provider, bridge=bridge, service=svc, conversation_id="c1", model="m", system="SYS"
    )
    h = Harness(deps, max_iterations=5)
    out = [e async for e in h.run(LLMMessage(role="user", content=[TextBlock(text="hi")]))]
    assert bridge.calls == [("analytics_read", {"action": "list"})]
    roles = [m.role for m in svc.appended]
    # user, assistant(tool_use), tool(result), assistant(final)
    assert roles == ["user", "assistant", "tool", "assistant"]


@pytest.mark.asyncio
async def test_max_iterations_guard_stops_loop():
    # Provider always asks for a tool → would loop forever without the guard.
    always_tool = [
        StreamEvent(type="tool_call_start", tool_id="t1", tool_name="analytics_read"),
        StreamEvent(type="tool_args_delta", args_fragment="{}"),
        StreamEvent(type="tool_call_end"),
        StreamEvent(type="message_done", stop_reason=StopReason.TOOL_USE),
    ]
    provider = FakeProvider([list(always_tool) for _ in range(10)])
    deps = HarnessDeps(
        provider=provider,
        bridge=FakeBridge(),
        service=RecordingService(),
        conversation_id="c1",
        model="m",
        system="SYS",
    )
    h = Harness(deps, max_iterations=3)
    out = [e async for e in h.run(LLMMessage(role="user", content=[TextBlock(text="hi")]))]
    # ends with an error/limit message_done, not an infinite loop
    assert any(e.type == "error" for e in out)


@pytest.mark.asyncio
async def test_usage_summed_across_iterations():
    """Terminal message_done.usage must equal the SUM of per-iteration usages, normalized."""
    provider = FakeProvider(
        [
            [  # iteration 1: tool call — openai-style usage keys
                StreamEvent(type="tool_call_start", tool_id="t1", tool_name="analytics_read"),
                StreamEvent(type="tool_args_delta", args_fragment="{}"),
                StreamEvent(type="tool_call_end"),
                StreamEvent(
                    type="message_done",
                    stop_reason=StopReason.TOOL_USE,
                    usage={"prompt_tokens": 100, "completion_tokens": 50},
                ),
            ],
            [  # iteration 2: final answer — anthropic-style usage keys
                StreamEvent(type="text_delta", text="Done"),
                StreamEvent(
                    type="message_done",
                    stop_reason=StopReason.END,
                    usage={"input_tokens": 200, "output_tokens": 80},
                ),
            ],
        ]
    )
    svc = RecordingService()
    deps = HarnessDeps(
        provider=provider,
        bridge=FakeBridge(),
        service=svc,
        conversation_id="c1",
        model="m",
        system="SYS",
    )
    h = Harness(deps, max_iterations=5)
    out = [e async for e in h.run(LLMMessage(role="user", content=[TextBlock(text="hi")]))]

    # Find the terminal message_done (the one yielded to the client)
    done_events = [e for e in out if e.type == "message_done"]
    assert len(done_events) == 1
    terminal = done_events[0]
    assert terminal.usage is not None
    # input: 100 (prompt_tokens iter1) + 200 (input_tokens iter2)
    assert terminal.usage["input_tokens"] == 300
    # output: 50 (completion_tokens iter1) + 80 (output_tokens iter2)
    assert terminal.usage["output_tokens"] == 130


@pytest.mark.asyncio
async def test_parallel_tool_calls_dispatched_correctly():
    """Two parallel tool calls with interleaved args — both must be dispatched with correct args,
    the assistant message must have two tool_use blocks, and the tool message two tool_result blocks
    with matching ids."""
    provider = FakeProvider(
        [
            [  # iteration 1: two parallel tool calls with interleaved arg fragments
                StreamEvent(type="tool_call_start", tool_id="id-A", tool_name="search"),
                StreamEvent(type="tool_call_start", tool_id="id-B", tool_name="fetch"),
                # interleaved args: A fragment, B fragment, A fragment, B fragment
                StreamEvent(type="tool_args_delta", tool_id="id-A", args_fragment='{"q":'),
                StreamEvent(type="tool_args_delta", tool_id="id-B", args_fragment='{"url":'),
                StreamEvent(type="tool_args_delta", tool_id="id-A", args_fragment='"hello"}'),
                StreamEvent(type="tool_args_delta", tool_id="id-B", args_fragment='"http://x"}'),
                StreamEvent(type="message_done", stop_reason=StopReason.TOOL_USE),
            ],
            [  # iteration 2: final text
                StreamEvent(type="text_delta", text="All done"),
                StreamEvent(type="message_done", stop_reason=StopReason.END),
            ],
        ]
    )
    bridge = FakeBridge()
    svc = RecordingService()
    deps = HarnessDeps(
        provider=provider, bridge=bridge, service=svc, conversation_id="c1", model="m", system="SYS"
    )
    h = Harness(deps, max_iterations=5)
    out = [e async for e in h.run(LLMMessage(role="user", content=[TextBlock(text="go")]))]

    # Both tools dispatched with correct, non-contaminated args
    assert len(bridge.calls) == 2
    calls_by_name = dict(bridge.calls)
    assert calls_by_name["search"] == {"q": "hello"}
    assert calls_by_name["fetch"] == {"url": "http://x"}

    # Roles: user, assistant(2 tool_use), tool(2 tool_result), assistant(final text)
    roles = [m.role for m in svc.appended]
    assert roles == ["user", "assistant", "tool", "assistant"]

    # Assistant tool message has two tool_use blocks
    assistant_tool_msg = svc.appended[1]
    tool_use_blocks = [b for b in assistant_tool_msg.content if isinstance(b, ToolUseBlock)]
    assert len(tool_use_blocks) == 2
    tool_use_ids = {b.id for b in tool_use_blocks}
    assert tool_use_ids == {"id-A", "id-B"}

    # Tool result message has two tool_result blocks with matching ids
    tool_result_msg = svc.appended[2]
    assert tool_result_msg.role == "tool"
    result_blocks = [b for b in tool_result_msg.content if isinstance(b, ToolResultBlock)]
    assert len(result_blocks) == 2
    result_ids = {b.tool_use_id for b in result_blocks}
    assert result_ids == {"id-A", "id-B"}
