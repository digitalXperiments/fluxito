"""Native card MCP writers are unregistered. Hosted deploy/bind is the path.

Leftover helpers (`_validate_card_specs`) stay for leftover-card read/render
and out-of-scope validation tests. These tests assert the five writers cannot
mutate Dashboard/DashboardCard and that dashboards.write grants hosted tools.
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

_RETIRED_WRITERS = (
    "dashboard_deploy_batch",
    "dashboard_create",
    "dashboard_card_upsert",
    "dashboard_card_remove",
    "dashboard_card_preview",
)


def _build_server() -> FastMCP:
    server = FastMCP(name="dashboard-card-tools-test")
    register_dashboard_tools(server)
    return server


async def _make_user(db_session_factory) -> uuid.UUID:
    async with db_session_factory() as db:
        u = User(email=f"cardtools-{uuid.uuid4().hex[:8]}@example.com")
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


@contextmanager
def _user_ctx(uid: uuid.UUID):
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
    prev_factory = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    try:
        yield
    finally:
        app_state.db_session_factory = prev_factory


def test_card_writers_are_unregistered():
    server = _build_server()
    names = set(server._tool_manager._tools)
    for name in _RETIRED_WRITERS:
        assert name not in names
    assert "deploy_dashboard" in names
    assert "bind_dashboard" in names
    assert "update_dashboard" in names


async def test_retired_writers_do_not_create_rows(wired, db_session_factory):
    """Even if a caller looks the tools up, they are gone — no Dashboard rows."""
    uid = await _make_user(db_session_factory)
    server = _build_server()
    with _user_ctx(uid):
        for name in _RETIRED_WRITERS:
            assert name not in server._tool_manager._tools

    async with db_session_factory() as db:
        assert (await db.execute(select(Dashboard))).scalars().all() == []
        assert (await db.execute(select(DashboardCard))).scalars().all() == []


def test_hosted_tools_are_in_domain_tools_map():
    assert {
        "dashboard_read",
        "get_dashboard_authoring_guide",
        "get_dashboard_query_recipe",
        "validate_dashboard_artifact",
        "list_dashboards",
        "get_dashboard",
        "list_dashboard_connections",
    } <= DOMAIN_TOOLS["dashboards"]["read"]
    assert {
        "dashboard_manage_scopes",
        "dashboard_rotate_token",
        "deploy_dashboard",
        "update_dashboard",
        "delete_dashboard",
        "bind_dashboard",
    } <= DOMAIN_TOOLS["dashboards"]["write"]
    for name in _RETIRED_WRITERS:
        assert name not in DOMAIN_TOOLS["dashboards"]["read"]
        assert name not in DOMAIN_TOOLS["dashboards"]["write"]


def test_dashboards_read_grant_allows_guide_not_writers():
    eff = EffectivePermissions(full=False, tools={"dashboards": {"read"}})
    assert eff.allows_tool("dashboard_read") is True
    assert eff.allows_tool("get_dashboard_authoring_guide") is True
    assert eff.allows_tool("bind_dashboard") is False
    assert eff.allows_tool("deploy_dashboard") is False
    for name in _RETIRED_WRITERS:
        assert eff.allows_tool(name) is False


def test_dashboards_write_grant_allows_hosted_not_cards():
    eff = EffectivePermissions(full=False, tools={"dashboards": {"write"}})
    assert eff.allows_tool("deploy_dashboard") is True
    assert eff.allows_tool("update_dashboard") is True
    assert eff.allows_tool("bind_dashboard") is True
    for name in _RETIRED_WRITERS:
        assert eff.allows_tool(name) is False


def test_no_dashboards_grant_denies_hosted_tools():
    eff = EffectivePermissions(full=False, tools={})
    assert eff.allows_tool("deploy_dashboard") is False
    assert eff.allows_tool("bind_dashboard") is False
    for name in _RETIRED_WRITERS:
        assert eff.allows_tool(name) is False
