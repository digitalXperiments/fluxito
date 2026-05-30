# tests/test_access_control.py
"""Backend tests for the access-control core (super-admin + admin panel + request-access)."""

import uuid

import pytest

import app.app_state as app_state
import app.models.access_request
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
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"csrf_token": csrf},
        headers={"x-csrf-token": csrf},
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
    with patch(
        "app.api.admin_routes._resolve_user_ctx",
        new=AsyncMock(return_value=type("C", (), {"user_id": uid, "email": "plain@example.com"})()),
    ):
        resp = await _http_client.get("/api/admin/users")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_users_lists_for_superadmin(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    sid = await _make_user(db_session_factory, "super@example.com", is_superadmin=True)
    await _make_user(db_session_factory, "member@example.com", is_superadmin=False)
    with patch(
        "app.api.admin_routes._resolve_user_ctx",
        new=AsyncMock(return_value=type("C", (), {"user_id": sid, "email": "super@example.com"})()),
    ):
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


@pytest.mark.asyncio
async def test_admin_cannot_revoke_last_superadmin(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    sid = await _make_user(db_session_factory, "solo-super@example.com", is_superadmin=True)
    ctx = type("C", (), {"user_id": sid, "email": "solo-super@example.com"})()
    with patch("app.api.admin_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
        resp = await _http_client.patch(f"/api/admin/users/{sid}/superadmin", json={"is_superadmin": False})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_self(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    sid = await _make_user(db_session_factory, "self@example.com", is_superadmin=True)
    ctx = type("C", (), {"user_id": sid, "email": "self@example.com"})()
    with patch("app.api.admin_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
        resp = await _http_client.patch(f"/api/admin/users/{sid}/active", json={"is_active": False})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_can_deactivate_other_user(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    from sqlalchemy import select

    from app.models.user import User

    sid = await _make_user(db_session_factory, "s2@example.com", is_superadmin=True)
    tid = await _make_user(db_session_factory, "victim@example.com", is_superadmin=False)
    ctx = type("C", (), {"user_id": sid, "email": "s2@example.com"})()
    with patch("app.api.admin_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
        resp = await _http_client.patch(f"/api/admin/users/{tid}/active", json={"is_active": False})
    assert resp.status_code == 200
    async with db_session_factory() as db:
        u = (await db.execute(select(User).where(User.id == uuid.UUID(tid)))).scalar_one()
        assert u.is_active is False


@pytest.mark.asyncio
async def test_admin_can_grant_superadmin(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    from sqlalchemy import select

    from app.models.user import User

    sid = await _make_user(db_session_factory, "granter@example.com", is_superadmin=True)
    tid = await _make_user(db_session_factory, "promote@example.com", is_superadmin=False)
    ctx = type("C", (), {"user_id": sid, "email": "granter@example.com"})()
    with patch("app.api.admin_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
        resp = await _http_client.patch(f"/api/admin/users/{tid}/superadmin", json={"is_superadmin": True})
    assert resp.status_code == 200
    async with db_session_factory() as db:
        u = (await db.execute(select(User).where(User.id == uuid.UUID(tid)))).scalar_one()
        assert u.is_superadmin is True


@pytest.mark.asyncio
async def test_request_access_creates_pending(_http_client, db_session_factory):
    from sqlalchemy import select

    from app.models.access_request import AccessRequest

    resp = await _http_client.post(
        "/request-access", json={"name": "Jane", "email": "newbie@example.com", "use_case": "kicking tires"}
    )
    assert resp.status_code == 200, resp.text
    async with db_session_factory() as db:
        r = (
            await db.execute(select(AccessRequest).where(AccessRequest.email == "newbie@example.com"))
        ).scalar_one()
        assert r.status == "pending"


@pytest.mark.asyncio
async def test_request_access_dedupes_existing_user(_http_client, db_session_factory):
    await _make_user(db_session_factory, "exists@example.com")
    resp = await _http_client.post("/request-access", json={"name": "X", "email": "exists@example.com"})
    assert resp.status_code == 400
    assert "sign in" in resp.json().get("error", "").lower()


@pytest.mark.asyncio
async def test_request_access_dedupes_pending(_http_client, db_session_factory):
    await _http_client.post("/request-access", json={"name": "A", "email": "dup@example.com"})
    resp = await _http_client.post("/request-access", json={"name": "A", "email": "dup@example.com"})
    assert resp.status_code == 400
    assert "pending" in resp.json().get("error", "").lower()


@pytest.mark.asyncio
async def test_register_blocked_when_gate_on(_http_client, db_session_factory):
    from app.settings_service import set_setting

    async with db_session_factory() as db:
        await set_setting(
            db, key="require_access_approval", value=True, is_secret=False, updated_by_user_id=None
        )
        await db.commit()
    try:
        resp = await _http_client.post(
            "/auth/register",
            json={"email": "blocked@example.com", "password": "password123", "display_name": "B"},
        )
        assert resp.status_code == 403
        assert "request access" in resp.json().get("error", "").lower()
    finally:
        async with db_session_factory() as db:
            await set_setting(
                db, key="require_access_approval", value=False, is_secret=False, updated_by_user_id=None
            )
            await db.commit()


@pytest.mark.asyncio
async def test_register_open_when_gate_off(_http_client, db_session_factory):
    resp = await _http_client.post(
        "/auth/register", json={"email": "open@example.com", "password": "password123", "display_name": "O"}
    )
    assert resp.status_code in (200, 201)


@pytest.mark.asyncio
async def test_approve_provisions_user_with_temp_password(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    from sqlalchemy import select

    from app.auth.email_auth import authenticate_user
    from app.models.access_request import AccessRequest

    sid = await _make_user(db_session_factory, "approver@example.com", is_superadmin=True)
    async with db_session_factory() as db:
        r = AccessRequest(name="Newbie", email="newbie2@example.com", use_case="x")
        db.add(r)
        await db.flush()
        rid = str(r.id)
        await db.commit()

    ctx = type("C", (), {"user_id": sid, "email": "approver@example.com"})()
    with patch("app.api.admin_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
        resp = await _http_client.post(f"/api/admin/access-requests/{rid}/approve")
    assert resp.status_code == 200, resp.text
    pw = resp.json()["temp_password"]
    assert pw
    user, err = await authenticate_user("newbie2@example.com", pw)
    assert err is None and user is not None

    async with db_session_factory() as db:
        r = (
            await db.execute(select(AccessRequest).where(AccessRequest.email == "newbie2@example.com"))
        ).scalar_one()
        assert r.status == "approved"


@pytest.mark.asyncio
async def test_approve_existing_password_account_not_reset(_http_client, db_session_factory):
    """If the email already has a password account, approval must NOT reset it."""
    from unittest.mock import AsyncMock, patch

    from app.auth.email_auth import authenticate_user, hash_password
    from app.models.access_request import AccessRequest
    from app.models.user import User

    sid = await _make_user(db_session_factory, "appr2@example.com", is_superadmin=True)
    async with db_session_factory() as db:
        db.add(
            User(
                email="hasacct@example.com",
                password_hash=hash_password("origpass1!"),
                email_verified=True,
                auth_provider="email",
            )
        )
        r = AccessRequest(name="Has Acct", email="hasacct@example.com")
        db.add(r)
        await db.flush()
        rid = str(r.id)
        await db.commit()

    ctx = type("C", (), {"user_id": sid, "email": "appr2@example.com"})()
    with patch("app.api.admin_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
        resp = await _http_client.post(f"/api/admin/access-requests/{rid}/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json().get("temp_password") is None
    # original password still works (not reset)
    user, err = await authenticate_user("hasacct@example.com", "origpass1!")
    assert err is None and user is not None


@pytest.mark.asyncio
async def test_reject_creates_no_account(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    from sqlalchemy import select

    from app.models.access_request import AccessRequest
    from app.models.user import User

    sid = await _make_user(db_session_factory, "appr3@example.com", is_superadmin=True)
    async with db_session_factory() as db:
        r = AccessRequest(name="Nope", email="nope@example.com")
        db.add(r)
        await db.flush()
        rid = str(r.id)
        await db.commit()

    ctx = type("C", (), {"user_id": sid, "email": "appr3@example.com"})()
    with patch("app.api.admin_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
        resp = await _http_client.post(f"/api/admin/access-requests/{rid}/reject")
    assert resp.status_code == 200
    async with db_session_factory() as db:
        assert (
            await db.execute(select(User).where(User.email == "nope@example.com"))
        ).scalar_one_or_none() is None
        r = (
            await db.execute(select(AccessRequest).where(AccessRequest.email == "nope@example.com"))
        ).scalar_one()
        assert r.status == "rejected"


@pytest.mark.asyncio
async def test_toggle_gate(_http_client, db_session_factory):
    from unittest.mock import AsyncMock, patch

    from app.settings_service import access_approval_required

    sid = await _make_user(db_session_factory, "toggler@example.com", is_superadmin=True)
    ctx = type("C", (), {"user_id": sid, "email": "toggler@example.com"})()
    with patch("app.api.admin_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
        resp = await _http_client.patch("/api/admin/settings/require-access-approval", json={"enabled": True})
    assert resp.status_code == 200
    try:
        assert await access_approval_required() is True
    finally:
        with patch("app.api.admin_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
            await _http_client.patch("/api/admin/settings/require-access-approval", json={"enabled": False})
