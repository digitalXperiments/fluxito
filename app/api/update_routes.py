"""Update status + trigger endpoints.

- GET  /api/updates/status — any authenticated user (drives the version badge).
- POST /api/updates/apply  — super-admin only (triggers the updater sidecar).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services import update_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/updates/status")
async def update_status(request: Request):
    """Return current vs latest version + whether an update is available."""
    return JSONResponse(await update_service.check_for_update())
