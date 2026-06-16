import pytest

from app.ask.providers.anthropic import AnthropicProvider
from app.ask.providers.openai import OpenAIProvider
from app.ask.providers.registry import SUPPORTED_PROVIDERS, default_model_for, make_provider


def test_make_provider_dispatch():
    assert isinstance(make_provider("anthropic", "sk"), AnthropicProvider)
    assert isinstance(make_provider("openai", "sk"), OpenAIProvider)


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        make_provider("gemini", "sk")


def test_default_models():
    assert default_model_for("anthropic") == "claude-opus-4-8"
    assert default_model_for("openai") == "gpt-4o"
    assert set(SUPPORTED_PROVIDERS) == {"anthropic", "openai"}
