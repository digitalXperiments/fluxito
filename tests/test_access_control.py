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
