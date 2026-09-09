"""Tests for public legal routes (Privacy Policy, Terms of Service) and brand assets."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request

from app.api.legal_routes import privacy_policy, terms_of_service

STATIC_IMG = Path(__file__).resolve().parent.parent / "app" / "static" / "img"


def _make_request(path: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(b"host", b"fluxito.app")],
    }
    req = Request(scope=scope)
    req.state.active_project_name = None
    req.state.active_project_id = None
    req.state.active_project_plan = "free"
    req.state.active_project_role = None
    req.state.nav_projects = []
    return req


@pytest.mark.asyncio
async def test_privacy_policy_page():
    req = _make_request("/privacy")
    with patch("app.api.legal_routes._resolve_user_ctx", new_callable=AsyncMock) as mock_ctx:
        mock_ctx.return_value = None
        response = await privacy_policy(req)

    assert response.status_code == 200
    html = response.body.decode("utf-8")
    assert "Privacy" in html
    assert "Policy" in html
    # Verify Google API Services User Data Policy / Limited Use clause
    assert "Google API Services User Data Policy" in html
    assert "Limited Use" in html
    # Verify key MCP and security disclosures
    assert "Model Context Protocol" in html or "MCP" in html
    assert "AES-256" in html
    assert "Zero AI Training" in html or "We do not use your data to train" in html
    # Verify Google scopes
    assert ".../auth/analytics.readonly" in html
    assert ".../auth/tagmanager.readonly" in html


@pytest.mark.asyncio
async def test_terms_of_service_page():
    req = _make_request("/terms")
    with patch("app.api.legal_routes._resolve_user_ctx", new_callable=AsyncMock) as mock_ctx:
        mock_ctx.return_value = None
        response = await terms_of_service(req)

    assert response.status_code == 200
    html = response.body.decode("utf-8")
    assert "Terms of" in html
    assert "Service" in html
    assert "Model Context Protocol" in html or "MCP" in html
    assert "Apache" in html


def test_brand_assets_exist_and_non_empty():
    assets = [
        "fluxito-wordmark.svg",
        "fluxito-wordmark-light.svg",
        "fluxito-logo.svg",
        "fluxito-logo-light.svg",
        "fluxito-mark.svg",
        "fluxito-logo-120.png",
        "fluxito-logo-512.png",
    ]
    for asset in assets:
        file_path = STATIC_IMG / asset
        assert file_path.is_file(), f"Missing brand asset: {asset}"
        assert file_path.stat().st_size > 0, f"Empty brand asset: {asset}"

    # Verify 120x120 PNG image dimensions for Google OAuth Consent Screen
    from PIL import Image

    png_120 = Image.open(STATIC_IMG / "fluxito-logo-120.png")
    assert png_120.size == (120, 120), f"Expected (120, 120), got {png_120.size}"

    png_512 = Image.open(STATIC_IMG / "fluxito-logo-512.png")
    assert png_512.size == (512, 512), f"Expected (512, 512), got {png_512.size}"
