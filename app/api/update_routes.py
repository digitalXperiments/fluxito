"""Update status + trigger endpoints.

- GET  /api/updates/status — any authenticated user (drives the version badge).
- POST /api/updates/apply  — super-admin only (triggers the updater sidecar).
- GET  /api/updates/job    — super-admin only (updater job status passthrough).
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.admin_routes import require_superadmin
from app.services import update_service

logger = logging.getLogger(__name__)
router = APIRouter()

UPDATER_URL = os.environ.get("UPDATER_URL", "http://updater:9000")
UPDATER_TOKEN = os.environ.get("UPDATER_TOKEN", "")


async def _trigger_updater(version: str, previous: str) -> dict:
    """POST the target version to the updater sidecar. Returns its JSON response."""
    headers = {"Authorization": f"Bearer {UPDATER_TOKEN}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{UPDATER_URL}/update",
            headers=headers,
            json={"version": version, "previous": previous},
        )
        resp.raise_for_status()
        return resp.json()


@router.get("/api/updates/status")
async def update_status(request: Request):
    """Return current vs latest version + whether an update is available."""
    return JSONResponse(await update_service.check_for_update())


@router.post("/api/updates/apply")
async def update_apply(request: Request):
    """Super-admin: trigger an update to the latest available version."""
    await require_superadmin(request)  # raises 401/403
    status = await update_service.check_for_update()
    if not status.get("update_available") or not status.get("latest"):
        return JSONResponse({"error": "no update available"}, status_code=409)
    try:
        result = await _trigger_updater(status["latest"], status["current"])
    except httpx.HTTPError as exc:
        logger.error("updater trigger failed: %s", exc)
        return JSONResponse({"error": "updater unreachable"}, status_code=502)
    return JSONResponse({"status": "started", "target": status["latest"], "updater": result})


@router.get("/api/updates/job")
async def update_job(request: Request):
    """Super-admin: current updater job status (survives app restart via shared volume)."""
    await require_superadmin(request)
    headers = {"Authorization": f"Bearer {UPDATER_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{UPDATER_URL}/status", headers=headers)
            resp.raise_for_status()
            return JSONResponse(resp.json())
    except httpx.HTTPError:
        # During the app's own restart the updater may briefly be unreachable.
        return JSONResponse({"status": "unknown"}, status_code=503)
