"""
Spec-engine drift guard + conformance.

These tests make the Phase-1 stress-test failures structurally impossible:

* Every routed action of a registry-backed tool has an ActionSpec, and vice-versa
  (no advertised-but-dead actions, no undocumented actions).
* Every dispatcher route points at a legacy tool that is actually reachable
  (catches "Internal: legacy tool '…' not found").
* The served schema is strict-client safe and its action enum matches the routes.
* `describe` discovery and the self-describing missing-param error work end-to-end.
"""

from __future__ import annotations

import pytest

from app.tools import specs
from app.tools.spec_engine import strict_safe_issues


@pytest.fixture(scope="module")
def mcp_server():
    from mcp.server.fastmcp import FastMCP

    from app.tools.registry import register_all_tools

    mcp = FastMCP(name="test-fluxito-specs")
    register_all_tools(mcp)
    return mcp


@pytest.fixture(scope="module")
def tool_manager(mcp_server):
    return mcp_server._tool_manager


def _all_route_tables() -> dict[str, dict]:
    from app.tools import unified as u

    return {
        "ANALYTICS_READ_ROUTES": u.ANALYTICS_READ_ROUTES,
        "ANALYTICS_WRITE_ROUTES": u.ANALYTICS_WRITE_ROUTES,
        "TAGMANAGER_READ_ROUTES": u.TAGMANAGER_READ_ROUTES,
        "TAGMANAGER_WRITE_ROUTES": u.TAGMANAGER_WRITE_ROUTES,
        "MARKETING_READ_ROUTES": u.MARKETING_READ_ROUTES,
        "MARKETING_WRITE_ROUTES": u.MARKETING_WRITE_ROUTES,
        "WAREHOUSE_READ_ROUTES": u.WAREHOUSE_READ_ROUTES,
        "SEO_READ_ROUTES": u.SEO_READ_ROUTES,
        "SEO_WRITE_ROUTES": u.SEO_WRITE_ROUTES,
        "DASHBOARD_READ_ROUTES": u.DASHBOARD_READ_ROUTES,
        "KNOWLEDGE_ROUTES": u.KNOWLEDGE_ROUTES,
        "DEPLOY_KNOWLEDGE_ROUTES": u.DEPLOY_KNOWLEDGE_ROUTES,
        "TRACKING_PLAN_ROUTES": u.TRACKING_PLAN_ROUTES,
        "AUTOMATION_READ_ROUTES": u.AUTOMATION_READ_ROUTES,
        "AUTOMATION_WRITE_ROUTES": u.AUTOMATION_WRITE_ROUTES,
        "AUDIT_ROUTES": u.AUDIT_ROUTES,
        "ANALYSIS_ROUTES": u.ANALYSIS_ROUTES,
    }


# Map each registry-backed tool to its authoritative route table.
def _routes_for_tool() -> dict[str, dict]:
    from app.tools import unified as u

    return {
        "analytics_read": u.ANALYTICS_READ_ROUTES,
        "analytics_write": u.ANALYTICS_WRITE_ROUTES,
        "tagmanager_read": u.TAGMANAGER_READ_ROUTES,
        "tagmanager_write": u.TAGMANAGER_WRITE_ROUTES,
        "marketing_read": u.MARKETING_READ_ROUTES,
        "marketing_write": u.MARKETING_WRITE_ROUTES,
        "warehouse_read": u.WAREHOUSE_READ_ROUTES,
        "seo_read": u.SEO_READ_ROUTES,
        "seo_write": u.SEO_WRITE_ROUTES,
        "dashboard_read": u.DASHBOARD_READ_ROUTES,
        "get_knowledge": u.KNOWLEDGE_ROUTES,
        "deploy_knowledge": u.DEPLOY_KNOWLEDGE_ROUTES,
        "tracking_plan": u.TRACKING_PLAN_ROUTES,
        "automation_read": u.AUTOMATION_READ_ROUTES,
        "automation_write": u.AUTOMATION_WRITE_ROUTES,
        "run_audit": u.AUDIT_ROUTES,
        "run_analysis": u.ANALYSIS_ROUTES,
    }


# ── Broad reachability guard (catches FINDINGS S0 #1 for EVERY tool) ──────────


def test_every_route_points_at_a_reachable_legacy_tool(tool_manager):
    """A route whose legacy tool is not in _legacy_tools 500s at runtime."""
    legacy = tool_manager._legacy_tools
    broken: list[str] = []
    for table_name, table in _all_route_tables().items():
        for action, route in table.items():
            legacy_tool_name = route[0]
            if legacy_tool_name not in legacy:
                broken.append(f"{table_name}:{action} -> {legacy_tool_name}")
    assert not broken, "Routes pointing at unreachable legacy tools:\n" + "\n".join(broken)


# ── Drift guard: registry <-> routes (catches S0 #2 + S3) ────────────────────


@pytest.mark.parametrize("tool", sorted(_routes_for_tool()))
def test_registry_matches_routes(tool):
    routes = set(_routes_for_tool()[tool])
    spec_actions = {s.action for s in specs.specs_for(tool)}
    assert spec_actions == routes, (
        f"{tool}: registry vs routes mismatch — "
        f"only in routes: {sorted(routes - spec_actions)}; "
        f"only in registry: {sorted(spec_actions - routes)}"
    )


@pytest.mark.parametrize("tool", sorted(_routes_for_tool()))
def test_no_duplicate_specs(tool):
    actions = [s.action for s in specs.specs_for(tool)]
    dupes = {a for a in actions if actions.count(a) > 1}
    assert not dupes, f"{tool}: duplicate specs for {sorted(dupes)}"


# ── Served schema conformance ────────────────────────────────────────────────


@pytest.mark.parametrize("tool", sorted(specs.SPECS))
def test_served_schema_is_strict_safe(tool_manager, tool):
    schema = tool_manager._tools[tool].parameters
    issues = strict_safe_issues(schema)
    assert not issues, f"{tool}: schema not strict-safe: {issues}"


@pytest.mark.parametrize("tool", sorted(_routes_for_tool()))
def test_action_enum_matches_routes_plus_describe(tool_manager, tool):
    routes = set(_routes_for_tool()[tool])
    enum = set(tool_manager._tools[tool].parameters["properties"]["action"]["enum"])
    assert enum == routes | {"describe"}, (
        f"{tool}: action enum != routes + describe. " f"diff: {sorted(enum ^ (routes | {'describe'}))}"
    )


@pytest.mark.parametrize("tool", sorted(specs.SPECS))
def test_description_is_generated_and_lists_actions(tool_manager, tool):
    desc = tool_manager._tools[tool].description
    assert "action='describe'" in desc, f"{tool}: generated description missing describe hint"
    # Every action should be named in the description.
    missing = [s.action for s in specs.specs_for(tool) if s.action not in desc]
    assert not missing, f"{tool}: actions absent from description: {missing}"


# ── Behaviour: describe discovery + self-describing errors ───────────────────


async def test_describe_lists_all_actions(tool_manager):
    tool = tool_manager._tools["analytics_read"]
    out = await tool.fn(action="describe")
    assert out["tool"] == "analytics_read"
    actions = {a["action"] for a in out["actions"]}
    assert {"run_report", "list_properties"} <= actions
    # describe itself is not a documented action
    assert "describe" not in actions


async def test_describe_single_action_has_full_spec(tool_manager):
    tool = tool_manager._tools["analytics_read"]
    out = await tool.fn(action="describe", params={"action": "run_report"})
    spec = out["spec"]
    assert spec["action"] == "run_report"
    req = {p["name"] for p in spec["required"]}
    assert {"platform", "property_id", "start_date", "end_date", "metrics"} <= req
    assert spec["example"]["params"]  # a runnable example is present


async def test_missing_param_returns_self_describing_error(tool_manager):
    tool = tool_manager._tools["analytics_read"]
    out = await tool.fn(action="run_report", params={"platform": "ga4"})
    assert out["error"] is True
    assert out["error_type"] == "missing_required_param"
    assert "property_id" in out["missing"]
    assert set(out["required"]) >= {"platform", "property_id", "start_date", "end_date", "metrics"}
    assert "example" in out and out["example"]["params"]


async def test_describe_action_accepted_by_arg_model(tool_manager):
    """End-to-end: describe survives pydantic arg validation (in the Literal)."""
    tool = tool_manager._tools["run_audit"]
    out = await tool.run({"action": "describe"})
    # tool.run wraps the result; just assert it didn't raise and returned actions.
    payload = out[1] if isinstance(out, tuple) else out
    text = str(payload)
    assert "actions" in text or "gtm_audit_container" in text


def test_error_types_are_in_closed_vocab():
    from app.tools.spec_engine import ERROR_TYPES

    # The envelope we emit must use a documented error_type.
    assert "missing_required_param" in ERROR_TYPES
    assert "unknown_action" in ERROR_TYPES
