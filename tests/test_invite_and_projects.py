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
