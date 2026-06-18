"""Superadmin-managed extra model names for the AI keys dropdown."""

from __future__ import annotations

from app import app_state
from app.ask.providers.registry import SUPPORTED_PROVIDERS
from app.settings_service import get_setting, set_setting

_SETTING_KEY = "ask_extra_models"


async def get_extra_models() -> dict[str, list[str]]:
    """Return the extra models dict {provider: [model, ...]}. Empty dict if none."""
    async with app_state.db_session_factory() as db:
        raw = await get_setting(db, _SETTING_KEY, default=None)
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in SUPPORTED_PROVIDERS and isinstance(v, list)}


async def set_extra_models(value: dict[str, list[str]]) -> None:
    """Replace the extra models dict. Only keeps known providers."""
    cleaned = {k: v for k, v in value.items() if k in SUPPORTED_PROVIDERS and isinstance(v, list)}
    async with app_state.db_session_factory() as db:
        await set_setting(db, key=_SETTING_KEY, value=cleaned, is_secret=False)
        await db.commit()
