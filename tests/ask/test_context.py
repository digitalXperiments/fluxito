from app.ask.context import window_history
from app.ask.providers.base import LLMMessage, TextBlock, ToolResultBlock, ToolUseBlock


def _u(t):
    return LLMMessage(role="user", content=[TextBlock(text=t)])


def test_window_keeps_recent_messages_under_budget():
    msgs = [_u(f"m{i}") for i in range(20)]
    out = window_history(msgs, max_messages=5)
    assert len(out) == 5
    assert out[-1].content[0].text == "m19"


def test_window_never_starts_on_an_orphan_tool_result():
    # A tool_result must be preceded by its assistant tool_use; if the cut lands
    # between them, drop the dangling tool_result so the first kept message is valid.
    msgs = [
        LLMMessage(role="assistant", content=[ToolUseBlock(id="t1", name="x", input={})]),
        LLMMessage(role="tool", content=[ToolResultBlock(tool_use_id="t1", content="ok")]),
        _u("hello"),
    ]
    out = window_history(msgs, max_messages=2)
    # The naive last-2 would be [tool_result, user] — invalid. Expect the tool turn dropped.
    assert out[0].role != "tool"
