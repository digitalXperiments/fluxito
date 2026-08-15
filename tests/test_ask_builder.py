"""Tests for Ask Fluxito dashboard builder leftovers:

- CardPreviewBlock / ChoicesBlock JSON round-trip (legacy blocks still load)
- propose_card is intercepted and hard-errors (hosted-only; no card preview)
- ask_choices virtual-tool event
- POST /api/ask/confirm-action: add is rejected (hosted-only), discard still works
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

import app.app_state as app_state

# ---------------------------------------------------------------------------
# 1. Block round-trip
# ---------------------------------------------------------------------------


def test_card_preview_block_round_trip():
    from app.ask.providers.base import CardPreviewBlock, blocks_from_json, blocks_to_json

    block = CardPreviewBlock(
        id="cp_1",
        card={"title": "Revenue by channel", "chart_type": "bar"},
        snap={"rows": [1, 2, 3]},
        warnings=["unknown field 'foo'"],
        dashboard_slug="abc123",
        state="proposed",
    )
    raw = blocks_to_json([block])
    assert raw == [
        {
            "type": "card_preview",
            "id": "cp_1",
            "card": {"title": "Revenue by channel", "chart_type": "bar"},
            "snap": {"rows": [1, 2, 3]},
            "warnings": ["unknown field 'foo'"],
            "dashboard_slug": "abc123",
            "state": "proposed",
        }
    ]
    back = blocks_from_json(raw)
    assert len(back) == 1
    assert back[0] == block


def test_choices_block_round_trip():
    from app.ask.providers.base import ChoicesBlock, blocks_from_json, blocks_to_json

    block = ChoicesBlock(
        id="ch_1",
        question="Which chart type?",
        options=[{"label": "Bar", "value": "bar"}, {"label": "Donut", "value": "donut"}],
        multi=False,
    )
    raw = blocks_to_json([block])
    back = blocks_from_json(raw)
    assert back == [block]


def test_blocks_from_json_tolerates_unknown_type_in_old_data():
    """Old data may contain block shapes we don't recognize yet — must not blow up loading
    the rest of the conversation. Per the pinned contract this is 'tolerated as today'."""
    from app.ask.providers.base import blocks_from_json

    raw = [{"type": "text", "text": "hi"}]
    # Known types still round-trip fine.
    out = blocks_from_json(raw)
    assert out[0].text == "hi"

    # An entirely unknown type raises (matches current documented behavior for text/tool_use/
    # tool_result) rather than silently corrupting history.
    with pytest.raises(ValueError):
        blocks_from_json([{"type": "some_future_block", "id": "x"}])


def test_block_to_provider_text_serialization():
    """CardPreviewBlock/ChoicesBlock must never reach a provider adapter as-is."""
    from app.ask.providers.base import (
        CardPreviewBlock,
        ChoicesBlock,
        LLMMessage,
        TextBlock,
        messages_for_provider,
    )

    card_msg = LLMMessage(
        role="assistant",
        content=[
            CardPreviewBlock(
                id="cp_1",
                card={"title": "Revenue by channel", "chart_type": "bar"},
                snap={},
                warnings=[],
                dashboard_slug=None,
            )
        ],
    )
    choices_msg = LLMMessage(
        role="assistant",
        content=[
            ChoicesBlock(id="ch_1", question="Which chart type?", options=[{"label": "Bar", "value": "bar"}])
        ],
    )
    plain_msg = LLMMessage(role="user", content=[TextBlock(text="hi")])

    out = messages_for_provider([card_msg, choices_msg, plain_msg])
    assert isinstance(out[0].content[0], TextBlock)
    assert out[0].content[0].text == "[proposed card: Revenue by channel, bar]"
    assert isinstance(out[1].content[0], TextBlock)
    assert out[1].content[0].text == "[asked user to choose: Which chart type?]"
    # Untouched messages are returned unchanged (same object).
    assert out[2] is plain_msg


# ---------------------------------------------------------------------------
# 2. Virtual tools: propose_card / ask_choices interception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_card_is_hosted_only_error():
    with patch("app.ask.tools.run_mcp_tool", new=AsyncMock()) as mocked:
        from app.ask.tools import dispatch_virtual_tool

        result = await dispatch_virtual_tool(
            user_id="u1",
            project_id="p1",
            name="propose_card",
            params={
                "title": "Revenue by channel",
                "platform": "ga4",
                "tool": "analytics_read",
                "action": "report",
                "params": {"metrics": ["revenue"]},
                "chart_type": "bar",
            },
        )

    mocked.assert_not_awaited()
    assert result.is_error is True
    assert result.block is None
    assert result.event is None
    assert "hosted_only" in result.content or "retired" in result.content.lower()
    assert "deploy_dashboard" in result.content


@pytest.mark.asyncio
async def test_propose_card_never_calls_card_preview():
    from app.ask.tools import dispatch_virtual_tool

    result = await dispatch_virtual_tool(
        user_id="u1", project_id="p1", name="propose_card", params={"title": "X"}
    )
    assert result.is_error is True
    assert result.block is None
    assert result.event is None
    assert "dashboard_card_preview" not in result.content


@pytest.mark.asyncio
async def test_ask_choices_emits_choices_block():
    from app.ask.providers.base import ChoicesBlock
    from app.ask.tools import dispatch_virtual_tool

    result = await dispatch_virtual_tool(
        user_id="u1",
        project_id="p1",
        name="ask_choices",
        params={
            "question": "Which chart type?",
            "options": [{"label": "Bar", "value": "bar"}, {"label": "Donut"}],
        },
    )
    assert result.is_error is False
    assert isinstance(result.block, ChoicesBlock)
    assert result.block.options == [{"label": "Bar", "value": "bar"}, {"label": "Donut", "value": "Donut"}]
    assert result.event.type == "choices"
    assert "wait for their reply" in result.content


@pytest.mark.asyncio
async def test_harness_intercepts_virtual_tool_before_bridge_dispatch():
    """The harness must route propose_card to dispatch_virtual_tool, never to bridge.dispatch,
    and must persist the resulting CardPreviewBlock as its own assistant message."""
    from app.ask.harness import Harness, HarnessDeps
    from app.ask.providers.base import (
        CardPreviewBlock,
        LLMMessage,
        StopReason,
        StreamEvent,
        TextBlock,
    )

    class FakeProvider:
        name = "fake"

        def __init__(self, scripts):
            self._scripts = list(scripts)

        async def stream(self, **_):
            for ev in self._scripts.pop(0):
                yield ev

    class FakeBridge:
        user_id = "u1"
        project_id = "p1"

        def __init__(self):
            self.calls = []

        def tool_specs(self):
            return []

        async def dispatch(self, name, params):
            self.calls.append((name, params))
            return ('{"rows": 1}', False)

    class RecordingService:
        def __init__(self):
            self.appended = []

        async def append(self, conv_id, message, token_usage=None):
            self.appended.append(message)

    provider = FakeProvider(
        [
            [
                StreamEvent(type="tool_call_start", tool_id="t1", tool_name="propose_card"),
                StreamEvent(
                    type="tool_args_delta",
                    args_fragment=(
                        '{"title":"Revenue","platform":"ga4","tool":"analytics_read",'
                        '"action":"report","params":{},"chart_type":"bar"}'
                    ),
                ),
                StreamEvent(type="message_done", stop_reason=StopReason.TOOL_USE),
            ],
            [
                StreamEvent(type="text_delta", text="Take a look"),
                StreamEvent(type="message_done", stop_reason=StopReason.END),
            ],
        ]
    )
    bridge = FakeBridge()
    svc = RecordingService()
    deps = HarnessDeps(
        provider=provider, bridge=bridge, service=svc, conversation_id="c1", model="m", system="SYS"
    )
    h = Harness(deps, max_iterations=5)

    fake_vres_source = AsyncMock(
        return_value=type(
            "R",
            (),
            {
                "content": "Card preview shown to the user with an Add-to-dashboard button.",
                "is_error": False,
                "block": CardPreviewBlock(
                    id="cp_1",
                    card={"title": "Revenue", "chart_type": "bar"},
                    snap={},
                    warnings=[],
                    dashboard_slug=None,
                ),
                "event": StreamEvent(type="card_preview", block={"type": "card_preview", "id": "cp_1"}),
            },
        )()
    )
    with patch("app.ask.harness.dispatch_virtual_tool", new=fake_vres_source):
        out = [e async for e in h.run(LLMMessage(role="user", content=[TextBlock(text="hi")]))]

    # Never reached the real bridge.
    assert bridge.calls == []
    fake_vres_source.assert_awaited_once()
    assert fake_vres_source.await_args.kwargs["name"] == "propose_card"
    assert fake_vres_source.await_args.kwargs["user_id"] == "u1"
    assert fake_vres_source.await_args.kwargs["project_id"] == "p1"

    # The card_preview StreamEvent was yielded to the client.
    assert any(e.type == "card_preview" for e in out)

    # Persisted: user, assistant(tool_use), tool(result), assistant(display block), assistant(final text)
    roles = [m.role for m in svc.appended]
    assert roles == ["user", "assistant", "tool", "assistant", "assistant"]
    display_msg = svc.appended[3]
    assert isinstance(display_msg.content[0], CardPreviewBlock)


# ---------------------------------------------------------------------------
# 3. POST /api/ask/confirm-action
# ---------------------------------------------------------------------------


@pytest.fixture
def _patch_db(db_session_factory):
    original = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    yield
    app_state.db_session_factory = original


@pytest.fixture
async def _http_client(_patch_db):
    import httpx
    from httpx import ASGITransport

    from app.main import app

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_confirm_action_discard(_http_client, db_session_factory):
    from app.ask.providers.base import CardPreviewBlock, LLMMessage
    from app.ask.service import ConversationService
    from app.auth.uid_cookie import sign_uid
    from app.models.project import Project
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="discard@example.com")
        db.add(u)
        await db.flush()
        p = Project(name="P", slug=f"p-{uuid.uuid4().hex[:8]}", owner_id=u.id)
        db.add(p)
        await db.flush()
        uid, pid = u.id, p.id
        await db.commit()

    svc = ConversationService()
    conv = await svc.create(project_id=pid, user_id=uid, provider="anthropic", model="m")
    block = CardPreviewBlock(
        id="cp_disc", card={"title": "X", "chart_type": "bar"}, snap={}, warnings=[], dashboard_slug=None
    )
    await svc.append(conv.id, LLMMessage(role="assistant", content=[block]))

    _http_client.cookies.set("uid", sign_uid(str(uid)))
    resp = await _http_client.post(
        "/api/ask/confirm-action",
        json={"conversation_id": str(conv.id), "block_id": "cp_disc", "action": "discard"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "discarded"

    stored = await svc.find_block(conv.id, "cp_disc")
    assert stored["state"] == "discarded"


@pytest.mark.asyncio
async def test_confirm_action_add_happy_path(_http_client, db_session_factory):
    from app.ask.providers.base import CardPreviewBlock, LLMMessage
    from app.ask.service import ConversationService
    from app.auth.uid_cookie import sign_uid
    from app.models.project import Project, ProjectMember
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="add@example.com")
        db.add(u)
        await db.flush()
        p = Project(name="P2", slug=f"p2-{uuid.uuid4().hex[:8]}", owner_id=u.id)
        db.add(p)
        await db.flush()
        db.add(ProjectMember(project_id=p.id, user_id=u.id, role="owner", is_active=True))
        uid, pid = u.id, p.id
        await db.commit()

    svc = ConversationService()
    conv = await svc.create(project_id=pid, user_id=uid, provider="anthropic", model="m")
    block = CardPreviewBlock(
        id="cp_add",
        card={
            "title": "Revenue by channel",
            "platform": "ga4",
            "tool": "analytics_read",
            "action": "report",
            "params": {"metrics": ["revenue"]},
            "chart_type": "bar",
            "chart_config": None,
        },
        snap={},
        warnings=[],
        dashboard_slug="dash-slug-1",
    )
    await svc.append(conv.id, LLMMessage(role="assistant", content=[block]))

    _http_client.cookies.set("uid", sign_uid(str(uid)))
    _http_client.cookies.set("active_project_id", str(pid))

    resp = await _http_client.post(
        "/api/ask/confirm-action",
        json={"conversation_id": str(conv.id), "block_id": "cp_add", "action": "add"},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body.get("error_type") == "hosted_only"
    assert "deploy_dashboard" in body["error"]

    stored = await svc.find_block(conv.id, "cp_add")
    assert stored["state"] != "added"


@pytest.mark.asyncio
async def test_confirm_action_add_without_dashboard_400(_http_client, db_session_factory):
    from app.ask.providers.base import CardPreviewBlock, LLMMessage
    from app.ask.service import ConversationService
    from app.auth.uid_cookie import sign_uid
    from app.models.project import Project
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="nodash@example.com")
        db.add(u)
        await db.flush()
        p = Project(name="P3", slug=f"p3-{uuid.uuid4().hex[:8]}", owner_id=u.id)
        db.add(p)
        await db.flush()
        uid, pid = u.id, p.id
        await db.commit()

    svc = ConversationService()
    conv = await svc.create(project_id=pid, user_id=uid, provider="anthropic", model="m")
    block = CardPreviewBlock(
        id="cp_nodash", card={"title": "X", "chart_type": "bar"}, snap={}, warnings=[], dashboard_slug=None
    )
    await svc.append(conv.id, LLMMessage(role="assistant", content=[block]))

    _http_client.cookies.set("uid", sign_uid(str(uid)))
    resp = await _http_client.post(
        "/api/ask/confirm-action",
        json={"conversation_id": str(conv.id), "block_id": "cp_nodash", "action": "add"},
    )
    assert resp.status_code == 400
    assert resp.json().get("error_type") == "hosted_only" or "retired" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_confirm_action_foreign_conversation_404(_http_client, db_session_factory):
    from app.ask.service import ConversationService
    from app.auth.uid_cookie import sign_uid
    from app.models.project import Project
    from app.models.user import User

    async with db_session_factory() as db:
        owner = User(email="owner@example.com")
        stranger = User(email="stranger@example.com")
        db.add_all([owner, stranger])
        await db.flush()
        p = Project(name="P4", slug=f"p4-{uuid.uuid4().hex[:8]}", owner_id=owner.id)
        db.add(p)
        await db.flush()
        owner_id, stranger_id, pid = owner.id, stranger.id, p.id
        await db.commit()

    svc = ConversationService()
    conv = await svc.create(project_id=pid, user_id=owner_id, provider="anthropic", model="m")

    _http_client.cookies.set("uid", sign_uid(str(stranger_id)))
    resp = await _http_client.post(
        "/api/ask/confirm-action",
        json={"conversation_id": str(conv.id), "block_id": "cp_x", "action": "discard"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_confirm_action_unauthenticated_401(_http_client):
    resp = await _http_client.post(
        "/api/ask/confirm-action",
        json={"conversation_id": str(uuid.uuid4()), "block_id": "x", "action": "discard"},
    )
    assert resp.status_code == 401
