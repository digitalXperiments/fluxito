from app.ask.tools import (
    READ_ONLY_TOOLS,
    is_allowed_call,
)


def test_write_tools_are_not_in_allowlist():
    for name in (
        "analytics_write",
        "tagmanager_write",
        "marketing_write",
        "seo_write",
        "automation_write",
        "deploy_knowledge",
        "dashboard_deploy_batch",
        "dashboard_manage_scopes",
        "dashboard_rotate_token",
        "save_audit_result",
        "generic_tool_write",
        "generic_tool_read",
        "run_script",
    ):
        assert name not in READ_ONLY_TOOLS


def test_tracking_plan_write_action_blocked():
    assert is_allowed_call("tracking_plan", {"action": "get_plan"}) is True
    assert is_allowed_call("tracking_plan", {"action": "create_event"}) is False
    assert is_allowed_call("tracking_plan", {}) is False  # no action → reject


def test_non_allowlisted_tool_blocked():
    assert is_allowed_call("analytics_write", {"action": "x"}) is False
    assert is_allowed_call("analytics_read", {"action": "list"}) is True
