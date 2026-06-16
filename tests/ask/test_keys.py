import pytest

import app.app_state as app_state
import app.models  # noqa: F401  (loads model metadata)
from app.ask.keys import ProviderKey, get_active_key, store_key


@pytest.fixture
def _patch_db(db_session_factory):
    original = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    yield
    app_state.db_session_factory = original


@pytest.mark.asyncio
async def test_store_then_get_round_trips_plaintext(_patch_db, db_session_factory):
    from app.models.project import Project
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="keytest@example.com")
        db.add(u)
        await db.flush()
        p = Project(name="KeyTestProject", slug="key-test-proj", owner_id=u.id)
        db.add(p)
        await db.flush()
        pid = p.id
        uid = u.id
        await db.commit()

    await store_key(
        project_id=pid,
        user_id=uid,
        provider="anthropic",
        api_key="sk-secret",
        default_model="claude-opus-4-8",
    )
    got = await get_active_key(project_id=pid, user_id=uid, provider="anthropic")
    assert isinstance(got, ProviderKey)
    assert got.api_key == "sk-secret"
    assert got.default_model == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_store_replaces_existing_active_key(_patch_db, db_session_factory):
    from app.models.project import Project
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="keytest2@example.com")
        db.add(u)
        await db.flush()
        p = Project(name="KeyTestProject2", slug="key-test-proj-2", owner_id=u.id)
        db.add(p)
        await db.flush()
        pid = p.id
        uid = u.id
        await db.commit()

    await store_key(
        project_id=pid,
        user_id=uid,
        provider="openai",
        api_key="sk-old",
        default_model="gpt-4",
    )
    await store_key(
        project_id=pid,
        user_id=uid,
        provider="openai",
        api_key="sk-new",
        default_model="gpt-4o",
    )
    got = await get_active_key(project_id=pid, user_id=uid, provider="openai")
    assert got is not None
    assert got.api_key == "sk-new"
    assert got.default_model == "gpt-4o"


@pytest.mark.asyncio
async def test_get_active_key_returns_none_when_missing(_patch_db, db_session_factory):
    import uuid

    got = await get_active_key(
        project_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        provider="anthropic",
    )
    assert got is None
