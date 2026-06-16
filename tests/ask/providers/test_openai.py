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


def _chunks(s: str):
    for i in range(0, len(s), 9):
        yield s[i : i + 9]
