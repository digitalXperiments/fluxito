# tests/test_auditing_routes.py
"""
Tests for Auditing Platform routes using mocked request and context.
Avoids slow and potentially conflicting database setup by mocking DB sessions.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from app.api.auditing_routes import audits_page


@pytest.mark.asyncio
async def test_audits_page_without_project():
    """
    Verify the /audits page loads and lists all 20 platforms even when no project_id is present.
    """
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/audits",
        "headers": [],
    }
    request = Request(scope=scope)
    request.state.active_project_name = None
    request.state.active_project_id = None
    request.state.active_project_plan = "free"
    request.state.active_project_role = None
    request.state.nav_projects = []

    user_ctx = MagicMock()
    user_ctx.user_id = "00000000-0000-0000-0000-000000000001"
    user_ctx.email = "testuser@example.com"

    user_view = {"email": "testuser@example.com", "display_name": "Test User"}

    with (
        patch("app.api.auditing_routes._resolve_user_ctx", new=AsyncMock(return_value=user_ctx)),
        patch("app.api.auditing_routes._load_user_view", new=AsyncMock(return_value=user_view)),
        patch("app.api.auditing_routes._resolve_project_id", new=AsyncMock(return_value=None)),
        patch("app.templating._base_url_from_request", return_value="http://testserver"),
    ):
        response = await audits_page(request)

    assert response.status_code == 200
    html = response.body.decode()
    # Rule Books tab badge should show 20
    assert 'Rule Books <span class="t-seg-count">20</span>' in html
    # Check that platforms like ga4_ecom are displayed (Note: Ecommerce without hyphen)
    assert "Google Analytics 4 (Ecommerce)" in html
    assert "Meta Pixel" in html


@pytest.mark.asyncio
async def test_audits_page_with_project():
    """
    Verify the /audits page loads, queries database, and lists all 20 platforms when a project_id is present.
    """
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/audits",
        "headers": [],
    }
    request = Request(scope=scope)
    request.state.active_project_name = "Test Project"
    request.state.active_project_id = uuid.uuid4()
    request.state.active_project_plan = "free"
    request.state.active_project_role = "owner"
    request.state.nav_projects = []

    user_ctx = MagicMock()
    user_ctx.user_id = "00000000-0000-0000-0000-000000000002"
    user_ctx.email = "testuser2@example.com"

    user_view = {"email": "testuser2@example.com", "display_name": "Test User 2"}
    project_id = uuid.uuid4()

    # Mock DB execution
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()

    # Mock result for runs query
    mock_runs_result = MagicMock()
    mock_runs_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))

    # Mock result for score summary query
    mock_ss_result = MagicMock()
    mock_ss_result.mappings = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))

    # Return mock results on DB execute calls
    mock_db.execute.side_effect = [mock_runs_result, mock_ss_result]

    mock_db_context = MagicMock()
    mock_db_context.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_context.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.api.auditing_routes._resolve_user_ctx", new=AsyncMock(return_value=user_ctx)),
        patch("app.api.auditing_routes._load_user_view", new=AsyncMock(return_value=user_view)),
        patch("app.api.auditing_routes._resolve_project_id", new=AsyncMock(return_value=project_id)),
        patch("app.templating._base_url_from_request", return_value="http://testserver"),
        patch("app.app_state.db_session_factory", return_value=mock_db_context),
    ):
        response = await audits_page(request)

    assert response.status_code == 200
    html = response.body.decode()
    # Rule Books tab badge should show 20
    assert 'Rule Books <span class="t-seg-count">20</span>' in html
    assert "Google Analytics 4 (Ecommerce)" in html
