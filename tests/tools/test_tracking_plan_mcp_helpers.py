# tests/tools/test_tracking_plan_mcp_helpers.py
"""Tests for the two MCP orchestration helpers added on top of the Plan-1A
services: get_overview (concise read) and create_event_with_properties
(atomic event + find-or-create property attach)."""

from types import SimpleNamespace

import pytest

from app.services.tracking_plan import get_main_branch, get_or_create_plan
from app.tools.tracking_plan_tools import run_action
from tests.services.tracking_plan.test_models import _make_project_and_user


async def _ctx_branch(session, role="admin"):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    branch = await get_main_branch(session, plan)
    ctx = SimpleNamespace(role=role, user_id=str(user_id), project_id=str(project_id), plan=plan)
    return ctx, branch


@pytest.mark.anyio
async def test_get_overview_keys_and_counts(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        cat = await run_action(session, branch, ctx, "create_category", {"name": "Commerce"})
        await run_action(
            session,
            branch,
            ctx,
            "create_event",
            {"name": "purchase", "category_id": cat["id"], "purpose": "money"},
        )

        overview = await run_action(session, branch, ctx, "get_overview", {})

        # Top-level shape
        assert set(overview.keys()) == {
            "plan",
            "branch",
            "counts",
            "categories",
            "events",
            "sources",
            "destinations",
            "health",
        }
        assert "error" not in overview

        # Branch + plan summaries
        assert overview["branch"]["is_main"] is True
        assert overview["branch"]["name"] == branch.name
        assert "name" in overview["plan"]

        # Counts reflect the one event + one category we created
        assert set(overview["counts"].keys()) == {
            "events",
            "event_properties",
            "user_properties",
            "sources",
            "destinations",
            "metrics",
            "categories",
            "bundles",
        }
        assert overview["counts"]["events"] == 1
        assert overview["counts"]["categories"] == 1
        assert overview["counts"]["sources"] == 0

        # Per-category event count
        assert overview["categories"] == [{"name": "Commerce", "event_count": 1}]

        # Lightweight event row
        assert overview["events"] == [
            {
                "name": "purchase",
                "category": "Commerce",
                "property_count": 0,
                "source_count": 0,
                "destination_count": 0,
            }
        ]

        # Health snapshot — event with no source/destination raises warnings.
        # is_publishable is only False when there are *error*-severity findings;
        # warnings do not block publishing.
        assert set(overview["health"]["findings_by_severity"].keys()) == {"warning", "info"}
        assert overview["health"]["findings_by_severity"]["warning"] >= 1
        assert overview["health"]["is_publishable"] is True


@pytest.mark.anyio
async def test_create_event_with_properties_find_or_create(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        # An existing event-kind library property to be reused by name.
        existing = await run_action(
            session, branch, ctx, "create_property", {"name": "currency", "data_type": "string"}
        )
        assert existing["ok"] is True

        result = await run_action(
            session,
            branch,
            ctx,
            "create_event_with_properties",
            {
                "name": "purchase",
                "purpose": "money",
                "properties": [
                    {"name": "value", "data_type": "float", "required": True, "example": "9.99"},
                    {"name": "currency", "required": True},
                ],
            },
        )

        assert result["ok"] is True
        assert result["name"] == "purchase"
        assert result["skipped"] == []

        attached = {a["name"]: a for a in result["attached"]}
        assert set(attached) == {"value", "currency"}
        # 'value' is brand new; 'currency' reuses the existing library property.
        assert attached["value"]["created"] is True
        assert attached["currency"]["created"] is False
        assert attached["currency"]["property_id"] == existing["id"]

        # The plan now shows the event with BOTH properties attached.
        plan = await run_action(session, branch, ctx, "get_plan", {})
        ev = next(e for e in plan["events"] if e["name"] == "purchase")
        prop_names = {p["name"] for p in ev["properties"]}
        assert prop_names == {"value", "currency"}

        # Find-or-create did not duplicate the reused 'currency' library property.
        currencies = [p for p in plan["properties"]["event"] if p["name"] == "currency"]
        assert len(currencies) == 1


@pytest.mark.anyio
async def test_create_event_with_properties_by_id(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        prop = await run_action(
            session, branch, ctx, "create_property", {"name": "plan_tier", "data_type": "string"}
        )

        result = await run_action(
            session,
            branch,
            ctx,
            "create_event_with_properties",
            {"name": "signup", "properties": [{"property_id": prop["id"]}]},
        )

        assert result["ok"] is True
        assert len(result["attached"]) == 1
        row = result["attached"][0]
        assert row["property_id"] == prop["id"]
        assert row["name"] == "plan_tier"
        assert row["created"] is False
