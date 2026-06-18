from app.ask.providers.base import (
    StopReason,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    blocks_from_json,
    blocks_to_json,
)


def test_content_block_round_trip_through_json():
    blocks = [
        TextBlock(text="hello"),
        ToolUseBlock(id="t1", name="analytics_read", input={"action": "list"}),
        ToolResultBlock(tool_use_id="t1", content="{}", is_error=False),
    ]
    raw = blocks_to_json(blocks)
    assert isinstance(raw, list) and raw[0] == {"type": "text", "text": "hello"}
    restored = blocks_from_json(raw)
    assert restored == blocks


def test_stop_reason_is_string_enum():
    assert StopReason.TOOL_USE.value == "tool_use"


def test_stream_event_defaults():
    ev = StreamEvent(type="text_delta", text="hi")
    assert ev.text == "hi" and ev.tool_id is None
