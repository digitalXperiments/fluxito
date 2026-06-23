"""Tool-level tests for Branch / AppsFlyer / Adjust branches of the unified marketing tools."""

import pytest

import app.app_state as state
from app.tools import marketing_tools


class _StubMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def deco(fn):
            self.tools[name] = fn
            return fn

        return deco


class _StubConnector:
    def __init__(self):
        self.calls = []

    async def get_app(self, api_key, secret_key):
        self.calls.append(("get_app", api_key, secret_key))
        return {"app": {"app_id": "1"}}

    async def request_daily_export(self, api_key, secret_key, export_date):
        self.calls.append(("request_daily_export", api_key, secret_key, export_date))
        return {"success": True, "export_date": export_date, "files": {}}

    async def list_apps(self, api_key):
        self.calls.append(("list_apps", api_key))
        return {"apps": [], "total": 0}

    async def get_installs_report(self, api_key, app_id, start_date, end_date):
        self.calls.append(("get_installs_report", api_key, app_id, start_date, end_date))
        return {"app_id": app_id, "installs": [], "total": 0}

    async def get_in_app_events_report(self, api_key, app_id, start_date, end_date):
        self.calls.append(("get_in_app_events_report", api_key, app_id, start_date, end_date))
        return {"app_id": app_id, "events": [], "total": 0}

    async def get_partners_report(self, api_key, app_id, start_date, end_date):
        self.calls.append(("get_partners_report", api_key, app_id, start_date, end_date))
        return {"app_id": app_id, "partners": [], "total": 0}

    async def get_report(self, api_key, dimensions, metrics, date_period, **filters):
        self.calls.append(("get_report", api_key, dimensions, metrics, date_period, filters))
        return {"rows": [], "totals": {}}

    async def get_pivot_report(self, api_key, dimensions, metrics, date_period, index, **filters):
        self.calls.append(("get_pivot_report", api_key, dimensions, metrics, date_period, index, filters))
        return {"rows": [], "totals": {}}

    async def list_events(self, api_key):
        self.calls.append(("list_events", api_key))
        return {"events": [], "total": 0}

    async def list_app_automation_apps(self, api_key):
        self.calls.append(("list_app_automation_apps", api_key))
        return {"apps": [], "total": 0}

    async def get_partner_links(self, api_key, app_token):
        self.calls.append(("get_partner_links", api_key, app_token))
        return {"trackers": [], "total": 0}


class _BranchUser:
    user_id = "u1"
    has_branch = True
    has_appsflyer = False
    has_adjust = False


class _AppsFlyerUser:
    user_id = "u1"
    has_branch = False
    has_appsflyer = True
    has_adjust = False


class _AdjustUser:
    user_id = "u1"
    has_branch = False
    has_appsflyer = False
    has_adjust = True


@pytest.fixture
def branch_wired(monkeypatch):
    mcp = _StubMCP()
    marketing_tools.register_marketing_tools(mcp)

    conn = _StubConnector()
    monkeypatch.setattr(state, "branch_connector", conn, raising=False)
    monkeypatch.setattr(marketing_tools, "_get_user", lambda: _BranchUser())

    async def fake_conn(user_id):
        return ("conn-1", "branch-key", "branch-secret")

    monkeypatch.setattr(marketing_tools, "_get_branch_conn", fake_conn)
    return mcp, conn


@pytest.fixture
def appsflyer_wired(monkeypatch):
    mcp = _StubMCP()
    marketing_tools.register_marketing_tools(mcp)

    conn = _StubConnector()
    monkeypatch.setattr(state, "appsflyer_connector", conn, raising=False)
    monkeypatch.setattr(marketing_tools, "_get_user", lambda: _AppsFlyerUser())

    async def fake_conn(user_id):
        return ("conn-1", "af-key", "af-secret")

    monkeypatch.setattr(marketing_tools, "_get_appsflyer_conn", fake_conn)
    return mcp, conn


@pytest.fixture
def adjust_wired(monkeypatch):
    mcp = _StubMCP()
    marketing_tools.register_marketing_tools(mcp)

    conn = _StubConnector()
    monkeypatch.setattr(state, "adjust_connector", conn, raising=False)
    monkeypatch.setattr(marketing_tools, "_get_user", lambda: _AdjustUser())

    async def fake_conn(user_id):
        return ("conn-1", "adj-key", "adj-secret")

    monkeypatch.setattr(marketing_tools, "_get_adjust_conn", fake_conn)
    return mcp, conn


# ---------------------------------------------------------------------------
# Branch marketing_read: get_app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branch_read_get_app(branch_wired):
    mcp, conn = branch_wired
    result = await mcp.tools["marketing_read"](platform="branch", action="get_app")
    assert conn.calls[0][0] == "get_app"
    assert "app" in result


@pytest.mark.asyncio
async def test_branch_read_unknown_action(branch_wired):
    mcp, conn = branch_wired
    result = await mcp.tools["marketing_read"](platform="branch", action="list_accounts")
    assert result.get("error") is True


# ---------------------------------------------------------------------------
# Branch marketing_write: request_daily_export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branch_write_request_daily_export(branch_wired):
    mcp, conn = branch_wired
    result = await mcp.tools["marketing_write"](
        platform="branch", action="request_daily_export", export_date="2025-01-15"
    )
    assert conn.calls[0][0] == "request_daily_export"
    assert result["success"] is True
    assert result["export_date"] == "2025-01-15"


@pytest.mark.asyncio
async def test_branch_write_missing_export_date(branch_wired):
    mcp, conn = branch_wired
    result = await mcp.tools["marketing_write"](platform="branch", action="request_daily_export")
    assert result.get("error") is True
    assert "export_date" in result.get("message", "")


# ---------------------------------------------------------------------------
# AppsFlyer marketing_read: list_apps, reports
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_appsflyer_read_list_apps(appsflyer_wired):
    mcp, conn = appsflyer_wired
    result = await mcp.tools["marketing_read"](platform="appsflyer", action="list_apps")
    assert conn.calls[0][0] == "list_apps"


@pytest.mark.asyncio
async def test_appsflyer_read_installs_report(appsflyer_wired):
    mcp, conn = appsflyer_wired
    result = await mcp.tools["marketing_read"](
        platform="appsflyer",
        action="get_installs_report",
        app_id="com.example.app",
        date_range_start="2025-01-01",
        date_range_end="2025-01-31",
    )
    assert conn.calls[0][0] == "get_installs_report"
    assert conn.calls[0][2] == "com.example.app"
    assert conn.calls[0][3] == "2025-01-01"
    assert conn.calls[0][4] == "2025-01-31"
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_appsflyer_read_installs_report_missing_dates(appsflyer_wired):
    mcp, conn = appsflyer_wired
    result = await mcp.tools["marketing_read"](
        platform="appsflyer",
        action="get_installs_report",
        app_id="com.example.app",
    )
    assert result.get("error") is True
    assert "date_range_start" in result.get("message", "")


@pytest.mark.asyncio
async def test_appsflyer_read_in_app_events_report(appsflyer_wired):
    mcp, conn = appsflyer_wired
    result = await mcp.tools["marketing_read"](
        platform="appsflyer",
        action="get_in_app_events_report",
        app_id="com.example.app",
        date_range_start="2025-01-01",
        date_range_end="2025-01-31",
    )
    assert conn.calls[0][0] == "get_in_app_events_report"


@pytest.mark.asyncio
async def test_appsflyer_read_partners_report(appsflyer_wired):
    mcp, conn = appsflyer_wired
    result = await mcp.tools["marketing_read"](
        platform="appsflyer",
        action="get_partners_report",
        app_id="com.example.app",
        date_range_start="2025-01-01",
        date_range_end="2025-01-31",
    )
    assert conn.calls[0][0] == "get_partners_report"


@pytest.mark.asyncio
async def test_appsflyer_read_unknown_action(appsflyer_wired):
    mcp, conn = appsflyer_wired
    result = await mcp.tools["marketing_read"](platform="appsflyer", action="get_report")
    assert result.get("error") is True


@pytest.mark.asyncio
async def test_appsflyer_read_missing_app_id(appsflyer_wired):
    mcp, conn = appsflyer_wired
    result = await mcp.tools["marketing_read"](
        platform="appsflyer",
        action="get_installs_report",
        date_range_start="2025-01-01",
        date_range_end="2025-01-31",
    )
    assert result.get("error") is True
    assert "app_id" in result.get("message", "")


# ---------------------------------------------------------------------------
# Adjust marketing_read: list_apps, get_report, get_pivot_report, etc.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adjust_read_list_apps(adjust_wired):
    mcp, conn = adjust_wired
    result = await mcp.tools["marketing_read"](platform="adjust", action="list_apps")
    assert conn.calls[0][0] == "list_apps"


@pytest.mark.asyncio
async def test_adjust_read_get_report(adjust_wired):
    mcp, conn = adjust_wired
    result = await mcp.tools["marketing_read"](
        platform="adjust",
        action="get_report",
        date_range_start="2025-01-01",
        date_range_end="2025-01-31",
        filters={"app_token": "abc123"},
    )
    assert conn.calls[0][0] == "get_report"
    # dimensions/metrics/date_period should be extracted from filters
    _call = conn.calls[0]
    assert _call[2] == "app,tracker"  # default dimensions
    assert _call[3] == "installs,clicks"  # default metrics
    assert "abc123" in str(_call[5])  # extra filter


@pytest.mark.asyncio
async def test_adjust_read_get_report_missing_dates(adjust_wired):
    mcp, conn = adjust_wired
    result = await mcp.tools["marketing_read"](
        platform="adjust",
        action="get_report",
    )
    assert result.get("error") is True
    assert "date_range_start" in result.get("message", "")


@pytest.mark.asyncio
async def test_adjust_read_get_pivot_report(adjust_wired):
    mcp, conn = adjust_wired
    result = await mcp.tools["marketing_read"](
        platform="adjust",
        action="get_pivot_report",
        date_range_start="2025-01-01",
        date_range_end="2025-01-31",
        filters={"index": "campaign"},
    )
    assert conn.calls[0][0] == "get_pivot_report"


@pytest.mark.asyncio
async def test_adjust_read_list_events(adjust_wired):
    mcp, conn = adjust_wired
    result = await mcp.tools["marketing_read"](platform="adjust", action="list_events")
    assert conn.calls[0][0] == "list_events"


@pytest.mark.asyncio
async def test_adjust_read_list_app_automation_apps(adjust_wired):
    mcp, conn = adjust_wired
    result = await mcp.tools["marketing_read"](platform="adjust", action="list_app_automation_apps")
    assert conn.calls[0][0] == "list_app_automation_apps"


@pytest.mark.asyncio
async def test_adjust_read_get_partner_links(adjust_wired):
    mcp, conn = adjust_wired
    result = await mcp.tools["marketing_read"](
        platform="adjust",
        action="get_partner_links",
        resource_id="app-token-123",
    )
    assert conn.calls[0][0] == "get_partner_links"
    assert conn.calls[0][2] == "app-token-123"


@pytest.mark.asyncio
async def test_adjust_read_get_partner_links_missing_resource_id(adjust_wired):
    mcp, conn = adjust_wired
    result = await mcp.tools["marketing_read"](
        platform="adjust",
        action="get_partner_links",
    )
    assert result.get("error") is True
    assert "resource_id" in result.get("message", "")


@pytest.mark.asyncio
async def test_adjust_read_unknown_action(adjust_wired):
    mcp, conn = adjust_wired
    result = await mcp.tools["marketing_read"](platform="adjust", action="get_campaign_performance")
    assert result.get("error") is True
