from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ask.public_catalog_fetcher import (
    _infer_capabilities,
    fetch_all_public_vendor_models,
    fetch_ollama_public_models,
    fetch_openrouter_public_catalog,
    fetch_public_models_for_provider,
)


def test_infer_capabilities():
    caps1 = _infer_capabilities("claude-3-7-sonnet", "hybrid reasoning and vision model")
    assert "reasoning" in caps1
    assert "vision" in caps1
    assert "code" in caps1

    caps2 = _infer_capabilities("gemini-2.5-flash", "fast lightweight cheap model")
    assert "fast" in caps2
    assert "cheap" in caps2


@pytest.mark.asyncio
async def test_fetch_ollama_public_models():
    models = await fetch_ollama_public_models()
    assert len(models) >= 5
    ids = {m.model_id for m in models}
    assert "qwen2.5-coder-32b-instruct" in ids
    assert "llama-3.3-70b-instruct" in ids
    for m in models:
        assert m.provider == "lmstudio"
        assert m.context_window is not None


@pytest.mark.asyncio
async def test_fetch_openrouter_public_catalog_parsing():
    fake_response_data = {
        "data": [
            {
                "id": "anthropic/claude-3.7-sonnet",
                "name": "Claude 3.7 Sonnet",
                "context_length": 200000,
                "description": "State of the art hybrid reasoning model",
            },
            {
                "id": "openai/gpt-4.5-preview",
                "name": "GPT-4.5 Preview",
                "context_length": 128000,
                "description": "Frontier intelligence",
            },
            {
                "id": "google/gemini-2.5-flash",
                "name": "Gemini 2.5 Flash",
                "context_length": 1000000,
                "description": "Fast multimodal model",
            },
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_response_data

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        catalog = await fetch_openrouter_public_catalog()

        assert "anthropic" in catalog
        assert "openai" in catalog
        assert "gemini" in catalog

        anthropic_models = catalog["anthropic"]
        assert len(anthropic_models) == 1
        assert anthropic_models[0].model_id == "claude-3.7-sonnet"
        assert anthropic_models[0].context_window == 200000

        openai_models = catalog["openai"]
        assert len(openai_models) == 1
        assert openai_models[0].model_id == "gpt-4.5-preview"


@pytest.mark.asyncio
async def test_fetch_public_models_for_provider_fallback():
    # If OpenRouter returns empty, fallback to local enriched models
    mock_resp = MagicMock()
    mock_resp.status_code = 500

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        models = await fetch_public_models_for_provider("anthropic")
        assert len(models) > 0
        ids = [m.model_id for m in models]
        assert "claude-3-7-sonnet-latest" in ids


@pytest.mark.asyncio
async def test_fetch_all_public_vendor_models():
    catalog = await fetch_all_public_vendor_models()
    for prov in ("anthropic", "openai", "grok", "gemini", "mistral", "lmstudio"):
        assert prov in catalog
        assert len(catalog[prov]) > 0
