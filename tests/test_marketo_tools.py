"""Tool-level tests for the Marketo branch of the unified marketing tools."""

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

    async def get_leads(self, instance_url, client_id, client_secret, **kw):
        self.calls.append(("get_leads", kw))
        return {"result": [{"id": 1}]}

    async def create_or_update_leads(self, instance_url, client_id, client_secret, **kw):
        self.calls.append(("create_or_update_leads", kw))
        return {"result": [{"id": 1, "status": "created"}]}


class _User:
    user_id = "u1"
    has_adobe_marketo = True


@pytest.fixture
def wired(monkeypatch):
    mcp = _StubMCP()
    marketing_tools.register_marketing_tools(mcp)

    conn = _StubConnector()
    monkeypatch.setattr(state, "adobe_marketo_connector", conn, raising=False)
    monkeypatch.setattr(marketing_tools, "_get_user", lambda: _User())

    async def fake_conn(user_id):
        return ("conn-1", "https://x.mktorest.com", "cid", "csecret")

    monkeypatch.setattr(marketing_tools, "_get_marketo_conn", fake_conn)
    return mcp, conn


@pytest.mark.asyncio
async def test_marketing_read_marketo_get_leads(wired):
    mcp, conn = wired
    result = await mcp.tools["marketing_read"](
        platform="marketo",
        action="get_leads",
        filters={"filter_type": "email", "filter_values": ["a@b.com"], "fields": ["id"]},
        limit=5,
    )
    assert result["result"] == [{"id": 1}]
    assert conn.calls[0][0] == "get_leads"
    assert conn.calls[0][1]["filter_type"] == "email"


@pytest.mark.asyncio
async def test_marketing_write_marketo_upsert(wired):
    mcp, conn = wired
    result = await mcp.tools["marketing_write"](
        platform="marketo",
        action="create_or_update_leads",
        account_id="ignored",
        payload={"leads": [{"email": "a@b.com"}], "lookup_field": "email"},
    )
    assert result["result"][0]["status"] == "created"
    assert conn.calls[0][0] == "create_or_update_leads"
