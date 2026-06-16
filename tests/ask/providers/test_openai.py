from app.ask.providers.base import (
    LLMMessage,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)
from app.ask.providers.openai import OpenAIProvider


def test_build_body_maps_roles_and_tools():
    p = OpenAIProvider(api_key="sk-test")
    body = p.build_body(
        model="gpt-4o",
        system="SYS",
        messages=[
            LLMMessage(role="user", content=[TextBlock(text="hi")]),
            LLMMessage(role="assistant", content=[ToolUseBlock(id="c1", name="x", input={"a": 1})]),
            LLMMessage(role="tool", content=[ToolResultBlock(tool_use_id="c1", content="ok")]),
        ],
        tools=[ToolSpec(name="x", description="d", input_schema={"type": "object"})],
        max_tokens=1024,
    )
    assert body["model"] == "gpt-4o" and body["stream"] is True
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}
    assert body["messages"][2]["role"] == "assistant"
    assert body["messages"][2]["tool_calls"][0] == {
        "id": "c1",
        "type": "function",
        "function": {"name": "x", "arguments": '{"a": 1}'},
    }
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}
    assert body["tools"][0] == {
        "type": "function",
        "function": {"name": "x", "description": "d", "parameters": {"type": "object"}},
    }


def test_parse_sse_accumulates_tool_args_by_index():
    p = OpenAIProvider(api_key="sk-test")
    raw = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"analytics_read","arguments":"{\\"a\\":"}}]}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        "data: [DONE]\n\n"
    )
    events = list(p.parse_sse(_chunks(raw)))
    text = "".join(e.text for e in events if e.type == "text_delta")
    assert text == "Hello"
    start = next(e for e in events if e.type == "tool_call_start")
    assert start.tool_id == "c1" and start.tool_name == "analytics_read"
    args = "".join(e.args_fragment for e in events if e.type == "tool_args_delta")
    assert args == '{"a":1}'
    done = next(e for e in events if e.type == "message_done")
    assert done.stop_reason == StopReason.TOOL_USE


def test_parse_sse_parallel_tool_calls_no_cross_contamination():
    """Two parallel tool calls with interleaved arg fragments must not cross-contaminate."""
    p = OpenAIProvider(api_key="sk-test")
    # index 0 = "search" with id "id-A"; index 1 = "fetch" with id "id-B"
    # arg fragments are interleaved: A chunk, B chunk, A chunk, B chunk
    raw = (
        # index 0 start (id present on first delta)
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"id-A","function":{"name":"search","arguments":""}}]}}]}\n\n'
        # index 1 start (id present on first delta)
        'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"id-B","function":{"name":"fetch","arguments":""}}]}}]}\n\n'
        # args for index 0 — first fragment
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"q\\":"}}]}}]}\n\n'
        # args for index 1 — first fragment (interleaved!)
        'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"function":{"arguments":"{\\"url\\":"}}]}}]}\n\n'
        # args for index 0 — second fragment
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"hello\\"}"}}]}}]}\n\n'
        # args for index 1 — second fragment
        'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"function":{"arguments":"\\"http://x\\"}"}}]}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        "data: [DONE]\n\n"
    )
    events = list(p.parse_sse(_chunks(raw)))

    # Two tool_call_start events with distinct ids and names
    starts = [e for e in events if e.type == "tool_call_start"]
    assert len(starts) == 2
    start_ids = {e.tool_id for e in starts}
    start_names = {e.tool_name for e in starts}
    assert start_ids == {"id-A", "id-B"}
    assert start_names == {"search", "fetch"}

    # Args fragments for each tool_id must be correct and not cross-contaminated
    args_by_id: dict[str, str] = {}
    for e in events:
        if e.type == "tool_args_delta":
            assert e.tool_id is not None, "tool_args_delta must carry tool_id"
            args_by_id[e.tool_id] = args_by_id.get(e.tool_id, "") + (e.args_fragment or "")

    assert args_by_id["id-A"] == '{"q":"hello"}'
    assert args_by_id["id-B"] == '{"url":"http://x"}'

    # A tool_call_end for each started tool
    ends = [e for e in events if e.type == "tool_call_end"]
    assert len(ends) == 2
    end_ids = {e.tool_id for e in ends}
    assert end_ids == {"id-A", "id-B"}

    # Terminal event is TOOL_USE
    done = next(e for e in events if e.type == "message_done")
    assert done.stop_reason == StopReason.TOOL_USE


def _chunks(s: str):
    for i in range(0, len(s), 9):
        yield s[i : i + 9]
