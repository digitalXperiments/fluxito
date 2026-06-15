# tests/tools/test_tracking_plan_reconcile.py
"""Tests for Phase C reconcile — pure-function unit tests + integration tests.

Mirrors test_tracking_plan_mcp_helpers.py's _ctx_branch harness + run_action calls.
"""

from types import SimpleNamespace

import pytest

from app.services.tracking_plan import get_main_branch, get_or_create_plan
from app.services.tracking_plan.reconcile import diff_events, match_key, normalize_name
from app.tools.tracking_plan_tools import run_action
from tests.services.tracking_plan.test_models import _make_project_and_user

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


async def _ctx_branch(session, role="admin"):
    project_id, user_id = await _make_project_and_user(session)
    plan = await get_or_create_plan(session, project_id=project_id, user_id=user_id)
    branch = await get_main_branch(session, plan)
    ctx = SimpleNamespace(role=role, user_id=str(user_id), project_id=str(project_id), plan=plan)
    return ctx, branch


# ---------------------------------------------------------------------------
# Unit tests — normalize_name
# ---------------------------------------------------------------------------


def test_normalize_snake_case_from_spaced():
    assert normalize_name("Add To Cart") == "add_to_cart"


def test_normalize_snake_case_from_camel():
    assert normalize_name("addToCart") == "add_to_cart"


def test_normalize_snake_case_noop():
    assert normalize_name("add_to_cart") == "add_to_cart"


def test_normalize_camel():
    assert normalize_name("Add To Cart", casing="camelCase") == "addToCart"


def test_normalize_title():
    assert normalize_name("add_to_cart", casing="Title") == "Add To Cart"


def test_normalize_none_casing():
    assert normalize_name("Add To Cart", casing="none") == "Add To Cart"


def test_normalize_empty():
    assert normalize_name("") == ""
    assert normalize_name("   ") == ""


def test_normalize_hyphenated():
    assert normalize_name("add-to-cart") == "add_to_cart"


# ---------------------------------------------------------------------------
# Unit tests — match_key
# ---------------------------------------------------------------------------


def test_match_key_collapses_three_casings():
    assert match_key("Add To Cart") == "addtocart"
    assert match_key("add_to_cart") == "addtocart"
    assert match_key("addToCart") == "addtocart"


def test_match_key_deterministic():
    assert match_key("purchase") == match_key("purchase")


# ---------------------------------------------------------------------------
# Unit tests — diff_events (pure, no DB)
# ---------------------------------------------------------------------------


def _make_current(name, category=None, properties=None):
    return {
        "id": f"ev-{name}",
        "name": name,
        "category": category,
        "display_name": None,
        "description": None,
        "properties": properties or [],
    }


def test_diff_new_event():
    result = diff_events(
        [{"name": "checkout"}],
        [],
    )
    assert len(result["new"]) == 1
    assert result["new"][0]["name"] == "checkout"
    assert result["updated"] == []
    assert result["unchanged"] == []
    assert result["conflicts"] == []


def test_diff_unchanged_event():
    current = [_make_current("add_to_cart")]
    result = diff_events([{"name": "add_to_cart"}], current)
    assert result["unchanged"] == [{"name": "add_to_cart"}]
    assert result["new"] == []
    assert result["updated"] == []


def test_diff_fuzzy_match_camel_to_snake():
    """addToCart incoming should fuzzy-match the existing add_to_cart."""
    current = [_make_current("add_to_cart")]
    result = diff_events([{"name": "addToCart"}], current, match_strategy="fuzzy")
    # Casing mismatch conflict surfaced but event is matched (unchanged or updated)
    assert result["new"] == []
    # Should be either unchanged (no other changes) or updated with a name rename
    all_matched = result["unchanged"] + result["updated"]
    assert len(all_matched) == 1
    # Conflict about casing mismatch is added
    assert any(c["reason"] == "casing_mismatch" for c in result["conflicts"])


def test_diff_exact_strategy_no_fuzzy():
    """Exact strategy: addToCart != add_to_cart → new."""
    current = [_make_current("add_to_cart")]
    result = diff_events([{"name": "addToCart"}], current, match_strategy="exact")
    assert len(result["new"]) == 1
    assert result["new"][0]["name"] == "addToCart"
    assert result["unchanged"] == []


def test_diff_updated_description():
    current = [_make_current("purchase", category="Commerce")]
    result = diff_events(
        [{"name": "purchase", "description": "User completed purchase"}],
        current,
    )
    assert len(result["updated"]) == 1
    upd = result["updated"][0]
    assert upd["name"] == "purchase"
    assert "description" in upd["changes"]
    assert upd["changes"]["description"]["to"] == "User completed purchase"


def test_diff_updated_new_property():
    current = [_make_current("purchase", properties=[{"name": "value", "data_type": "float"}])]
    result = diff_events(
        [{"name": "purchase", "properties": [{"name": "currency", "data_type": "string"}]}],
        current,
    )
    assert len(result["updated"]) == 1
    assert "properties_to_add" in result["updated"][0]["changes"]
    assert result["updated"][0]["changes"]["properties_to_add"][0]["name"] == "currency"


def test_diff_duplicate_input_conflict():
    """Two incoming events that share a fuzzy key → duplicate_input conflict."""
    result = diff_events(
        [{"name": "Add To Cart"}, {"name": "add_to_cart"}],
        [],
        match_strategy="fuzzy",
    )
    assert any(c["reason"] == "duplicate_input" for c in result["conflicts"])
    # First occurrence wins — only one new entry
    assert len(result["new"]) == 1


def test_diff_sorted_output():
    """Output lists are sorted by name for determinism."""
    current = []
    incoming = [{"name": "z_event"}, {"name": "a_event"}, {"name": "m_event"}]
    result = diff_events(incoming, current)
    names = [e["name"] for e in result["new"]]
    assert names == sorted(names)


def test_diff_idempotent():
    """Calling diff_events twice with same inputs returns equal results."""
    current = [_make_current("purchase"), _make_current("page_view")]
    incoming = [
        {"name": "purchase", "description": "new desc"},
        {"name": "checkout"},
    ]
    r1 = diff_events(incoming, current)
    r2 = diff_events(incoming, current)
    assert r1 == r2


# ---------------------------------------------------------------------------
# Integration tests — reconcile_preview (no DB writes)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconcile_preview_no_writes(db_session_factory):
    """Preview never writes anything even when new events are in the list."""
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        # Seed one existing event
        await run_action(session, branch, ctx, "create_event", {"name": "purchase"})

        overview_before = await run_action(session, branch, ctx, "get_overview", {})
        event_count_before = overview_before["counts"]["events"]

        # Preview with one matching + one new event
        preview = await run_action(
            session,
            branch,
            ctx,
            "reconcile_preview",
            {
                "events": [
                    {"name": "Purchase"},  # fuzzy-matches "purchase"
                    {"name": "Add To Cart", "category": "Commerce"},
                ],
                "options": {"normalize_casing": True, "casing": "snake_case"},
            },
        )

        # Shape checks
        assert "error" not in preview
        assert set(preview.keys()) == {
            "new",
            "updated",
            "unchanged",
            "conflicts",
            "normalized_names",
            "summary",
        }

        # Summary counts
        assert preview["summary"]["new"] == 1
        assert preview["summary"]["unchanged"] + preview["summary"]["updated"] == 1

        # normalized_names maps inputs to snake_case outputs
        assert preview["normalized_names"]["Add To Cart"] == "add_to_cart"
        assert preview["normalized_names"]["Purchase"] == "purchase"

        # No writes occurred — event count unchanged
        overview_after = await run_action(session, branch, ctx, "get_overview", {})
        assert overview_after["counts"]["events"] == event_count_before


@pytest.mark.anyio
async def test_reconcile_preview_empty_events_error(db_session_factory):
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)
        result = await run_action(session, branch, ctx, "reconcile_preview", {"events": []})
        assert result.get("error") is True
        assert result["error_type"] == "validation_failed"


# ---------------------------------------------------------------------------
# Integration tests — reconcile_apply
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconcile_apply_create(db_session_factory):
    """Create decision: new event with properties is created and logged."""
    from sqlalchemy import select

    from app.models.tracking_plan import TPActivity

    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        result = await run_action(
            session,
            branch,
            ctx,
            "reconcile_apply",
            {
                "events": [
                    {
                        "name": "Add To Cart",
                        "description": "Item added to cart",
                        "properties": [
                            {"name": "value", "data_type": "float", "required": True, "example": "9.99"},
                        ],
                    }
                ],
                "decisions": {"add_to_cart": "create"},
                "options": {"normalize_casing": True, "casing": "snake_case"},
            },
        )

        assert result["ok"] is True
        assert len(result["created"]) == 1
        assert result["created"][0]["name"] == "add_to_cart"
        assert result["updated"] == []
        assert result["skipped"] == []
        assert result["errors"] == []

        # Event exists in the plan with the property attached
        ev = await run_action(session, branch, ctx, "get_event", {"name": "add_to_cart"})
        assert "error" not in ev
        assert ev["name"] == "add_to_cart"
        assert ev["description"] == "Item added to cart"
        prop_names = {p["name"] for p in ev["properties"]}
        assert "value" in prop_names

        # Activity row with action == "reconcile_apply" was logged
        rows = list(
            (
                await session.execute(
                    select(TPActivity)
                    .where(
                        TPActivity.plan_id == ctx.plan.id,
                        TPActivity.action == "reconcile_apply",
                    )
                    .order_by(TPActivity.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].entity_type == "event"
        assert "created" in rows[0].summary


@pytest.mark.anyio
async def test_reconcile_apply_update(db_session_factory):
    """Update decision: existing event gets description updated."""
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        # Seed event
        await run_action(session, branch, ctx, "create_event", {"name": "purchase"})

        result = await run_action(
            session,
            branch,
            ctx,
            "reconcile_apply",
            {
                "events": [
                    {
                        "name": "purchase",
                        "description": "User completed a purchase",
                        "properties": [
                            {"name": "revenue", "data_type": "float"},
                        ],
                    }
                ],
                "decisions": {"purchase": "update"},
            },
        )

        assert result["ok"] is True
        assert len(result["updated"]) == 1
        assert result["updated"][0]["name"] == "purchase"

        # Verify changes persisted
        ev = await run_action(session, branch, ctx, "get_event", {"name": "purchase"})
        assert ev["description"] == "User completed a purchase"
        prop_names = {p["name"] for p in ev["properties"]}
        assert "revenue" in prop_names


@pytest.mark.anyio
async def test_reconcile_apply_skip(db_session_factory):
    """Skip decision: event is not created."""
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        result = await run_action(
            session,
            branch,
            ctx,
            "reconcile_apply",
            {
                "events": [{"name": "page_view"}],
                "decisions": {"page_view": "skip"},
            },
        )

        assert result["ok"] is True
        assert len(result["skipped"]) == 1
        assert result["created"] == []

        # Event was not created
        ev = await run_action(session, branch, ctx, "get_event", {"name": "page_view"})
        assert ev.get("error") is True


@pytest.mark.anyio
async def test_reconcile_apply_default_skip(db_session_factory):
    """Events with no decision entry default to skip."""
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        result = await run_action(
            session,
            branch,
            ctx,
            "reconcile_apply",
            {
                "events": [{"name": "unmentioned_event"}],
                "decisions": {},
            },
        )

        assert result["ok"] is True
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["name"] == "unmentioned_event"


@pytest.mark.anyio
async def test_reconcile_apply_mixed_decisions(db_session_factory):
    """Create, update, and skip in the same call — batch continues on each."""
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        # Seed one event to update
        await run_action(session, branch, ctx, "create_event", {"name": "purchase"})

        result = await run_action(
            session,
            branch,
            ctx,
            "reconcile_apply",
            {
                "events": [
                    {"name": "add_to_cart"},
                    {"name": "purchase", "description": "Updated"},
                    {"name": "page_view"},
                ],
                "decisions": {
                    "add_to_cart": "create",
                    "purchase": "update",
                    "page_view": "skip",
                },
            },
        )

        assert result["ok"] is True
        assert len(result["created"]) == 1
        assert len(result["updated"]) == 1
        assert len(result["skipped"]) == 1
        assert result["errors"] == []


@pytest.mark.anyio
async def test_reconcile_apply_conflict_error_continues_batch(db_session_factory):
    """ConflictError on create (name already exists) → errors entry; batch continues."""
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        # Pre-create event so apply's "create" gets a ConflictError
        await run_action(session, branch, ctx, "create_event", {"name": "purchase"})

        result = await run_action(
            session,
            branch,
            ctx,
            "reconcile_apply",
            {
                "events": [
                    {"name": "purchase"},
                    {"name": "checkout"},
                ],
                "decisions": {
                    "purchase": "create",  # conflict — already exists
                    "checkout": "create",  # should succeed
                },
            },
        )

        assert result["ok"] is True
        # checkout succeeds
        assert any(e["name"] == "checkout" for e in result["created"])
        # purchase → errors entry
        assert any(e["name"] == "purchase" and "conflict" in e["error"] for e in result["errors"])


@pytest.mark.anyio
async def test_reconcile_apply_update_not_found_continues(db_session_factory):
    """Update decision for non-existent event → errors entry; batch continues."""
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        result = await run_action(
            session,
            branch,
            ctx,
            "reconcile_apply",
            {
                "events": [
                    {"name": "ghost_event"},
                    {"name": "new_event"},
                ],
                "decisions": {
                    "ghost_event": "update",  # not in plan
                    "new_event": "create",
                },
            },
        )

        assert result["ok"] is True
        assert any(e["name"] == "ghost_event" for e in result["errors"])
        assert any(e["name"] == "new_event" for e in result["created"])


@pytest.mark.anyio
async def test_reconcile_apply_fuzzy_update(db_session_factory):
    """Update with fuzzy matching: incoming 'addToCart' matches 'add_to_cart'."""
    async with db_session_factory() as session:
        ctx, branch = await _ctx_branch(session)

        await run_action(session, branch, ctx, "create_event", {"name": "add_to_cart"})

        result = await run_action(
            session,
            branch,
            ctx,
            "reconcile_apply",
            {
                "events": [{"name": "Add To Cart", "description": "Fuzzy matched"}],
                "decisions": {"add_to_cart": "update"},
                "options": {"normalize_casing": True, "casing": "snake_case", "match_strategy": "fuzzy"},
            },
        )

        assert result["ok"] is True
        assert len(result["updated"]) == 1

        ev = await run_action(session, branch, ctx, "get_event", {"name": "add_to_cart"})
        assert ev["description"] == "Fuzzy matched"
