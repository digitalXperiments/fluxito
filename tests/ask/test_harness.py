import pytest

from app.ask.harness import Harness, HarnessDeps
from app.ask.providers.base import LLMMessage, StopReason, StreamEvent, TextBlock


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
