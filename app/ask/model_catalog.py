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
        "claude-opus-5",
        "claude-opus-5-fast",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-opus-4.8",
        "claude-sonnet-4.6",
        "claude-haiku-4.5",
        "claude-3-7-sonnet-latest",
        "claude-3-5-sonnet-latest",
    ],
    "openai": [
        "gpt-5.6-sol-pro",
        "gpt-5.6-sol",
        "gpt-5.6-terra-pro",
        "gpt-5.6-terra",
        "gpt-5.6-luna-pro",
        "gpt-5.6-luna",
        "gpt-5.5-pro",
        "gpt-5.5",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.3-codex",
        "o3-pro",
        "o3-mini",
        "o4-mini",
        "gpt-4.1",
    ],
    "grok": [
        "grok-4.6",
        "grok-4.5",
        "grok-4.3",
        "grok-4.20",
        "grok-3",
        "grok-3-mini",
    ],
    "gemini": [
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ],
    "mistral": [
        "mistral-medium-3-5",
        "mistral-large-2512",
        "mistral-small-2603",
        "devstral-2512",
        "ministral-14b-2512",
        "ministral-8b-2512",
        "codestral-2508",
        "pixtral-large-latest",
    ],
    "lmstudio": [
        "deepseek-r1-distill-qwen-32b",
        "qwen2.5-coder-32b-instruct",
        "llama-3.3-70b-instruct",
        "deepseek-reasoner",
        "deepseek-chat",
        "phi-4",
    ],
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
    if app_state.db_session_factory is None:
        return {}
    try:
        async with app_state.db_session_factory() as db:
            raw = await get_setting(db, _SETTING_KEY, default=None)
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items() if k in SUPPORTED_PROVIDERS and isinstance(v, list)}
    except Exception:
        return {}


async def set_extra_models(value: dict[str, list[str]]) -> None:
    """Replace the extra models dict. Only keeps known providers."""
    cleaned = {k: v for k, v in value.items() if k in SUPPORTED_PROVIDERS and isinstance(v, list)}
    async with app_state.db_session_factory() as db:
        await set_setting(db, key=_SETTING_KEY, value=cleaned, is_secret=False)
        await db.commit()


_EXCLUDED_MODEL_SUBSTRINGS = (
    "text-embedding",
    "embedding",
    "whisper",
    "tts-",
    "dall-e",
    "babbage",
    "davinci",
    "curie",
    "ada",
    "canary",
    "text-moderation",
    "omni-moderation",
    "instruct-preview",
    "realtime-preview",
    "audio-preview",
    "search-preview",
    "transcription",
    "translation",
    "moderation",
    ":batch",
)


def _is_excluded_model(model_id: str) -> bool:
    lower = model_id.lower()
    return any(p in lower for p in _EXCLUDED_MODEL_SUBSTRINGS)


async def get_merged_catalog() -> dict[str, list[CatalogEntry]]:
    """Return the full merged catalog keyed by provider.

    For each provider, models are ordered by priority:
    1. Curated Builtin flagship models (exact priority order: newest first)
    2. Extra/Custom models added by administrators
    3. Live models synced from provider APIs (non-excluded, active only)
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

        # 1. Curated Builtin models (preserves flagship priority order)
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

        # 2. Extra models (superadmin-managed custom models)
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

        # 3. Live models (from vendor API / public fetcher)
        for row in live_rows:
            if row.provider == provider:
                if _is_excluded_model(row.model_id) or row.is_deprecated or not row.is_enabled:
                    continue
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

        catalog[provider] = entries

    return catalog

    return catalog


async def get_catalog_for_provider(provider: str) -> list[CatalogEntry]:
    """Return the merged catalog for a single provider."""
    catalog = await get_merged_catalog()
    return catalog.get(provider, [])


async def _load_live_models() -> list[AiCatalogModel]:
    """Load all live-synced models from the DB."""
    if app_state.db_session_factory is None:
        return []
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
