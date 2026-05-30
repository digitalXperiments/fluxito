# tests/test_rate_limits.py
"""Tests for MCP rate limiting: exemption cache, limiter decision, admin config."""

import uuid

import pytest

import app.app_state as app_state
import app.models.access_request  # noqa: F401 — register tables in metadata
import app.models.sdr  # noqa: F401


@pytest.fixture
def _patch_db(db_session_factory):
    original = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    yield
    app_state.db_session_factory = original


async def _make_user(db_session_factory, email, *, is_superadmin=False):
    from app.models.user import User
    async with db_session_factory() as db:
        u = User(email=email, is_superadmin=is_superadmin)
        db.add(u)
        await db.flush()
        uid = str(u.id)
        await db.commit()
        return uid


@pytest.mark.asyncio
async def test_superadmin_cache_true_for_superadmin(_patch_db, db_session_factory):
    from app.auth.superadmin_cache import is_superadmin_cached, _clear_superadmin_cache

    _clear_superadmin_cache()
    sid = await _make_user(db_session_factory, "s@example.com", is_superadmin=True)
    assert await is_superadmin_cached(sid) is True


@pytest.mark.asyncio
async def test_superadmin_cache_false_for_normal_user(_patch_db, db_session_factory):
    from app.auth.superadmin_cache import is_superadmin_cached, _clear_superadmin_cache

    _clear_superadmin_cache()
    uid = await _make_user(db_session_factory, "n@example.com", is_superadmin=False)
    assert await is_superadmin_cached(uid) is False


@pytest.mark.asyncio
async def test_superadmin_cache_caches(_patch_db, db_session_factory):
    """A cached value persists within TTL even if the DB row changes."""
    from sqlalchemy import update

    from app.auth.superadmin_cache import is_superadmin_cached, _clear_superadmin_cache
    from app.models.user import User

    _clear_superadmin_cache()
    sid = await _make_user(db_session_factory, "c@example.com", is_superadmin=True)
    assert await is_superadmin_cached(sid) is True  # caches True

    async with db_session_factory() as db:
        await db.execute(update(User).where(User.id == uuid.UUID(sid)).values(is_superadmin=False))
        await db.commit()

    assert await is_superadmin_cached(sid) is True  # still True from cache (within TTL)


@pytest.mark.asyncio
async def test_superadmin_cache_unknown_user_false(_patch_db, db_session_factory):
    from app.auth.superadmin_cache import is_superadmin_cached, _clear_superadmin_cache

    _clear_superadmin_cache()
    assert await is_superadmin_cached(str(uuid.uuid4())) is False


@pytest.mark.asyncio
async def test_check_rate_limit_blocks_over_limit(_patch_db, db_session_factory, monkeypatch):
    """check_rate_limit returns a rate_limited error dict once per-min is exceeded."""
    import app.auth.rate_limiter as rl

    class _FakeRedis:
        def __init__(self):
            self.counts = {}

        async def incr(self, key):
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]

        async def expire(self, key, ttl):
            return True

        async def get(self, key):
            return None

    fake = _FakeRedis()
    monkeypatch.setattr(app_state, "redis_client", fake, raising=False)
    monkeypatch.setattr(rl, "_cached_limits", {"default": {"per_min": 2, "per_hour": 1000}}, raising=False)
    monkeypatch.setattr(rl, "_cached_limits_ts", 9e18, raising=False)  # far future → cache fresh

    uid = "u-test"
    assert await rl.check_rate_limit(uid) is None  # 1st
    assert await rl.check_rate_limit(uid) is None  # 2nd
    blocked = await rl.check_rate_limit(uid)        # 3rd → over
    assert blocked is not None
    assert blocked["error_type"] == "rate_limited"
    assert "retry_after_seconds" in blocked
