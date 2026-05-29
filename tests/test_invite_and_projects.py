# tests/test_invite_and_projects.py
"""DB + endpoint tests for invite temp-password, register-claim, default project, reset."""

import uuid

import pytest

import app.app_state as app_state


@pytest.fixture
def _patch_db(db_session_factory):
    original = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    yield
    app_state.db_session_factory = original


@pytest.mark.asyncio
async def test_register_does_not_claim_passwordless_account(_patch_db, db_session_factory):
    """A password-less existing account (Google user or invited stub) must NOT be
    silently claimed by registration — that would be an account-takeover vector."""
    from app.auth.email_auth import authenticate_user, register_user
    from app.models.user import User

    # Seed a password-less row (auth_provider defaults to "google" via server_default).
    async with db_session_factory() as db:
        placeholder = User(email="invitee@example.com")
        db.add(placeholder)
        await db.commit()

    user, error = await register_user("invitee@example.com", "Sup3rSecret!", "Invitee")
    assert user is None
    assert error is not None  # rejected, not claimed

    # The password was NOT set on the existing account.
    authed, auth_err = await authenticate_user("invitee@example.com", "Sup3rSecret!")
    assert authed is None


@pytest.mark.asyncio
async def test_register_rejects_existing_password_account(_patch_db, db_session_factory):
    """If a real (password-set) account exists, registration still errors."""
    from app.auth.email_auth import register_user

    await register_user("taken@example.com", "FirstPass1!", "First")
    user, error = await register_user("taken@example.com", "SecondPass1!", "Second")
    assert user is None
    assert error and "already exists" in error.lower()


@pytest.mark.asyncio
async def test_ensure_default_project_creates_when_none(_patch_db, db_session_factory):
    from sqlalchemy import select

    from app.api.project_routes import ensure_default_project
    from app.models.project import ProjectMember
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="solo@example.com", display_name="Solo")
        db.add(u)
        await db.flush()
        uid = u.id
        await db.commit()

    created = await ensure_default_project(uid, "Solo", "solo@example.com")
    assert created is True

    async with db_session_factory() as db:
        memberships = (
            await db.execute(select(ProjectMember).where(ProjectMember.user_id == uid))
        ).scalars().all()
    assert len(memberships) == 1
    assert memberships[0].role == "owner"


@pytest.mark.asyncio
async def test_ensure_default_project_noop_when_member(_patch_db, db_session_factory):
    from app.api.project_routes import ensure_default_project
    from app.models.project import Project, ProjectMember
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email="hasproj@example.com")
        db.add(u)
        await db.flush()
        p = Project(name="X", slug="x-existing", owner_id=u.id)
        db.add(p)
        await db.flush()
        db.add(ProjectMember(project_id=p.id, user_id=u.id, role="owner"))
        uid = u.id
        await db.commit()

    created = await ensure_default_project(uid, None, "hasproj@example.com")
    assert created is False


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


async def _seed_owner_and_project(db_session_factory, slug="acme"):
    from app.models.project import Project, ProjectMember
    from app.models.user import User

    async with db_session_factory() as db:
        owner = User(email="owner@example.com", display_name="Owner")
        db.add(owner)
        await db.flush()
        proj = Project(name="Acme", slug=slug, owner_id=owner.id)
        db.add(proj)
        await db.flush()
        db.add(ProjectMember(project_id=proj.id, user_id=owner.id, role="owner"))
        await db.commit()
        return str(owner.id), str(owner.email), slug


@pytest.mark.asyncio
async def test_invite_returns_temp_password_and_member_can_login(
    _http_client, db_session_factory
):
    from unittest.mock import AsyncMock, patch

    from app.auth.email_auth import authenticate_user

    owner_id, owner_email, slug = await _seed_owner_and_project(db_session_factory)
    owner_ctx = {"user_id": owner_id, "email": owner_email}

    with patch(
        "app.api.project_routes._resolve_user",
        new=AsyncMock(return_value=owner_ctx),
    ):
        resp = await _http_client.post(
            f"/api/project/{slug}/members",
            json={"email": "newhire@example.com", "role": "member"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("temp_password")
    assert data.get("smtp_sent") is False

    user, err = await authenticate_user("newhire@example.com", data["temp_password"])
    assert err is None
    assert user is not None


@pytest.mark.asyncio
async def test_invite_existing_password_account_not_reset(_http_client, db_session_factory):
    """Inviting an existing real account must NOT reset its password or return one."""
    from unittest.mock import AsyncMock, patch

    from app.auth.email_auth import authenticate_user, hash_password
    from app.models.user import User

    owner_id, owner_email, slug = await _seed_owner_and_project(db_session_factory, slug="acme2")
    async with db_session_factory() as db:
        db.add(User(email="real@example.com", password_hash=hash_password("orig-pass-1!"),
                    email_verified=True, auth_provider="email"))
        await db.commit()

    owner_ctx = {"user_id": owner_id, "email": owner_email}
    with patch("app.api.project_routes._resolve_user", new=AsyncMock(return_value=owner_ctx)):
        resp = await _http_client.post(
            f"/api/project/{slug}/members", json={"email": "real@example.com", "role": "member"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("temp_password") is None
    # Original password still works (not reset).
    user, err = await authenticate_user("real@example.com", "orig-pass-1!")
    assert err is None and user is not None


@pytest.mark.asyncio
async def test_invite_existing_google_user_not_hijacked(_http_client, db_session_factory):
    """Inviting an existing Google-only user must NOT set a password or return one."""
    from unittest.mock import AsyncMock, patch

    from sqlalchemy import select

    from app.models.user import User

    owner_id, owner_email, slug = await _seed_owner_and_project(db_session_factory, slug="acme3")
    async with db_session_factory() as db:
        g = User(email="googler@example.com", auth_provider="google", email_verified=True)
        db.add(g)
        await db.flush()
        gid = g.id
        await db.commit()

    owner_ctx = {"user_id": owner_id, "email": owner_email}
    with patch("app.api.project_routes._resolve_user", new=AsyncMock(return_value=owner_ctx)):
        resp = await _http_client.post(
            f"/api/project/{slug}/members", json={"email": "googler@example.com", "role": "member"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("temp_password") is None
    # Credentials untouched: still no password, still google.
    async with db_session_factory() as db:
        row = (await db.execute(select(User).where(User.id == gid))).scalar_one()
        assert row.password_hash is None
        assert row.auth_provider == "google"
