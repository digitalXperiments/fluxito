import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services import update_service


@pytest.mark.asyncio
async def test_status_endpoint_returns_check_result(monkeypatch):
    async def _fake_check():
        return {
            "current": "1.0.2", "latest": "1.0.5", "update_available": True,
            "release_notes_url": "https://x", "published_at": "2026-05-30T00:00:00Z",
            "checks_enabled": True,
        }

    monkeypatch.setattr(update_service, "check_for_update", _fake_check)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/updates/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["update_available"] is True
    assert body["latest"] == "1.0.5"
