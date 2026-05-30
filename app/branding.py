"""Instance branding (whitelabel) — name, logo, accent.

A module-level cache warmed at startup and refreshed on the admin PATCH. `brand()`
is sync and non-blocking (Jinja global); `refresh_brand()` reloads it from settings.
"""

from __future__ import annotations

import logging

import app.app_state as app_state

logger = logging.getLogger(__name__)

_DEFAULTS = {"name": "Fluxito", "logo_url": "", "accent": ""}
_BRAND_CACHE: dict = dict(_DEFAULTS)


def brand() -> dict:
    """Return the current brand dict (sync, non-blocking). Jinja global."""
    return _BRAND_CACHE


async def refresh_brand() -> dict:
    """Reload brand settings into the module cache. Safe to call anytime."""
    from app.settings_service import get_runtime_setting

    try:
        async with app_state.db_session_factory() as db:
            name = await get_runtime_setting(db, "brand_name", default="Fluxito")
            logo_url = await get_runtime_setting(db, "brand_logo_url", default="")
            accent = await get_runtime_setting(db, "brand_accent", default="")
        _BRAND_CACHE["name"] = str(name) or "Fluxito"
        _BRAND_CACHE["logo_url"] = str(logo_url or "")
        _BRAND_CACHE["accent"] = str(accent or "")
    except Exception as e:
        logger.warning("refresh_brand failed; keeping last brand cache: %s", e)
    return _BRAND_CACHE
