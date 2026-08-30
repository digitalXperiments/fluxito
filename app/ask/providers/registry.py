"""Resolve a Provider instance from a provider name + api key."""

from __future__ import annotations

from app.ask.providers.anthropic import AnthropicProvider
from app.ask.providers.base import Provider
from app.ask.providers.openai import OpenAIProvider

# OpenAI-compatible providers: base_url, default_model, and whether the server
# accepts stream_options.include_usage (some reject unknown params).
_OPENAI_COMPAT: dict[str, dict[str, object]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "send_usage": True,
        # OpenAI's newer models (gpt-5/o-series) reject max_tokens.
        "token_param": "max_completion_tokens",
    },
    "grok": {
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-3",
        "send_usage": False,
        "token_param": "max_tokens",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
        "send_usage": False,
        "token_param": "max_tokens",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.5-flash",
        "send_usage": False,
        "token_param": "max_tokens",
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "default_model": "",
        "send_usage": False,
        "token_param": "max_tokens",
    },
}

SUPPORTED_PROVIDERS = ("anthropic", "openai", "grok", "gemini", "mistral", "lmstudio")

# Public alias for the sync service and other consumers.
OPENAI_COMPAT: dict[str, dict[str, object]] = _OPENAI_COMPAT

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-3-7-sonnet-latest",
    **{k: str(v["default_model"]) for k, v in _OPENAI_COMPAT.items()},
}


def make_provider(name: str, api_key: str, base_url: str | None = None) -> Provider:
    """Return an instantiated provider for *name*.

    *base_url* overrides the registry default (useful for lmstudio / custom
    self-hosted endpoints stored per key in the database).
    """
    if name == "anthropic":
        return AnthropicProvider(api_key=api_key)
    if name in _OPENAI_COMPAT:
        cfg = _OPENAI_COMPAT[name]
        effective_base_url = base_url or str(cfg["base_url"])
        return OpenAIProvider(
            api_key,
            base_url=effective_base_url,
            send_usage=bool(cfg["send_usage"]),
            token_param=str(cfg.get("token_param", "max_tokens")),
        )
    raise ValueError(f"Unsupported provider: {name!r}")


def default_model_for(name: str) -> str:
    try:
        return _DEFAULT_MODELS[name]
    except KeyError:
        raise ValueError(f"Unsupported provider: {name!r}") from None


def provider_needs_base_url(name: str) -> bool:
    """Return True for providers that require a user-supplied base URL (LM Studio)."""
    return name == "lmstudio"


def default_base_url(name: str) -> str | None:
    """Return the default base URL for an OpenAI-compatible provider, or None."""
    cfg = _OPENAI_COMPAT.get(name)
    if cfg is None:
        return None
    return str(cfg["base_url"])
