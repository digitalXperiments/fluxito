"""Fetch live model lists from AI vendor APIs and persist to ai_catalog_models.

Supports OpenAI-compatible providers (OpenAI, xAI Grok, Mistral, Gemini via
its OpenAI compatibility layer) and Anthropic's native models endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app import app_state
from app.ask.model_metadata import enrich_model
from app.ask.providers.registry import OPENAI_COMPAT
from app.models.ai_catalog import AiCatalogModel

SYNC_TIMEOUT_S = 15.0
USER_AGENT = "Fluxito/1.0 ModelCatalogSync"

ANTHROPIC_VERSION = "2023-06-01"


@dataclass
class SyncResult:
    provider: str
    model_count: int
    errors: list[str]


@dataclass
class RawModel:
    id: str
    display_name: str | None = None
    is_deprecated: bool = False


async def sync_all_providers() -> list[SyncResult]:
    """Fetch models from every provider that has at least one stored key.

    Returns a list of per-provider sync results.
    """
    results: list[SyncResult] = []
    async with app_state.db_session_factory() as db:
        from sqlalchemy import select
        from sqlalchemy.orm import load_only

        from app.models.conversation import AIProviderKey

        rows = (
            (
                await db.execute(
                    select(AIProviderKey)
                    .where(AIProviderKey.is_active.is_(True))
                    .options(
                        load_only(
                            AIProviderKey.provider, AIProviderKey.api_key_encrypted, AIProviderKey.base_url
                        )
                    )
                    .distinct(AIProviderKey.provider)
                )
            )
            .scalars()
            .all()
        )

        if not rows:
            return results

        seen: set[str] = set()
        for row in rows:
            prov = row.provider
            if prov in seen:
                continue
            seen.add(prov)
            if prov == "lmstudio":
                continue
            from app.utils.encryption import decrypt_str

            api_key = decrypt_str(row.api_key_encrypted)
            result = await sync_provider(prov, api_key, base_url=row.base_url)
            results.append(result)
    return results


async def sync_provider(provider: str, api_key: str, base_url: str | None = None) -> SyncResult:
    """Fetch models from a single provider and persist them."""
    errors: list[str] = []
    models: list[RawModel] = []

    try:
        if provider == "anthropic":
            models = await _fetch_anthropic_models(api_key)
        elif provider in OPENAI_COMPAT:
            cfg = OPENAI_COMPAT[provider]
            effective_base = base_url or str(cfg["base_url"])
            models = await _fetch_openai_compat_models(str(cfg["base_url"]), effective_base, api_key)
        else:
            errors.append(f"Unsupported provider: {provider}")
    except Exception as exc:
        errors.append(f"Sync failed: {exc}")
        return SyncResult(provider=provider, model_count=0, errors=errors)

    await _persist_models(provider, models)
    return SyncResult(provider=provider, model_count=len(models), errors=errors)


async def _fetch_openai_compat_models(
    registry_base: str, effective_base: str, api_key: str
) -> list[RawModel]:
    """Fetch models from any OpenAI-compatible /v1/models endpoint."""
    url = effective_base.rstrip("/") + "/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
    }
    async with httpx.AsyncClient(timeout=SYNC_TIMEOUT_S) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        body = resp.json()

    raw_list: list[dict[str, Any]] = body.get("data", [])
    results: list[RawModel] = []
    for item in raw_list:
        mid: str = item.get("id", "")
        if not mid:
            continue
        results.append(
            RawModel(
                id=mid,
                display_name=None,
                is_deprecated=_is_deprecated_openai(item),
            )
        )
    return results


def _is_deprecated_openai(item: dict[str, Any]) -> bool:
    """Check if an OpenAI-compatible model is deprecated/ended."""
    owned = (item.get("owned_by") or "").lower()
    return "deprecated" in owned


async def _fetch_anthropic_models(api_key: str) -> list[RawModel]:
    """Fetch models from Anthropic's native /v1/models endpoint."""
    url = "https://api.anthropic.com/v1/models"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "User-Agent": USER_AGENT,
    }
    async with httpx.AsyncClient(timeout=SYNC_TIMEOUT_S) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        body = resp.json()

    raw_list: list[dict[str, Any]] = body.get("data", [])
    results: list[RawModel] = []
    for item in raw_list:
        mid: str = item.get("id", "")
        if not mid:
            continue
        display_name: str | None = item.get("display_name")
        results.append(
            RawModel(
                id=mid,
                display_name=display_name,
                is_deprecated=_is_deprecated_anthropic(item),
            )
        )
    return results


def _is_deprecated_anthropic(item: dict[str, Any]) -> bool:
    created = item.get("created_at")
    if not created:
        return False
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        now = datetime.now(datetime.UTC)
        days_old = (now - dt).days
        if days_old > 365:
            return True
    except (ValueError, TypeError):
        pass
    return False


async def _persist_models(provider: str, models: list[RawModel]) -> None:
    """Replace the 'live' source models for *provider* with the given list."""
    async with app_state.db_session_factory() as db:
        from sqlalchemy import delete

        await db.execute(
            delete(AiCatalogModel).where(
                AiCatalogModel.provider == provider,
                AiCatalogModel.source == "live",
            )
        )

        for rm in models:
            meta = enrich_model(rm.id, provider)
            db.add(
                AiCatalogModel(
                    provider=provider,
                    model_id=rm.id,
                    display_name=rm.display_name or meta.get("display_name"),
                    context_window=meta.get("context_window"),
                    capabilities=meta.get("capabilities"),
                    is_deprecated=rm.is_deprecated,
                    source="live",
                    is_enabled=not rm.is_deprecated,
                )
            )

        await db.commit()


async def find_key_for_provider(provider: str) -> dict | None:
    """Find any active key for *provider* across all users/projects."""
    from sqlalchemy import select
    from sqlalchemy.orm import load_only

    from app.models.conversation import AIProviderKey
    from app.utils.encryption import decrypt_str

    async with app_state.db_session_factory() as db:
        row = (
            await db.execute(
                select(AIProviderKey)
                .where(
                    AIProviderKey.provider == provider,
                    AIProviderKey.is_active.is_(True),
                )
                .options(load_only(AIProviderKey.api_key_encrypted, AIProviderKey.base_url))
                .limit(1)
            )
        ).scalar_one_or_none()

        if row is None:
            return None
        return {
            "api_key": decrypt_str(row.api_key_encrypted),
            "base_url": row.base_url,
        }
