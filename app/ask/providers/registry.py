"""Resolve a Provider instance from a provider name + api key."""

from __future__ import annotations

from app.ask.providers.anthropic import AnthropicProvider
from app.ask.providers.base import Provider
from app.ask.providers.openai import OpenAIProvider

SUPPORTED_PROVIDERS = ("anthropic", "openai")

_DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o",
}


def make_provider(name: str, api_key: str) -> Provider:
    if name == "anthropic":
        return AnthropicProvider(api_key=api_key)
    if name == "openai":
        return OpenAIProvider(api_key=api_key)
    raise ValueError(f"Unsupported provider: {name!r}")


def default_model_for(name: str) -> str:
    try:
        return _DEFAULT_MODELS[name]
    except KeyError:
        raise ValueError(f"Unsupported provider: {name!r}") from None
