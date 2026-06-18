"""Tests for ConversationService (DB-backed)."""

import pytest

import app.app_state as app_state
import app.models  # noqa: F401  (ensures all model metadata loaded)


@pytest.fixture
def _patch_db(db_session_factory):
    original = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    yield
    app_state.db_session_factory = original


@pytest.mark.asyncio
async def test_create_append_and_load_round_trip(_patch_db, db_session_factory):
    from app.ask.providers.base import LLMMessage, TextBlock, ToolResultBlock, ToolUseBlock
    from app.ask.service import ConversationService
    from app.models.project import Project
    from app.models.user import User

    # Seed real User + Project to satisfy FK constraints.
    async with db_session_factory() as db:
        u = User(email="svc-test@example.com")
        db.add(u)
        await db.flush()
        p = Project(name="SvcTest", slug="svc-test", owner_id=u.id)
        db.add(p)
        await db.flush()
        uid = u.id
        pid = p.id
        await db.commit()

    svc = ConversationService()
    conv = await svc.create(project_id=pid, user_id=uid, provider="anthropic", model="claude-opus-4-8")

    await svc.append(conv.id, LLMMessage(role="user", content=[TextBlock(text="hi")]))
    await svc.append(
        conv.id,
        LLMMessage(
            role="assistant",
            content=[ToolUseBlock(id="t1", name="analytics_read", input={"a": 1})],
        ),
    )
    await svc.append(
        conv.id,
        LLMMessage(role="tool", content=[ToolResultBlock(tool_use_id="t1", content="{}")]),
    )

    history = await svc.load_history(conv.id)
    assert [m.role for m in history] == ["user", "assistant", "tool"]
    assert history[1].content[0].name == "analytics_read"
    assert history[2].content[0].tool_use_id == "t1"


@pytest.mark.asyncio
async def test_append_assigns_increasing_seq(_patch_db, db_session_factory):
    from sqlalchemy import select

    from app.ask.providers.base import LLMMessage, TextBlock
    from app.ask.service import ConversationService
    from app.models.conversation import ChatMessage
    from app.models.project import Project
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="seq-test@example.com")
        db.add(u)
        await db.flush()
        p = Project(name="SeqTest", slug="seq-test", owner_id=u.id)
        db.add(p)
        await db.flush()
        uid = u.id
        pid = p.id
        await db.commit()

    svc = ConversationService()
    conv = await svc.create(project_id=pid, user_id=uid, provider="openai", model="gpt-4o")

    for i in range(3):
        await svc.append(conv.id, LLMMessage(role="user", content=[TextBlock(text=f"msg{i}")]))

    async with db_session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.conversation_id == conv.id)
                    .order_by(ChatMessage.seq.asc())
                )
            )
            .scalars()
            .all()
        )

    seqs = [r.seq for r in rows]
    assert seqs == [0, 1, 2]


@pytest.mark.asyncio
async def test_set_title_and_archive(_patch_db, db_session_factory):
    from app.ask.service import ConversationService
    from app.models.project import Project
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="title-test@example.com")
        db.add(u)
        await db.flush()
        p = Project(name="TitleTest", slug="title-test", owner_id=u.id)
        db.add(p)
        await db.flush()
        uid = u.id
        pid = p.id
        await db.commit()

    svc = ConversationService()
    conv = await svc.create(project_id=pid, user_id=uid, provider="anthropic", model="claude-opus-4-8")

    await svc.set_title(conv.id, "My Chat")
    fetched = await svc.get(conv.id)
    assert fetched is not None
    assert fetched.title == "My Chat"

    await svc.archive(conv.id)
    fetched2 = await svc.get(conv.id)
    assert fetched2 is not None
    assert fetched2.archived is True

    # list_for should exclude archived conversations
    listing = await svc.list_for(project_id=pid, user_id=uid)
    assert all(not c.archived for c in listing)
    assert conv.id not in [c.id for c in listing]
