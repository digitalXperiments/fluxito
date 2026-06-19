"""Superadmin-managed extra model names + merged catalog builtin/live/extra.

The merged catalog combines three sources:
  - **builtin**: Hardcoded in model_metadata.py (curated display names, capabilities)
  - **live**:  Fetched from vendor APIs via model_sync, stored in ai_catalog_models
  - **extra**: Superadmin-managed extras, stored in app_settings JSONB
"""

from __future__ import annotations

from dataclasses import dataclass

from app import app_state
from app.ask.model_metadata import enrich_model
from app.ask.providers.registry import SUPPORTED_PROVIDERS
from app.models.ai_catalog import AiCatalogModel
from app.settings_service import get_setting, set_setting

_SETTING_KEY = "ask_extra_models"

_BUILTIN_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "o3-mini",
        "o4-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    ],
    "grok": [
        "grok-2-latest",
        "grok-3",
        "grok-3-mini-latest",
    ],
    "gemini": [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
    ],
    "mistral": [
        "mistral-large-latest",
        "mistral-small-latest",
        "codestral-latest",
    ],
    "lmstudio": [],
}


@dataclass
class CatalogEntry:
    provider: str
    model_id: str
    display_name: str | None
    context_window: int | None
    capabilities: list[str]
    is_deprecated: bool
    source: str
    is_enabled: bool
    id_: str | None = None


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


async def get_merged_catalog() -> dict[str, list[CatalogEntry]]:
    """Return the full merged catalog keyed by provider.

    For each provider, models are ordered: builtin → live → extra.
    Within each source, models are sorted alphabetically by model_id.
    """
    extras = await get_extra_models()
    live_rows = await _load_live_models()

    catalog: dict[str, list[CatalogEntry]] = {}

    for provider in SUPPORTED_PROVIDERS:
        entries: list[CatalogEntry] = []
        seen: set[str] = set()

        def add(entry: CatalogEntry, _seen=seen, _entries=entries) -> None:
            key = entry.model_id
            if key in _seen:
                return
            _seen.add(key)
            _entries.append(entry)

        # 1. Builtin models
        for mid in _BUILTIN_MODELS.get(provider, []):
            meta = enrich_model(mid, provider)
            add(
                CatalogEntry(
                    provider=provider,
                    model_id=mid,
                    display_name=meta.get("display_name"),
                    context_window=meta.get("context_window"),
                    capabilities=meta.get("capabilities") or [],
                    is_deprecated=False,
                    source="builtin",
                    is_enabled=True,
                )
            )

        # 2. Live models (from vendor API)
        for row in live_rows:
            if row.provider == provider:
                add(
                    CatalogEntry(
                        provider=row.provider,
                        model_id=row.model_id,
                        display_name=row.display_name,
                        context_window=row.context_window,
                        capabilities=row.capabilities or [],
                        is_deprecated=row.is_deprecated,
                        source="live",
                        is_enabled=row.is_enabled,
                        id_=str(row.id),
                    )
                )

        # 3. Extra models (superadmin-managed)
        for mid in extras.get(provider, []):
            meta = enrich_model(mid, provider)
            add(
                CatalogEntry(
                    provider=provider,
                    model_id=mid,
                    display_name=meta.get("display_name"),
                    context_window=meta.get("context_window"),
                    capabilities=meta.get("capabilities") or [],
                    is_deprecated=False,
                    source="extra",
                    is_enabled=True,
                )
            )

        catalog[provider] = sorted(entries, key=lambda e: (e.source, e.model_id))

    return catalog


async def get_catalog_for_provider(provider: str) -> list[CatalogEntry]:
    """Return the merged catalog for a single provider."""
    catalog = await get_merged_catalog()
    return catalog.get(provider, [])


async def _load_live_models() -> list[AiCatalogModel]:
    """Load all live-synced models from the DB."""
    from sqlalchemy import select

    try:
        async with app_state.db_session_factory() as db:
            rows = (
                (await db.execute(select(AiCatalogModel).where(AiCatalogModel.source == "live")))
                .scalars()
                .all()
            )
            return list(rows)
    except Exception:
        return []
