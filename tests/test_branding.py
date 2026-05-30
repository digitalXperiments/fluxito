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
    # Reset the module-global brand cache so branding tests never leak
    # a non-default value into other tests/files.
    import app.branding as _b
    _b._BRAND_CACHE.update({"name": "Fluxito", "logo_url": "", "accent": ""})


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


@pytest.mark.asyncio
async def test_invite_email_uses_brand_name(_patch_db, db_session_factory, monkeypatch):
    from app.branding import refresh_brand
    from app.settings_service import set_setting
    import app.email_service as es

    async with db_session_factory() as db:
        await set_setting(db, key="brand_name", value="Acme Analytics", is_secret=False, updated_by_user_id=None)
        await db.commit()
    await refresh_brand()

    captured = {}

    async def _fake_send_email(to_email, subject, html_body, text_body=None):
        captured["subject"] = subject
        captured["html"] = html_body
        captured["text"] = text_body

    monkeypatch.setattr(es, "send_email", _fake_send_email)
    await es.send_project_invite_email(
        to_email="x@example.com", project_name="Proj", project_slug="proj",
        inviter_email="boss@example.com", role="member",
    )
    assert "Acme Analytics" in captured["subject"]
    assert "Acme Analytics" in captured["html"]
    assert "Fluxito" not in captured["subject"]

