# tests/test_landing.py
"""Marketing landing page route + brand-awareness."""

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
    import app.branding as _b
    _b._BRAND_CACHE.update({"name": "Fluxito", "logo_url": "", "accent": ""})


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
        follow_redirects=False,
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_landing_renders_for_anonymous(_http_client):
    from unittest.mock import AsyncMock, patch

    with patch("app.api.google_oauth_routes._resolve_user_ctx", new=AsyncMock(return_value=None)):
        resp = await _http_client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "/request-access" in body
    assert "/signin" not in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_landing_redirects_logged_in_to_home(_http_client):
    from unittest.mock import AsyncMock, patch

    ctx = type("C", (), {"user_id": "00000000-0000-0000-0000-000000000001", "email": "a@b.com"})()
    with patch("app.api.google_oauth_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
        resp = await _http_client.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/home"
