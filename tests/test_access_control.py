# tests/test_access_control.py
"""Backend tests for the access-control core (super-admin + admin panel + request-access)."""

import uuid

import pytest

import app.app_state as app_state
import app.models.access_request  # noqa: F401 — register table in metadata
import app.models.sdr  # noqa: F401


@pytest.fixture
def _patch_db(db_session_factory):
    original = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    yield
    app_state.db_session_factory = original


@pytest.mark.asyncio
async def test_user_has_is_superadmin_default_false(_patch_db, db_session_factory):
    from sqlalchemy import select

    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="a@example.com")
        db.add(u)
        await db.flush()
        uid = u.id
        await db.commit()
    async with db_session_factory() as db:
        u = (await db.execute(select(User).where(User.id == uid))).scalar_one()
        assert u.is_superadmin is False


@pytest.mark.asyncio
async def test_access_request_model_persists(_patch_db, db_session_factory):
    from sqlalchemy import select

    from app.models.access_request import AccessRequest

    async with db_session_factory() as db:
        r = AccessRequest(name="Jane", email="jane@example.com", use_case="testing")
        db.add(r)
        await db.flush()
        rid = r.id
        await db.commit()
    async with db_session_factory() as db:
        r = (await db.execute(select(AccessRequest).where(AccessRequest.id == rid))).scalar_one()
        assert r.status == "pending"
        assert r.email == "jane@example.com"


@pytest.mark.asyncio
async def test_load_user_view_includes_is_superadmin(_patch_db, db_session_factory):
    from types import SimpleNamespace

    from app.api.google_oauth_routes import _load_user_view
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="boss@example.com", is_superadmin=True)
        db.add(u)
        await db.flush()
        uid = str(u.id)
        await db.commit()

    view = await _load_user_view(SimpleNamespace(user_id=uid, email="boss@example.com"))
    assert view["is_superadmin"] is True


@pytest.fixture
async def _http_client(_patch_db):
    import httpx
    from httpx import ASGITransport

    from app.auth.csrf import _generate_csrf_token
    from app.main import app

    csrf = _generate_csrf_token()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver",
        cookies={"csrf_token": csrf}, headers={"x-csrf-token": csrf},
    ) as client:
        yield client


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
async def test_admin_users_requires_superadmin(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    uid = await _make_user(db_session_factory, "plain@example.com", is_superadmin=False)
    with patch("app.api.admin_routes._resolve_user_ctx",
               new=AsyncMock(return_value=type("C", (), {"user_id": uid, "email": "plain@example.com"})())):
        resp = await _http_client.get("/api/admin/users")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_users_lists_for_superadmin(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    sid = await _make_user(db_session_factory, "super@example.com", is_superadmin=True)
    await _make_user(db_session_factory, "member@example.com", is_superadmin=False)
    with patch("app.api.admin_routes._resolve_user_ctx",
               new=AsyncMock(return_value=type("C", (), {"user_id": sid, "email": "super@example.com"})())):
        resp = await _http_client.get("/api/admin/users")
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()["users"]]
    assert "super@example.com" in emails and "member@example.com" in emails


@pytest.mark.asyncio
async def test_admin_users_unauthenticated_401(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    with patch("app.api.admin_routes._resolve_user_ctx", new=AsyncMock(return_value=None)):
        resp = await _http_client.get("/api/admin/users")
    assert resp.status_code == 401
