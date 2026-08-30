"""Public vendor model fetcher helper.

Fetches live models, context lengths, and capability tags directly from public
documentation, open registries (OpenRouter, Ollama), and vendor developer APIs
without requiring authenticated user keys.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.ask.model_metadata import (
    CAP_AGENTIC,
    CAP_CHEAP,
    CAP_CODE,
    CAP_FAST,
    CAP_REASONING,
    CAP_VISION,
    enrich_model,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15.0
USER_AGENT = "Fluxito/1.0 (ModelCatalogFetcher; +https://fluxito.app)"

# OpenRouter provider prefix mapping to Fluxito provider keys
_OPENROUTER_PROVIDER_MAP = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "gemini",
    "x-ai": "grok",
    "mistralai": "mistral",
    "deepseek": "lmstudio",
    "meta-llama": "lmstudio",
    "qwen": "lmstudio",
}


@dataclass
class ScrapedModel:
    provider: str
    model_id: str
    display_name: str | None = None
    context_window: int | None = None
    capabilities: list[str] = field(default_factory=list)
    is_recommended: bool = False
    description: str | None = None


def _infer_capabilities(model_id: str, description: str = "") -> list[str]:
    """Infer capability tags from model identifier and description."""
    meta = enrich_model(model_id, "")
    caps = set(meta.get("capabilities") or [])
    lower = f"{model_id} {description}".lower()

    if any(k in lower for k in ("reason", "thinking", "r1", "o1", "o3", "opus", "sonnet-3.7", "3-7")):
        caps.add(CAP_REASONING)
    if any(k in lower for k in ("vision", "multimodal", "image", "pixtral", "gemini")):
        caps.add(CAP_VISION)
    if any(k in lower for k in ("code", "coder", "codestral", "claude", "qwen2.5-coder")):
        caps.add(CAP_CODE)
    if any(k in lower for k in ("fast", "flash", "haiku", "mini", "small", "nano", "lite", "turbo")):
        caps.add(CAP_FAST)
    if any(k in lower for k in ("cheap", "flash", "mini", "nano", "haiku", "lite")):
        caps.add(CAP_CHEAP)
    if any(k in lower for k in ("agent", "tool", "sonnet", "gpt-4", "pro")):
        caps.add(CAP_AGENTIC)

    return sorted(caps)


async def fetch_openrouter_public_catalog() -> dict[str, list[ScrapedModel]]:
    """Query OpenRouter's public model catalog endpoint (unauthenticated).

    Returns a mapping of {fluxito_provider: [ScrapedModel, ...]}.
    """
    url = "https://openrouter.ai/api/v1/models"
    headers = {"User-Agent": USER_AGENT}
    categorized: dict[str, list[ScrapedModel]] = {
        "anthropic": [],
        "openai": [],
        "gemini": [],
        "grok": [],
        "mistral": [],
        "lmstudio": [],
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning("OpenRouter public API returned status %s", resp.status_code)
                return categorized
            data = resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch OpenRouter public catalog: %s", exc)
        return categorized

    models_raw: list[dict[str, Any]] = data.get("data", [])
    for item in models_raw:
        full_id = item.get("id", "")
        if "/" not in full_id:
            continue
        vendor_prefix, raw_model_name = full_id.split("/", 1)
        provider_key = _OPENROUTER_PROVIDER_MAP.get(vendor_prefix.lower())
        if not provider_key:
            continue

        ctx = item.get("context_length")
        name = item.get("name") or raw_model_name
        desc = item.get("description") or ""
        caps = _infer_capabilities(raw_model_name, desc)

        # Standardize model ID to vendor native format
        model_id = raw_model_name
        if provider_key == "anthropic" and not model_id.startswith("claude-"):
            model_id = f"claude-{model_id}"

        categorized[provider_key].append(
            ScrapedModel(
                provider=provider_key,
                model_id=model_id,
                display_name=name,
                context_window=ctx if isinstance(ctx, int) else None,
                capabilities=caps,
                description=desc[:160] if desc else None,
            )
        )

    return categorized


async def fetch_ollama_public_models() -> list[ScrapedModel]:
    """Discover popular open-weight models suitable for LM Studio / Ollama."""
    popular_tags = [
        ("qwen2.5-coder-32b-instruct", "Qwen 2.5 Coder 32B", 131_072, [CAP_CODE, CAP_REASONING, CAP_FAST]),
        ("llama-3.3-70b-instruct", "Llama 3.3 70B", 128_000, [CAP_REASONING, CAP_CODE, CAP_AGENTIC]),
        ("deepseek-r1-distill-qwen-32b", "DeepSeek R1 Distill Qwen 32B", 65_536, [CAP_REASONING, CAP_CODE]),
        ("deepseek-r1-distill-llama-70b", "DeepSeek R1 Distill Llama 70B", 65_536, [CAP_REASONING, CAP_CODE]),
        ("deepseek-reasoner", "DeepSeek-R1 Full", 64_000, [CAP_REASONING, CAP_CODE, CAP_CHEAP]),
        ("deepseek-chat", "DeepSeek-V3", 64_000, [CAP_CODE, CAP_FAST, CAP_CHEAP]),
        ("phi-4", "Microsoft Phi-4", 16_384, [CAP_REASONING, CAP_FAST, CAP_CHEAP]),
    ]
    return [
        ScrapedModel(
            provider="lmstudio",
            model_id=mid,
            display_name=name,
            context_window=ctx,
            capabilities=caps,
        )
        for mid, name, ctx, caps in popular_tags
    ]


async def fetch_public_models_for_provider(provider: str) -> list[ScrapedModel]:
    """Fetch public models for a specific provider.

    Combines OpenRouter live catalog data with local curated fallbacks.
    """
    if provider == "lmstudio":
        return await fetch_ollama_public_models()

    catalog_map = await fetch_openrouter_public_catalog()
    models = catalog_map.get(provider, [])

    # If OpenRouter returns models for this provider, return them deduplicated
    if models:
        seen = set()
        deduped = []
        for m in models:
            if m.model_id not in seen:
                seen.add(m.model_id)
                deduped.append(m)
        return deduped

    # Fallback to local enriched definitions
    from app.ask.model_catalog import _BUILTIN_MODELS

    fallback_ids = _BUILTIN_MODELS.get(provider, [])
    res = []
    for mid in fallback_ids:
        meta = enrich_model(mid, provider)
        res.append(
            ScrapedModel(
                provider=provider,
                model_id=mid,
                display_name=str(meta.get("display_name") or mid),
                context_window=meta.get("context_window"),  # type: ignore[arg-type]
                capabilities=meta.get("capabilities") or [],  # type: ignore[arg-type]
            )
        )
    return res


async def fetch_all_public_vendor_models() -> dict[str, list[ScrapedModel]]:
    """Fetch live public models across all supported providers concurrently."""
    from app.ask.providers.registry import SUPPORTED_PROVIDERS

    # Try OpenRouter aggregated catalog first
    openrouter_models = await fetch_openrouter_public_catalog()
    local_models = await fetch_ollama_public_models()

    results: dict[str, list[ScrapedModel]] = {}

    for prov in SUPPORTED_PROVIDERS:
        if prov == "lmstudio":
            results[prov] = local_models
            continue

        prov_models = openrouter_models.get(prov, [])
        if prov_models:
            seen = set()
            deduped = []
            for m in prov_models:
                if m.model_id not in seen:
                    seen.add(m.model_id)
                    deduped.append(m)
            results[prov] = deduped
        else:
            # Fallback to provider-specific fetcher
            results[prov] = await fetch_public_models_for_provider(prov)

    return results
