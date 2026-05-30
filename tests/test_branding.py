# tests/test_branding.py
"""Whitelabel branding: provider, emails, admin route."""

import pytest

import app.app_state as app_state
import app.models.access_request  # noqa: F401
import app.models.sdr  # noqa: F401


@pytest.fixture
def _patch_db(db_session_factory):
    original = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    yield
    app_state.db_session_factory = original


@pytest.mark.asyncio
async def test_brand_defaults(_patch_db, db_session_factory):
    from app.branding import brand, refresh_brand

    await refresh_brand()
    b = brand()
    assert b["name"] == "Fluxito"
    assert b["logo_url"] == ""
    assert b["accent"] == ""


@pytest.mark.asyncio
async def test_brand_reflects_settings(_patch_db, db_session_factory):
    from app.branding import brand, refresh_brand
    from app.settings_service import set_setting

    async with db_session_factory() as db:
        await set_setting(db, key="brand_name", value="Acme Analytics", is_secret=False, updated_by_user_id=None)
        await set_setting(db, key="brand_logo_url", value="https://x/logo.png", is_secret=False, updated_by_user_id=None)
        await set_setting(db, key="brand_accent", value="#ff0000", is_secret=False, updated_by_user_id=None)
        await db.commit()

    await refresh_brand()
    b = brand()
    assert b["name"] == "Acme Analytics"
    assert b["logo_url"] == "https://x/logo.png"
    assert b["accent"] == "#ff0000"
