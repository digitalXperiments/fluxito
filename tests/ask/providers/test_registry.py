import pytest

from app.ask.providers.anthropic import AnthropicProvider
from app.ask.providers.openai import OpenAIProvider
from app.ask.providers.registry import (
    SUPPORTED_PROVIDERS,
    default_base_url,
    default_model_for,
    make_provider,
    provider_needs_base_url,
)


def test_supported_providers_set():
    assert set(SUPPORTED_PROVIDERS) == {"anthropic", "openai", "grok", "gemini", "mistral", "lmstudio"}


def test_make_provider_dispatch_anthropic():
    assert isinstance(make_provider("anthropic", "sk"), AnthropicProvider)


def test_make_provider_dispatch_openai_compat():
    assert isinstance(make_provider("openai", "sk"), OpenAIProvider)
    assert isinstance(make_provider("grok", "sk"), OpenAIProvider)
    assert isinstance(make_provider("gemini", "sk"), OpenAIProvider)
    assert isinstance(make_provider("mistral", "sk"), OpenAIProvider)
    assert isinstance(make_provider("lmstudio", ""), OpenAIProvider)


def test_make_provider_base_url_override():
    p = make_provider("lmstudio", "", base_url="http://myserver:5000/v1")
    assert isinstance(p, OpenAIProvider)
    assert p._base_url == "http://myserver:5000/v1"


def test_make_provider_registry_base_url_used_when_no_override():
    p = make_provider("grok", "sk")
    assert isinstance(p, OpenAIProvider)
    assert p._base_url == "https://api.x.ai/v1"


def test_make_provider_send_usage_flags():
    openai_p = make_provider("openai", "sk")
    assert openai_p._send_usage is True  # type: ignore[attr-defined]
    grok_p = make_provider("grok", "sk")
    assert grok_p._send_usage is False  # type: ignore[attr-defined]


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        make_provider("unknown-provider", "sk")


def test_default_models():
    assert default_model_for("anthropic") == "claude-3-7-sonnet-latest"
    assert default_model_for("openai") == "gpt-4o"
    assert default_model_for("grok") == "grok-3"
    assert default_model_for("gemini") == "gemini-2.5-flash"
    assert default_model_for("mistral") == "mistral-large-latest"


def test_default_model_unknown_raises():
    with pytest.raises(ValueError):
        default_model_for("unknown-provider")


def test_provider_needs_base_url():
    assert provider_needs_base_url("lmstudio") is True
    assert provider_needs_base_url("openai") is False
    assert provider_needs_base_url("anthropic") is False


def test_default_base_url():
    assert default_base_url("openai") == "https://api.openai.com/v1"
    assert default_base_url("grok") == "https://api.x.ai/v1"
    assert default_base_url("lmstudio") == "http://localhost:1234/v1"
    assert default_base_url("anthropic") is None
