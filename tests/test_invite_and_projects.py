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
async def test_register_claims_passwordless_placeholder(_patch_db, db_session_factory):
    """An invited placeholder (no password_hash) gets claimed, not orphaned."""
    from sqlalchemy import select

    from app.auth.email_auth import authenticate_user, register_user
    from app.models.user import User

    # Seed a placeholder like invite_member creates (no password, default provider).
    async with db_session_factory() as db:
        placeholder = User(email="invitee@example.com")
        db.add(placeholder)
        await db.flush()
        placeholder_id = placeholder.id
        await db.commit()

    user, error = await register_user("invitee@example.com", "Sup3rSecret!", "Invitee")
    assert error is None
    assert user is not None
    # Same row claimed, not a new user
    assert user.id == placeholder_id

    async with db_session_factory() as db:
        rows = (await db.execute(select(User).where(User.email == "invitee@example.com"))).scalars().all()
    assert len(rows) == 1

    # And they can now log in with the chosen password
    authed, auth_err = await authenticate_user("invitee@example.com", "Sup3rSecret!")
    assert auth_err is None
    assert authed.id == placeholder_id


@pytest.mark.asyncio
async def test_register_rejects_existing_password_account(_patch_db, db_session_factory):
    """If a real (password-set) account exists, registration still errors."""
    from app.auth.email_auth import register_user

    await register_user("taken@example.com", "FirstPass1!", "First")
    user, error = await register_user("taken@example.com", "SecondPass1!", "Second")
    assert user is None
    assert error and "already exists" in error.lower()
