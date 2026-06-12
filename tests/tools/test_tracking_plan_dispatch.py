import json
from pathlib import Path

from app.tools.unified import TRACKING_PLAN_ROUTES

_NEW_V2_ACTIONS = [
    "get_plan",
    "get_event",
    "validate",
    "create_event",
    "update_event",
    "delete_event",
    "set_event_sources",
    "set_event_destination",
    "remove_event_destination",
    "create_property",
    "update_property",
    "delete_property",
    "attach_property",
    "detach_property",
    "create_category",
    "update_category",
    "delete_category",
    "create_source",
    "update_source",
    "delete_source",
    "create_destination",
    "update_destination",
    "delete_destination",
    "connect_source_destination",
    "disconnect_source_destination",
    "create_metric",
    "update_metric",
    "delete_metric",
    "publish",
]


def test_new_actions_are_routed():
    for action in _NEW_V2_ACTIONS:
        assert action in TRACKING_PLAN_ROUTES, f"action '{action}' missing from TRACKING_PLAN_ROUTES"
        tool, legacy_action = TRACKING_PLAN_ROUTES[action]
        assert (
            tool == "tracking_plan_v2"
        ), f"action '{action}' routes to '{tool}', expected 'tracking_plan_v2'"
        assert (
            legacy_action == action
        ), f"action '{action}' has legacy_action '{legacy_action}', expected '{action}'"


def test_tracking_plan_specs_parse_and_cover_new_actions():
    spec_path = Path("app/tools/specs/data/tracking_plan.json")
    data = json.loads(spec_path.read_text())
    actions = {entry["action"] for entry in data}
    for action in ["get_plan", "create_event", "create_property", "attach_property", "publish"]:
        assert action in actions, f"spec action '{action}' missing from tracking_plan.json"
    # All v2 actions should have a spec entry
    for action in _NEW_V2_ACTIONS:
        assert action in actions, f"v2 action '{action}' has no spec entry in tracking_plan.json"
