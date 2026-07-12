"""Unit tests for the pure materialization-plan normalizer used by
``DraftService.approve`` to turn a proposed GTM change into concrete
create/update ops before publishing. No DB / network — pure logic.

Plus focused tests for ``DraftService._materialize_and_publish`` error
translation and the reuse-path idempotency guard, driven by a fake connector
(still no DB — the publish stub returns a version id so the mock-version DB
fallback is never reached)."""

import uuid as _uuid

import pytest

from app.ask.drafts import (
    DraftPublishError,
    DraftService,
    _custom_event_filter,
    _ga4_tag_parameters,
    _materialization_plan,
)
from app.connectors.errors import ConnectorError
from app.services.implementation.coverage import _extract_gtm_events

# ---------------------------------------------------------------------------
# Small body builders
# ---------------------------------------------------------------------------


def test_custom_event_filter_shape():
    filt = _custom_event_filter("purchase")
    assert filt[0]["type"] == "equals"
    params = {p["key"]: p["value"] for p in filt[0]["parameter"]}
    assert params["arg0"] == "{{_event}}"
    assert params["arg1"] == "purchase"


def test_ga4_tag_parameters_event_name_and_settings_table():
    params = _ga4_tag_parameters(
        "purchase",
        [{"name": "value", "value": "{{DLV - value}}"}, {"name": "currency", "value": None}],
        {},
    )
    by_key = {p["key"]: p for p in params}
    assert by_key["eventName"]["value"] == "purchase"
    table = by_key["eventSettingsTable"]["list"]
    assert len(table) == 2
    first = {m["key"]: m["value"] for m in table[0]["map"]}
    assert first == {"parameter": "value", "parameterValue": "{{DLV - value}}"}
    # None param value coerces to empty string.
    second = {m["key"]: m["value"] for m in table[1]["map"]}
    assert second["parameterValue"] == ""


def test_ga4_tag_parameters_measurement_reference():
    params = _ga4_tag_parameters("purchase", [], {"measurement_id": "G-ABC123"})
    by_key = {p["key"]: p["value"] for p in params}
    assert by_key["measurementIdOverride"] == "G-ABC123"


# ---------------------------------------------------------------------------
# Shape A — Implement hub build_deploy_proposal
# ---------------------------------------------------------------------------


def _implement_payload():
    return {
        "change_type": "create",
        "entity_type": "tag",
        "entity_name": "GA4 Event — purchase",
        "proposed_config": {
            "tag": {
                "type": "gaawe",
                "name": "GA4 Event — purchase",
                "event_name": "purchase",
                "event_parameters": [{"name": "value", "value": "{{DLV - value}}", "required": True}],
            },
            "trigger": {
                "type": "customEvent",
                "name": "CE — purchase",
                "event_filter": "purchase",
            },
            "workspace_id": "42",
        },
    }


def test_plan_shape_a_orders_trigger_before_tag_and_links():
    plan = _materialization_plan(_implement_payload())
    assert [op["entity"] for op in plan] == ["trigger", "tag"]

    trig, tag = plan
    assert trig["op"] == "create"
    assert trig["gtm_type"] == "customEvent"
    assert trig["custom_event_filter"][0]["parameter"][1]["value"] == "purchase"

    assert tag["op"] == "create"
    assert tag["gtm_type"] == "gaawe"
    # Tag links to the trigger created in the same plan by name.
    assert tag["trigger_ref"] == "CE — purchase"
    ev_param = next(p for p in tag["parameters"] if p["key"] == "eventName")
    assert ev_param["value"] == "purchase"


def test_plan_shape_a_tag_only():
    payload = _implement_payload()
    del payload["proposed_config"]["trigger"]
    plan = _materialization_plan(payload)
    assert [op["entity"] for op in plan] == ["tag"]
    assert plan[0]["trigger_ref"] is None


# ---------------------------------------------------------------------------
# Shape B — chat propose_change spec
# ---------------------------------------------------------------------------


def test_plan_shape_b_create_tag_with_changes():
    payload = {
        "change_type": "create",
        "entity_type": "tag",
        "entity_name": "GA4 Event — signup",
        "proposed_config": {
            "entity_type": "tag",
            "name": "GA4 Event — signup",
            "change_type": "create",
            "changes": {
                "tag_type": "gaawe",
                "event_name": "signup",
                "firing_trigger_ids": ["101"],
            },
        },
    }
    plan = _materialization_plan(payload)
    assert len(plan) == 1
    tag = plan[0]
    assert tag["entity"] == "tag" and tag["op"] == "create"
    assert tag["gtm_type"] == "gaawe"
    assert tag["firing_trigger_ids"] == ["101"]
    assert any(p["key"] == "eventName" and p["value"] == "signup" for p in tag["parameters"])


def test_plan_shape_b_create_custom_event_trigger():
    payload = {
        "change_type": "create",
        "proposed_config": {
            "entity_type": "trigger",
            "name": "CE — add_to_cart",
            "change_type": "create",
            "changes": {"trigger_type": "customEvent", "event_name": "add_to_cart"},
        },
    }
    plan = _materialization_plan(payload)
    assert len(plan) == 1
    trig = plan[0]
    assert trig["entity"] == "trigger"
    assert trig["custom_event_filter"][0]["parameter"][1]["value"] == "add_to_cart"


def test_plan_shape_b_create_variable():
    payload = {
        "proposed_config": {
            "entity_type": "variable",
            "name": "DLV - value",
            "change_type": "create",
            "changes": {"variable_type": "v", "parameters": [{"key": "name", "value": "value"}]},
        }
    }
    plan = _materialization_plan(payload)
    assert len(plan) == 1
    var = plan[0]
    assert var["entity"] == "variable"
    assert var["gtm_type"] == "v"
    assert var["parameters"] == [{"key": "name", "value": "value"}]


def test_plan_shape_b_update_tag():
    payload = {
        "change_type": "update",
        "proposed_config": {
            "entity_type": "tag",
            "name": "Meta Pixel — Purchase",
            "change_type": "update",
            "tag_id": "77",
            "changes": {"paused": True, "notes": "deprecate"},
        },
    }
    plan = _materialization_plan(payload)
    assert len(plan) == 1
    op = plan[0]
    assert op["op"] == "update"
    assert op["tag_id"] == "77"
    assert op["updates"] == {"paused": True, "notes": "deprecate"}


def test_plan_update_without_tag_id_is_empty():
    payload = {
        "change_type": "update",
        "proposed_config": {"entity_type": "tag", "name": "x", "change_type": "update", "changes": {}},
    }
    assert _materialization_plan(payload) == []


# ---------------------------------------------------------------------------
# Legacy / underspecified drafts fall back to publish-as-is (empty plan).
# ---------------------------------------------------------------------------


def test_plan_legacy_no_config_is_empty():
    assert _materialization_plan({"target": "TAG: something", "diff": []}) == []
    assert _materialization_plan(None) == []
    assert _materialization_plan({"proposed_config": None}) == []


# ---------------------------------------------------------------------------
# _materialize_and_publish — error translation + reuse-path idempotency.
# ---------------------------------------------------------------------------

_IDS = {"connection_id": "c", "account_id": "a", "container_id": "co", "workspace_id": "5"}


class _FakeDraft:
    def __init__(self, payload):
        self.id = _uuid.uuid4()
        self.title = "Test draft"
        self.project_id = _uuid.uuid4()
        self.payload = payload


class _FakeConnector:
    """Records calls; each write can be told to raise ConnectorError; the
    ``existing_*`` overrides seed what the reuse-path pre-scan reads back."""

    def __init__(self, **overrides):
        self.calls: list = []
        self._o = overrides
        self.created_variables: list[str] = []
        self.created_triggers: list[str] = []
        self.created_tags: list[str] = []
        self.last_tag_firing: tuple = ()

    async def create_workspace(self, *a):
        self.calls.append("create_workspace")
        return {"workspace_id": "99"}

    async def create_variable(self, conn, acct, cont, ws, name, gtm_type, params):
        self.calls.append(("create_variable", name))
        if isinstance(self._o.get("create_variable"), Exception):
            raise self._o["create_variable"]
        self.created_variables.append(name)
        return {"variable_id": f"v-{name}"}

    async def create_trigger(self, conn, acct, cont, ws, name, gtm_type, filters, cef):
        self.calls.append(("create_trigger", name))
        if isinstance(self._o.get("create_trigger"), Exception):
            raise self._o["create_trigger"]
        self.created_triggers.append(name)
        return {"trigger_id": f"t-{name}"}

    async def create_tag(self, conn, acct, cont, ws, name, gtm_type, params, firing, blocking, notes):
        self.calls.append(("create_tag", name))
        if isinstance(self._o.get("create_tag"), Exception):
            raise self._o["create_tag"]
        self.created_tags.append(name)
        self.last_tag_firing = tuple(firing)
        return {"tag_id": f"tag-{name}"}

    async def update_tag(self, *a):
        self.calls.append("update_tag")
        return {"tag_id": "updated"}

    async def publish_container(self, *a):
        self.calls.append("publish_container")
        return {"version_id": "v-published"}

    async def list_variables(self, conn, acct, cont, ws):
        return {"variables": self._o.get("existing_variables", [])}

    async def list_triggers(self, conn, acct, cont, ws):
        return {"triggers": self._o.get("existing_triggers", [])}

    async def list_tags(self, conn, acct, cont, ws):
        return {"tags": self._o.get("existing_tags", [])}


async def test_connector_error_on_create_becomes_publish_error_and_skips_publish():
    # A real GTM create failure raises ConnectorError (friendly_errors), which
    # must be surfaced as DraftPublishError (-> 502) and must NOT publish.
    connector = _FakeConnector(create_tag=ConnectorError("boom", platform="GTM"))
    draft = _FakeDraft(_implement_payload())
    plan = _materialization_plan(draft.payload)

    with pytest.raises(DraftPublishError):
        await DraftService()._materialize_and_publish(connector, _IDS, draft, plan)

    assert "publish_container" not in connector.calls


async def test_reuse_path_skips_already_created_entities():
    # Retry on a reused (dedicated) workspace: the trigger already exists from a
    # prior attempt, so it must NOT be re-created, yet the tag must still link to
    # the existing trigger's id.
    connector = _FakeConnector(
        existing_triggers=[{"trigger_name": "CE — purchase", "trigger_id": "t-existing"}],
    )
    payload = _implement_payload()
    payload["gtm"] = {"dedicated_workspace": True}
    draft = _FakeDraft(payload)
    plan = _materialization_plan(payload)

    version, record = await DraftService()._materialize_and_publish(connector, _IDS, draft, plan)

    assert version == "v-published"
    assert connector.created_triggers == []  # skipped — no duplicate
    assert connector.created_tags == ["GA4 Event — purchase"]
    assert "t-existing" in connector.last_tag_firing  # still linked
    assert record["scratch_workspace_created"] is False
    assert "create_workspace" not in connector.calls  # reuse path


async def test_reuse_path_fully_populated_creates_nothing_but_publishes():
    connector = _FakeConnector(
        existing_triggers=[{"trigger_name": "CE — purchase", "trigger_id": "t-existing"}],
        existing_tags=[{"tag_name": "GA4 Event — purchase", "tag_id": "tag-existing"}],
    )
    payload = _implement_payload()
    payload["gtm"] = {"dedicated_workspace": True}
    draft = _FakeDraft(payload)
    plan = _materialization_plan(payload)

    version, _ = await DraftService()._materialize_and_publish(connector, _IDS, draft, plan)

    assert version == "v-published"
    assert connector.created_triggers == []
    assert connector.created_tags == []
    assert "publish_container" in connector.calls


async def test_fresh_scratch_path_creates_all_and_publishes():
    connector = _FakeConnector()
    draft = _FakeDraft(_implement_payload())
    plan = _materialization_plan(draft.payload)

    version, record = await DraftService()._materialize_and_publish(connector, _IDS, draft, plan)

    assert version == "v-published"
    assert connector.created_triggers == ["CE — purchase"]
    assert connector.created_tags == ["GA4 Event — purchase"]
    assert record["scratch_workspace_created"] is True
    assert "create_workspace" in connector.calls


# ---------------------------------------------------------------------------
# Coverage reader recognizes the customEventFilter shape this flow writes.
# ---------------------------------------------------------------------------


def test_coverage_reads_custom_event_filter_written_by_materialization():
    # A trigger created by this flow stores the event name under
    # ``customEventFilter`` (gtm.py). The coverage reader must recognize it.
    trigger = {
        "name": "CE — purchase",
        "type": "customEvent",
        "customEventFilter": _custom_event_filter("purchase"),
    }
    events = _extract_gtm_events([], [trigger])
    assert {"event_name": "purchase", "source": "trigger", "label": "CE — purchase"} in events
