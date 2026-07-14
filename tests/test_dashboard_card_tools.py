"""Tests for the incremental card-tool surface: dashboard_create,
dashboard_card_preview, dashboard_card_upsert, dashboard_card_remove.

Mirrors the fixture style of tests/test_dashboard_card_validation.py (pure
validation helpers) and tests/test_rbac_data_leakage.py / test_tool_audit_hook.py
(registering a real FastMCP server + wiring app_state to the test DB so the
tool closures can run end-to-end against Postgres).

NOTE on contextvars: ``current_user_ctx``/``current_project_ctx`` must be set
INSIDE the test coroutine itself, not in a fixture — pytest-asyncio runs each
async fixture and each async test as separate asyncio Tasks, and a Task's
context mutations don't propagate to a sibling Task. ``_user_ctx()`` below is
a plain (sync) context manager used directly in each test body, matching the
pattern in test_rbac_data_leakage.py / test_tool_audit_hook.py.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

import app.app_state as app_state
from app.auth.mcp_session_manager import UserContext
from app.auth.permissions import DOMAIN_TOOLS, EffectivePermissions
from app.models.dashboard import Dashboard, DashboardCard
from app.models.user import User
from app.tools.dashboard_tools import register_dashboard_tools

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


class _FakeTool:
    """Stand-in for a FastMCP ``Tool`` — just enough for
    ``query_engine.resolve_tool``/``dispatch`` to call ``tool.run(call_args)``
    without going through FastMCP's typed-argument validation (which would
    reject an open-ended ``**kwargs`` stub)."""

    def __init__(self, fn):
        self._fn = fn

    async def run(self, call_args, *_args, **_kwargs):
        return await self._fn(call_args)


async def _ga4_stub(call_args: dict) -> dict:
    """Canned GA4 run_report-shaped response for preview tests."""
    return {
        "dimension_headers": ["sessionDefaultChannelGroup"],
        "metric_headers": ["sessions"],
        "rows": [
            {"dimension_values": ["Organic Search"], "metric_values": ["120"]},
            {"dimension_values": ["Direct"], "metric_values": ["80"]},
        ],
    }


def _build_server() -> FastMCP:
    server = FastMCP(name="dashboard-card-tools-test")
    register_dashboard_tools(server)
    # Register a fake analytics_read tool the cards can dispatch to.
    server._tool_manager._tools["analytics_read"] = _FakeTool(_ga4_stub)
    return server


def _tool(server: FastMCP, name: str):
    """Return the raw async function behind an MCP tool, bypassing FastMCP's
    typed-argument validation/audit wrapper — our card payloads are plain
    dicts, so calling the closure directly keeps assertions simple."""
    return server._tool_manager._tools[name].fn


async def _make_user(db_session_factory) -> uuid.UUID:
    async with db_session_factory() as db:
        u = User(email=f"cardtools-{uuid.uuid4().hex[:8]}@example.com")
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


@contextmanager
def _user_ctx(uid: uuid.UUID):
    """Set current_user_ctx/current_project_ctx for the duration of the
    `with` block, resetting on exit. Must be entered from inside the test
    coroutine (see module docstring)."""
    ctx = UserContext(user_id=str(uid), email="owner@example.com", display_name="Owner")
    user_tok = app_state.current_user_ctx.set(ctx)
    proj_tok = app_state.current_project_ctx.set(None)
    try:
        yield ctx
    finally:
        app_state.current_user_ctx.reset(user_tok)
        app_state.current_project_ctx.reset(proj_tok)


@pytest.fixture
def wired(db_session_factory):
    """Point app_state at the test DB; reset afterward. Does NOT touch
    contextvars — use ``_user_ctx()`` inside the test body for that."""
    prev_factory = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    try:
        yield
    finally:
        app_state.db_session_factory = prev_factory


def _sample_card(key: str = "channel_bar") -> dict:
    return {
        "key": key,
        "title": "Traffic by Channel",
        "chart_type": "bar",
        "platform": "ga4",
        "tool": "analytics_read",
        "action": "run_report",
        "params": {
            "property_id": "279951751",
            "metrics": ["sessions"],
            "dimensions": ["sessionDefaultChannelGroup"],
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
        },
    }


async def _count_dashboards(db_session_factory) -> int:
    async with db_session_factory() as db:
        result = await db.execute(select(Dashboard))
        return len(result.scalars().all())


# ---------------------------------------------------------------------------
# dashboard_create
# ---------------------------------------------------------------------------


async def test_dashboard_create_empty_shell(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        resp = await _tool(server, "dashboard_create")(title="My New Dashboard")

    assert "error" not in resp
    assert resp["slug"]
    assert resp["url"].endswith(f"/live-dashboards/{resp['slug']}")

    async with db_session_factory() as db:
        result = await db.execute(select(Dashboard).where(Dashboard.id == uuid.UUID(resp["dashboard_id"])))
        dash = result.scalar_one()
        assert dash.title == "My New Dashboard"

        cards_result = await db.execute(select(DashboardCard).where(DashboardCard.dashboard_id == dash.id))
        assert cards_result.scalars().all() == []


async def test_dashboard_create_requires_title(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        resp = await _tool(server, "dashboard_create")(title="")
    assert resp["error"] is True


# ---------------------------------------------------------------------------
# dashboard_card_preview — persists nothing
# ---------------------------------------------------------------------------


async def test_card_preview_returns_snap_without_persisting(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    before = await _count_dashboards(db_session_factory)

    with _user_ctx(uid):
        resp = await _tool(server, "dashboard_card_preview")(
            platform="ga4",
            tool="analytics_read",
            action="run_report",
            params={
                "property_id": "279951751",
                "metrics": ["sessions"],
                "dimensions": ["sessionDefaultChannelGroup"],
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            },
            chart_type="bar",
            chart_config=None,
        )

    assert "error" not in resp
    assert "snap" in resp
    assert resp["normalized_spec"]["chart_type"] == "bar"
    assert resp["warnings"] == []

    # No dashboard/card rows were created anywhere.
    after = await _count_dashboards(db_session_factory)
    assert after == before
    async with db_session_factory() as db:
        result = await db.execute(select(DashboardCard))
        assert result.scalars().all() == []


async def test_card_preview_rejects_invalid_chart_type(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        resp = await _tool(server, "dashboard_card_preview")(
            platform="ga4",
            tool="analytics_read",
            action="run_report",
            params={"property_id": "1", "metrics": ["sessions"], "dimensions": ["date"]},
            chart_type="not_a_real_type",
        )
    assert resp["error"] is True
    assert resp["error_type"] == "invalid_card_spec"


async def test_card_preview_rejects_missing_required_params(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        resp = await _tool(server, "dashboard_card_preview")(
            platform="ga4",
            tool="analytics_read",
            action="run_report",
            params={"property_id": "1"},  # missing metrics/dimensions/dates
            chart_type="bar",
        )
    assert resp["error"] is True
    assert resp["error_type"] == "invalid_card_spec"


# ---------------------------------------------------------------------------
# dashboard_card_upsert — new card + update by key
# ---------------------------------------------------------------------------


async def test_card_upsert_adds_new_card(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        created = await _tool(server, "dashboard_create")(title="Upsert Target")
        slug = created["slug"]
        resp = await _tool(server, "dashboard_card_upsert")(dashboard_slug=slug, card=_sample_card())

    assert "error" not in resp
    assert resp["card_key"] == "channel_bar"
    assert resp["position"] == 0

    async with db_session_factory() as db:
        result = await db.execute(
            select(DashboardCard).where(DashboardCard.dashboard_id == uuid.UUID(created["dashboard_id"]))
        )
        cards = result.scalars().all()
        assert len(cards) == 1
        assert cards[0].title == "Traffic by Channel"
        assert (cards[0].query_params or {}).get("key") == "channel_bar"


async def test_card_upsert_updates_existing_card_by_key(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        created = await _tool(server, "dashboard_create")(title="Update Target")
        slug = created["slug"]
        await _tool(server, "dashboard_card_upsert")(dashboard_slug=slug, card=_sample_card())

        updated_card = _sample_card()
        updated_card["title"] = "Channel Mix (renamed)"
        resp = await _tool(server, "dashboard_card_upsert")(dashboard_slug=slug, card=updated_card)

    assert resp["card_key"] == "channel_bar"
    assert resp["position"] == 0  # same position — updated in place, not appended

    async with db_session_factory() as db:
        result = await db.execute(
            select(DashboardCard).where(DashboardCard.dashboard_id == uuid.UUID(created["dashboard_id"]))
        )
        cards = result.scalars().all()
        # Still exactly one card — the key matched, so it updated rather than
        # appending a second row.
        assert len(cards) == 1
        assert cards[0].title == "Channel Mix (renamed)"


async def test_card_upsert_unknown_dashboard_slug(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        resp = await _tool(server, "dashboard_card_upsert")(
            dashboard_slug="does-not-exist", card=_sample_card()
        )
    assert resp["error"] is True


async def test_card_upsert_rejects_invalid_card_spec(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        created = await _tool(server, "dashboard_create")(title="Bad Card Target")
        bad_card = _sample_card()
        bad_card["chart_type"] = "not_a_type"
        resp = await _tool(server, "dashboard_card_upsert")(dashboard_slug=created["slug"], card=bad_card)
    assert resp["error"] is True
    assert resp["error_type"] == "invalid_card_spec"


# ---------------------------------------------------------------------------
# dashboard_card_upsert — 20-card cap
# ---------------------------------------------------------------------------


async def test_card_upsert_enforces_20_card_cap(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        created = await _tool(server, "dashboard_create")(title="Full Dashboard")
        slug = created["slug"]

        for i in range(20):
            resp = await _tool(server, "dashboard_card_upsert")(
                dashboard_slug=slug, card=_sample_card(f"card_{i}")
            )
            assert "error" not in resp, resp

        # The 21st NEW card is rejected with a friendly, actionable error.
        resp = await _tool(server, "dashboard_card_upsert")(dashboard_slug=slug, card=_sample_card("card_21"))
        assert resp["error"] is True
        assert resp["error_type"] == "card_limit_reached"

        # Updating one of the existing 20 (same key) is still allowed — the
        # cap only blocks NEW cards.
        existing_update = _sample_card("card_0")
        existing_update["title"] = "Card 0 renamed"
        resp = await _tool(server, "dashboard_card_upsert")(dashboard_slug=slug, card=existing_update)
        assert "error" not in resp


# ---------------------------------------------------------------------------
# dashboard_card_upsert — scope auto-extension
# ---------------------------------------------------------------------------


async def test_card_upsert_auto_extends_query_scopes(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        created = await _tool(server, "dashboard_create")(title="Scope Target")
        slug = created["slug"]

        async with db_session_factory() as db:
            result = await db.execute(
                select(Dashboard).where(Dashboard.id == uuid.UUID(created["dashboard_id"]))
            )
            assert result.scalar_one().query_scopes == []

        await _tool(server, "dashboard_card_upsert")(dashboard_slug=slug, card=_sample_card())

        async with db_session_factory() as db:
            result = await db.execute(
                select(Dashboard).where(Dashboard.id == uuid.UUID(created["dashboard_id"]))
            )
            dash = result.scalar_one()
            assert dash.query_scopes == [{"platform": "ga4", "property_id": "279951751"}]

        # Upserting the SAME card again must not duplicate the scope entry.
        await _tool(server, "dashboard_card_upsert")(dashboard_slug=slug, card=_sample_card())
        async with db_session_factory() as db:
            result = await db.execute(
                select(Dashboard).where(Dashboard.id == uuid.UUID(created["dashboard_id"]))
            )
            assert result.scalar_one().query_scopes == [{"platform": "ga4", "property_id": "279951751"}]


# ---------------------------------------------------------------------------
# dashboard_card_remove
# ---------------------------------------------------------------------------


async def test_card_remove_deletes_by_key(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        created = await _tool(server, "dashboard_create")(title="Remove Target")
        slug = created["slug"]
        await _tool(server, "dashboard_card_upsert")(dashboard_slug=slug, card=_sample_card())

        resp = await _tool(server, "dashboard_card_remove")(dashboard_slug=slug, card_key="channel_bar")

    assert resp["success"] is True
    assert resp["removed_card_key"] == "channel_bar"
    assert resp["remaining_cards"] == 0

    async with db_session_factory() as db:
        result = await db.execute(
            select(DashboardCard).where(DashboardCard.dashboard_id == uuid.UUID(created["dashboard_id"]))
        )
        assert result.scalars().all() == []


async def test_card_remove_unknown_key(wired, db_session_factory):
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        created = await _tool(server, "dashboard_create")(title="Remove Target 2")
        resp = await _tool(server, "dashboard_card_remove")(dashboard_slug=created["slug"], card_key="nope")
    assert resp["error"] is True


async def test_card_remove_does_not_touch_tp_metrics(wired, db_session_factory):
    """dashboard_card_remove's docstring notes that migration 064
    (tp_members_type_cleanup) already dropped tp_metrics.dashboard_card_id —
    the column this tool would otherwise need to null out no longer exists.
    Guard that assumption: TPMetric must not carry that column, and removing
    a card must not error trying to touch it."""
    from app.models.tracking_plan import TPMetric

    assert not hasattr(TPMetric, "dashboard_card_id")

    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        created = await _tool(server, "dashboard_create")(title="TP Metric Guard")
        slug = created["slug"]
        await _tool(server, "dashboard_card_upsert")(dashboard_slug=slug, card=_sample_card())

        resp = await _tool(server, "dashboard_card_remove")(dashboard_slug=slug, card_key="channel_bar")
    assert resp["success"] is True


# ---------------------------------------------------------------------------
# RBAC — DOMAIN_TOOLS membership + EffectivePermissions gating
# ---------------------------------------------------------------------------


def test_new_card_tools_are_in_domain_tools_map():
    assert DOMAIN_TOOLS["dashboards"]["read"] == {"dashboard_read", "dashboard_card_preview"}
    assert {
        "dashboard_deploy_batch",
        "dashboard_manage_scopes",
        "dashboard_rotate_token",
        "dashboard_create",
        "dashboard_card_upsert",
        "dashboard_card_remove",
    } <= DOMAIN_TOOLS["dashboards"]["write"]


def test_dashboards_read_grant_allows_preview_not_write():
    eff = EffectivePermissions(full=False, tools={"dashboards": {"read"}})
    assert eff.allows_tool("dashboard_read") is True
    assert eff.allows_tool("dashboard_card_preview") is True
    assert eff.allows_tool("dashboard_create") is False
    assert eff.allows_tool("dashboard_card_upsert") is False
    assert eff.allows_tool("dashboard_card_remove") is False


def test_dashboards_write_grant_allows_new_write_tools():
    eff = EffectivePermissions(full=False, tools={"dashboards": {"write"}})
    assert eff.allows_tool("dashboard_create") is True
    assert eff.allows_tool("dashboard_card_upsert") is True
    assert eff.allows_tool("dashboard_card_remove") is True


def test_no_dashboards_grant_denies_new_tools():
    eff = EffectivePermissions(full=False, tools={})
    assert eff.allows_tool("dashboard_card_preview") is False
    assert eff.allows_tool("dashboard_create") is False
    assert eff.allows_tool("dashboard_card_upsert") is False
    assert eff.allows_tool("dashboard_card_remove") is False
