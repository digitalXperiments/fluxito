import pytest
from httpx import ASGITransport, AsyncClient

from app.api import update_routes
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


@pytest.mark.asyncio
async def test_apply_requires_superadmin(monkeypatch):
    async def _deny(request):
        from fastapi import HTTPException
        raise HTTPException(403, "Super-admin only")

    monkeypatch.setattr(update_routes, "require_superadmin", _deny)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/updates/apply")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_apply_forwards_to_updater(monkeypatch):
    async def _allow(request):
        return {"id": "1", "email": "a@b.c", "is_superadmin": True}

    async def _check():
        return {"current": "1.0.2", "latest": "1.0.5", "update_available": True,
                "release_notes_url": "https://x", "published_at": None, "checks_enabled": True}

    calls = {}

    async def _post(version, previous):
        calls["version"] = version
        calls["previous"] = previous
        return {"status": "accepted", "target": version}

    monkeypatch.setattr(update_routes, "require_superadmin", _allow)
    monkeypatch.setattr(update_service, "check_for_update", _check)
    monkeypatch.setattr(update_routes, "_trigger_updater", _post)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/updates/apply")
    assert resp.status_code == 200
    assert calls["version"] == "1.0.5"
    assert calls["previous"] == "1.0.2"


@pytest.mark.asyncio
async def test_apply_rejects_when_no_update(monkeypatch):
    async def _allow(request):
        return {"id": "1", "email": "a@b.c", "is_superadmin": True}

    async def _check():
        return {"current": "1.0.5", "latest": "1.0.5", "update_available": False,
                "release_notes_url": None, "published_at": None, "checks_enabled": True}

    monkeypatch.setattr(update_routes, "require_superadmin", _allow)
    monkeypatch.setattr(update_service, "check_for_update", _check)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/updates/apply")
    assert resp.status_code == 409
