from app.ask.providers.anthropic import AnthropicProvider
from app.ask.providers.base import (
    LLMMessage,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


def test_build_body_maps_messages_and_tools():
    p = AnthropicProvider(api_key="sk-test")
    body = p.build_body(
        model="claude-opus-4-8",
        system="SYS",
        messages=[
            LLMMessage(role="user", content=[TextBlock(text="hi")]),
            LLMMessage(role="assistant", content=[ToolUseBlock(id="t1", name="x", input={"a": 1})]),
            LLMMessage(role="tool", content=[ToolResultBlock(tool_use_id="t1", content="ok")]),
        ],
        tools=[ToolSpec(name="x", description="d", input_schema={"type": "object"})],
        max_tokens=1024,
    )
    assert body["model"] == "claude-opus-4-8"
    assert body["system"] == "SYS"
    assert body["stream"] is True
    assert body["tools"] == [{"name": "x", "description": "d", "input_schema": {"type": "object"}}]
    # user text
    assert body["messages"][0] == {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    # assistant tool_use
    assert body["messages"][1]["content"][0] == {
        "type": "tool_use",
        "id": "t1",
        "name": "x",
        "input": {"a": 1},
    }
    # tool result is folded into a user-role message (Anthropic threading rule)
    assert body["messages"][2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False}],
    }


def test_parse_sse_text_and_tool_use():
    p = AnthropicProvider(api_key="sk-test")
    raw = (
        'event: message_start\ndata: {"type":"message_start"}\n\n'
        'event: content_block_start\ndata: {"index":0,"content_block":{"type":"text"}}\n\n'
        'event: content_block_delta\ndata: {"index":0,"delta":{"type":"text_delta","text":"Hel"}}\n\n'
        'event: content_block_delta\ndata: {"index":0,"delta":{"type":"text_delta","text":"lo"}}\n\n'
        'event: content_block_stop\ndata: {"index":0}\n\n'
        'event: content_block_start\ndata: {"index":1,"content_block":{"type":"tool_use","id":"tu1","name":"analytics_read"}}\n\n'
        'event: content_block_delta\ndata: {"index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"a\\":"}}\n\n'
        'event: content_block_delta\ndata: {"index":1,"delta":{"type":"input_json_delta","partial_json":"1}"}}\n\n'
        'event: content_block_stop\ndata: {"index":1}\n\n'
        'event: message_delta\ndata: {"delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":5}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    events = list(p.parse_sse(_chunks(raw)))
    types = [e.type for e in events]
    assert "text_delta" in types and "tool_call_start" in types
    text = "".join(e.text for e in events if e.type == "text_delta")
    assert text == "Hello"
    start = next(e for e in events if e.type == "tool_call_start")
    assert start.tool_id == "tu1" and start.tool_name == "analytics_read"
    args = "".join(e.args_fragment for e in events if e.type == "tool_args_delta")
    assert args == '{"a":1}'
    done = next(e for e in events if e.type == "message_done")
    assert done.stop_reason == StopReason.TOOL_USE


def _chunks(s: str):
    # Simulate arbitrary network chunk boundaries.
    for i in range(0, len(s), 7):
        yield s[i : i + 7]
