from app.ask.tools import (
    BASE_TOOLS,
    READ_ONLY_TOOLS,
    SECTION_TOOLS,
    allowed_tools_for,
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


def test_base_alias_matches_read_only():
    assert READ_ONLY_TOOLS is BASE_TOOLS
    assert "tagmanager_write" not in BASE_TOOLS


def test_implement_section_enables_propose_change_only():
    # tagmanager_write is only unlocked in the implement section...
    assert "tagmanager_write" in SECTION_TOOLS["implement"]
    assert is_allowed_call("tagmanager_write", {"action": "propose_change"}, "implement") is True
    # ...and even there, only the propose_change action is permitted.
    assert is_allowed_call("tagmanager_write", {"action": "create_tag"}, "implement") is False
    assert is_allowed_call("tagmanager_write", {"action": "publish_container"}, "implement") is False
    assert is_allowed_call("tagmanager_write", {}, "implement") is False


def test_other_sections_do_not_enable_tagmanager_write():
    for section in (None, "home", "plan", "report", "audit", "context", "settings"):
        assert is_allowed_call("tagmanager_write", {"action": "propose_change"}, section) is False


def test_allowed_tools_for_section():
    assert "tagmanager_write" in allowed_tools_for("implement")
    assert "tagmanager_write" not in allowed_tools_for("report")
    assert "tagmanager_write" not in allowed_tools_for(None)
    # base read tools are always present
    assert "analytics_read" in allowed_tools_for("implement")
    assert "analytics_read" in allowed_tools_for(None)
