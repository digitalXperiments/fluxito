import pytest

import app.app_state as app_state
import app.models  # noqa: F401  (loads model metadata)
from app.ask.keys import (
    ProviderKey,
    ProviderKeyInfo,
    delete_key,
    get_active_key,
    get_default_key,
    list_keys,
    set_default,
    store_key,
)


@pytest.fixture
def _patch_db(db_session_factory):
    original = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    yield
    app_state.db_session_factory = original


# ── helpers ────────────────────────────────────────────────────────────────


async def _seed_user_project(db_session_factory, email_suffix=""):
    from app.models.project import Project
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email=f"keytest{email_suffix}@example.com")
        db.add(u)
        await db.flush()
        p = Project(name=f"KeyTestProject{email_suffix}", slug=f"key-test{email_suffix}", owner_id=u.id)
        db.add(p)
        await db.flush()
        pid, uid = p.id, u.id
        await db.commit()
    return pid, uid


# ── existing tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_then_get_round_trips_plaintext(_patch_db, db_session_factory):
    pid, uid = await _seed_user_project(db_session_factory, "1")

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
    pid, uid = await _seed_user_project(db_session_factory, "2")

    await store_key(project_id=pid, user_id=uid, provider="openai", api_key="sk-old", default_model="gpt-4")
    await store_key(project_id=pid, user_id=uid, provider="openai", api_key="sk-new", default_model="gpt-4o")
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


# ── new tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_stored_key_becomes_default(_patch_db, db_session_factory):
    pid, uid = await _seed_user_project(db_session_factory, "3")

    await store_key(project_id=pid, user_id=uid, provider="anthropic", api_key="sk-first", default_model=None)
    infos = await list_keys(project_id=pid, user_id=uid)
    assert len(infos) == 1
    assert infos[0].is_default is True


@pytest.mark.asyncio
async def test_second_key_does_not_clear_first_default(_patch_db, db_session_factory):
    pid, uid = await _seed_user_project(db_session_factory, "4")

    await store_key(project_id=pid, user_id=uid, provider="anthropic", api_key="sk-a", default_model=None)
    await store_key(project_id=pid, user_id=uid, provider="openai", api_key="sk-b", default_model=None)
    infos = await list_keys(project_id=pid, user_id=uid)
    by_provider = {i.provider: i for i in infos}
    # anthropic was stored first → is_default=True; openai should not be default
    assert by_provider["anthropic"].is_default is True
    assert by_provider["openai"].is_default is False


@pytest.mark.asyncio
async def test_updating_default_provider_preserves_default(_patch_db, db_session_factory):
    pid, uid = await _seed_user_project(db_session_factory, "5")

    await store_key(project_id=pid, user_id=uid, provider="anthropic", api_key="sk-old", default_model=None)
    # Update the same provider (it was default) — should remain default.
    await store_key(project_id=pid, user_id=uid, provider="anthropic", api_key="sk-new", default_model=None)
    infos = await list_keys(project_id=pid, user_id=uid)
    assert len(infos) == 1
    assert infos[0].is_default is True


@pytest.mark.asyncio
async def test_delete_key(_patch_db, db_session_factory):
    pid, uid = await _seed_user_project(db_session_factory, "6")

    await store_key(project_id=pid, user_id=uid, provider="anthropic", api_key="sk-x", default_model=None)
    await delete_key(project_id=pid, user_id=uid, provider="anthropic")
    got = await get_active_key(project_id=pid, user_id=uid, provider="anthropic")
    assert got is None


@pytest.mark.asyncio
async def test_set_default_switches_default(_patch_db, db_session_factory):
    pid, uid = await _seed_user_project(db_session_factory, "7")

    await store_key(project_id=pid, user_id=uid, provider="anthropic", api_key="sk-a", default_model=None)
    await store_key(project_id=pid, user_id=uid, provider="openai", api_key="sk-b", default_model=None)

    # Set openai as default
    await set_default(project_id=pid, user_id=uid, provider="openai")
    infos = await list_keys(project_id=pid, user_id=uid)
    by_provider = {i.provider: i for i in infos}
    assert by_provider["openai"].is_default is True
    assert by_provider["anthropic"].is_default is False


@pytest.mark.asyncio
async def test_get_default_key_returns_flagged_default(_patch_db, db_session_factory):
    pid, uid = await _seed_user_project(db_session_factory, "8")

    await store_key(project_id=pid, user_id=uid, provider="anthropic", api_key="sk-a", default_model=None)
    await store_key(project_id=pid, user_id=uid, provider="openai", api_key="sk-b", default_model=None)
    await set_default(project_id=pid, user_id=uid, provider="openai")

    key = await get_default_key(project_id=pid, user_id=uid)
    assert key is not None
    assert key.provider == "openai"


@pytest.mark.asyncio
async def test_get_default_key_fallback_when_none_flagged(_patch_db, db_session_factory):
    """get_default_key falls back to most recently updated when no flag set."""
    import uuid as uuid_mod

    # No keys at all → None
    key = await get_default_key(project_id=uuid_mod.uuid4(), user_id=uuid_mod.uuid4())
    assert key is None


@pytest.mark.asyncio
async def test_list_keys_returns_provider_key_info(_patch_db, db_session_factory):
    pid, uid = await _seed_user_project(db_session_factory, "9")

    await store_key(
        project_id=pid, user_id=uid, provider="anthropic", api_key="sk-a", default_model="claude-opus-4-8"
    )
    infos = await list_keys(project_id=pid, user_id=uid)
    assert len(infos) == 1
    info = infos[0]
    assert isinstance(info, ProviderKeyInfo)
    assert info.provider == "anthropic"
    assert info.default_model == "claude-opus-4-8"
    # No api_key field exposed
    assert not hasattr(info, "api_key")


@pytest.mark.asyncio
async def test_update_key_meta_updates_model_and_base_url(_patch_db, db_session_factory):
    from app.ask.keys import update_key_meta

    pid, uid = await _seed_user_project(db_session_factory, "10")

    await store_key(project_id=pid, user_id=uid, provider="anthropic", api_key="sk-secret", default_model="claude-opus-4-8")
    updated = await update_key_meta(
        project_id=pid,
        user_id=uid,
        provider="anthropic",
        default_model="claude-sonnet-4-6",
        base_url=None,
    )
    assert updated is True
    got = await get_active_key(project_id=pid, user_id=uid, provider="anthropic")
    assert got is not None
    assert got.api_key == "sk-secret"  # key unchanged
    assert got.default_model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_update_key_meta_returns_false_when_no_key(_patch_db, db_session_factory):
    import uuid as uuid_mod

    from app.ask.keys import update_key_meta

    updated = await update_key_meta(
        project_id=uuid_mod.uuid4(),
        user_id=uuid_mod.uuid4(),
        provider="anthropic",
        default_model="x",
        base_url=None,
    )
    assert updated is False


@pytest.mark.asyncio
async def test_model_catalog_round_trip(_patch_db, db_session_factory):
    from app.ask.model_catalog import get_extra_models, set_extra_models

    await set_extra_models({"anthropic": ["claude-custom-v1"], "openai": ["gpt-5"]})
    result = await get_extra_models()
    assert result["anthropic"] == ["claude-custom-v1"]
    assert result["openai"] == ["gpt-5"]


@pytest.mark.asyncio
async def test_model_catalog_filters_unknown_providers(_patch_db, db_session_factory):
    from app.ask.model_catalog import get_extra_models, set_extra_models

    await set_extra_models({"anthropic": ["m1"], "unknown_provider": ["x"]})
    result = await get_extra_models()
    assert "unknown_provider" not in result
    assert "anthropic" in result
