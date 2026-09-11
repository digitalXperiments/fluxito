# tests/test_landing.py
"""Marketing landing page route + brand-awareness."""

import pytest

import app.app_state as app_state


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
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"csrf_token": csrf},
        headers={"x-csrf-token": csrf},
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
    assert "/signin" in body
    assert "/request-access" not in body
    assert "/signin" not in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_landing_has_key_content(_http_client):
    from unittest.mock import AsyncMock, patch

    with patch("app.api.google_oauth_routes._resolve_user_ctx", new=AsyncMock(return_value=None)):
        resp = await _http_client.get("/")
    body = resp.text
    assert "27 platforms" in body
    assert "GTM" in body
    assert "MCP" in body


@pytest.mark.asyncio
async def test_landing_redirects_logged_in_to_home(_http_client):
    from unittest.mock import AsyncMock, patch

    ctx = type("C", (), {"user_id": "00000000-0000-0000-0000-000000000001", "email": "a@b.com"})()
    with patch("app.api.google_oauth_routes._resolve_user_ctx", new=AsyncMock(return_value=ctx)):
        resp = await _http_client.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/home"


@pytest.mark.asyncio
async def test_landing_hides_oss_when_rebranded(_http_client):
    from unittest.mock import AsyncMock, patch

    import app.branding as b

    b._BRAND_CACHE.update({"name": "Acme Analytics", "logo_url": "", "accent": ""})
    try:
        with patch("app.api.google_oauth_routes._resolve_user_ctx", new=AsyncMock(return_value=None)):
            resp = await _http_client.get("/")
        body = resp.text
        assert "Acme Analytics" in body
        assert "github.com/digitalXperiments/fluxito" not in body
        assert "Open-source" not in body
    finally:
        b._BRAND_CACHE.update({"name": "Fluxito", "logo_url": "", "accent": ""})


def test_landing_social_share_metadata():
    """Verify that landing.html renders complete OpenGraph and Twitter card metadata for LinkedIn/social sharing."""
    from pathlib import Path

    from starlette.requests import Request

    from app.templating import _base_url_from_request, templates

    req = Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"host", b"fluxito.app"), (b"x-forwarded-proto", b"https")],
        }
    )

    ctx = {
        "request": req,
        "user": None,
        "active": None,
        "base_url": _base_url_from_request(req),
        "github_url": "https://github.com/digitalXperiments/fluxito",
    }

    tmpl = templates.get_template("landing.html")
    html = tmpl.render(ctx)

    # OpenGraph tags for LinkedIn, Facebook, Slack
    assert '<meta property="og:type" content="website"/>' in html
    assert '<meta property="og:url" content="https://fluxito.app/"/>' in html
    assert (
        '<meta property="og:title" content="Fluxito — The analytics hire you never managed to make"/>' in html
    )
    assert 'property="og:description"' in html
    assert '<meta property="og:image" content="https://fluxito.app/static/img/og-preview.png"/>' in html
    assert (
        '<meta property="og:image:secure_url" content="https://fluxito.app/static/img/og-preview.png"/>'
        in html
    )
    assert '<meta property="og:image:width" content="1200"/>' in html
    assert '<meta property="og:image:height" content="630"/>' in html
    assert '<link rel="image_src" href="https://fluxito.app/static/img/og-preview.png"/>' in html

    # Twitter card tags
    assert '<meta name="twitter:card" content="summary_large_image"/>' in html
    assert (
        '<meta name="twitter:title" content="Fluxito — The analytics hire you never managed to make"/>'
        in html
    )
    assert '<meta name="twitter:image" content="https://fluxito.app/static/img/og-preview.png"/>' in html

    # Ensure og-preview.png asset exists on disk and is non-empty
    img_path = Path(__file__).resolve().parent.parent / "app" / "static" / "img" / "og-preview.png"
    assert img_path.is_file()
    assert img_path.stat().st_size > 10_000
