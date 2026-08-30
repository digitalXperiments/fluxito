"""Curated metadata for known AI models — display names, capabilities, context windows.

This supplements live vendor API data (which often returns just a model ID)
with human-friendly names and structured capability tags. The mapping is
versioned alongside the code as a fallback; live-synced data takes priority.
"""

from __future__ import annotations

CAP_REASONING = "reasoning"
CAP_VISION = "vision"
CAP_CODE = "code"
CAP_FAST = "fast"
CAP_CHEAP = "cheap"
CAP_AGENTIC = "agentic"

ModelMeta = dict[str, object]

_MODEL_METADATA: dict[str, ModelMeta] = {
    # ── Anthropic ──────────────────────────────────────────────────────────
    "claude-opus-5": {
        "display_name": "Claude Opus 5",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_AGENTIC, CAP_VISION],
        "context_window": 1_000_000,
    },
    "claude-opus-5-fast": {
        "display_name": "Claude Opus 5 (Fast)",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_FAST, CAP_VISION],
        "context_window": 1_000_000,
    },
    "claude-sonnet-5": {
        "display_name": "Claude Sonnet 5",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_AGENTIC, CAP_VISION],
        "context_window": 1_000_000,
    },
    "claude-fable-5": {
        "display_name": "Claude Fable 5",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 1_000_000,
    },
    "claude-opus-4.8": {
        "display_name": "Claude Opus 4.8",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION, CAP_AGENTIC],
        "context_window": 1_000_000,
    },
    "claude-opus-4.8-fast": {
        "display_name": "Claude Opus 4.8 (Fast)",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_FAST, CAP_VISION],
        "context_window": 1_000_000,
    },
    "claude-opus-4.7": {
        "display_name": "Claude Opus 4.7",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_AGENTIC],
        "context_window": 1_000_000,
    },
    "claude-sonnet-4.6": {
        "display_name": "Claude Sonnet 4.6",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION, CAP_FAST],
        "context_window": 1_000_000,
    },
    "claude-haiku-4.5": {
        "display_name": "Claude Haiku 4.5",
        "capabilities": [CAP_CODE, CAP_FAST, CAP_VISION, CAP_CHEAP],
        "context_window": 200_000,
    },
    "claude-opus-4.5": {
        "display_name": "Claude Opus 4.5",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 200_000,
    },
    "claude-sonnet-4.5": {
        "display_name": "Claude Sonnet 4.5",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_AGENTIC],
        "context_window": 1_000_000,
    },
    "claude-sonnet-4": {
        "display_name": "Claude Sonnet 4",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 1_000_000,
    },
    "claude-3-7-sonnet-latest": {
        "display_name": "Claude 3.7 Sonnet",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION, CAP_AGENTIC],
        "context_window": 200_000,
    },
    "claude-3-5-sonnet-latest": {
        "display_name": "Claude 3.5 Sonnet",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 200_000,
    },
    "claude-3-5-haiku-latest": {
        "display_name": "Claude 3.5 Haiku",
        "capabilities": [CAP_FAST, CAP_CODE, CAP_VISION, CAP_CHEAP],
        "context_window": 200_000,
    },
    "claude-3-opus-latest": {
        "display_name": "Claude 3 Opus",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 200_000,
    },
    # ── OpenAI ─────────────────────────────────────────────────────────────
    "gpt-5.6-sol-pro": {
        "display_name": "GPT-5.6 Sol Pro",
        "capabilities": [CAP_REASONING, CAP_AGENTIC, CAP_CODE, CAP_VISION],
        "context_window": 1_050_000,
    },
    "gpt-5.6-sol": {
        "display_name": "GPT-5.6 Sol",
        "capabilities": [CAP_REASONING, CAP_AGENTIC, CAP_CODE],
        "context_window": 1_050_000,
    },
    "gpt-5.6-terra-pro": {
        "display_name": "GPT-5.6 Terra Pro",
        "capabilities": [CAP_REASONING, CAP_AGENTIC, CAP_CODE, CAP_VISION],
        "context_window": 1_050_000,
    },
    "gpt-5.6-terra": {
        "display_name": "GPT-5.6 Terra",
        "capabilities": [CAP_REASONING, CAP_AGENTIC],
        "context_window": 1_050_000,
    },
    "gpt-5.6-luna-pro": {
        "display_name": "GPT-5.6 Luna Pro",
        "capabilities": [CAP_REASONING, CAP_AGENTIC, CAP_CODE, CAP_VISION],
        "context_window": 1_050_000,
    },
    "gpt-5.6-luna": {
        "display_name": "GPT-5.6 Luna",
        "capabilities": [CAP_REASONING, CAP_FAST, CAP_AGENTIC],
        "context_window": 1_050_000,
    },
    "gpt-5.5-pro": {
        "display_name": "GPT-5.5 Pro",
        "capabilities": [CAP_REASONING, CAP_AGENTIC],
        "context_window": 1_050_000,
    },
    "gpt-5.5": {
        "display_name": "GPT-5.5",
        "capabilities": [CAP_REASONING, CAP_AGENTIC],
        "context_window": 1_050_000,
    },
    "gpt-5.4-mini": {
        "display_name": "GPT-5.4 Mini",
        "capabilities": [CAP_FAST, CAP_CHEAP, CAP_REASONING, CAP_VISION],
        "context_window": 400_000,
    },
    "gpt-5.4-nano": {
        "display_name": "GPT-5.4 Nano",
        "capabilities": [CAP_FAST, CAP_CHEAP, CAP_VISION],
        "context_window": 400_000,
    },
    "gpt-5.3-codex": {
        "display_name": "GPT-5.3 Codex",
        "capabilities": [CAP_CODE, CAP_REASONING, CAP_AGENTIC],
        "context_window": 400_000,
    },
    "o3-pro": {
        "display_name": "o3 Pro",
        "capabilities": [CAP_REASONING, CAP_AGENTIC],
        "context_window": 200_000,
    },
    "o4-mini": {
        "display_name": "o4 Mini",
        "capabilities": [CAP_REASONING, CAP_FAST, CAP_CHEAP, CAP_CODE, CAP_VISION],
        "context_window": 200_000,
    },
    "gpt-4.1": {
        "display_name": "GPT-4.1",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION, CAP_AGENTIC],
        "context_window": 1_047_576,
    },
    "gpt-4.5-preview": {
        "display_name": "GPT-4.5 Preview",
        "capabilities": [CAP_REASONING, CAP_VISION, CAP_AGENTIC],
        "context_window": 128_000,
    },
    "gpt-4.5-preview-2025-02-27": {
        "display_name": "GPT-4.5 Preview (2025-02-27)",
        "capabilities": [CAP_REASONING, CAP_VISION, CAP_AGENTIC],
        "context_window": 128_000,
    },
    "o3-mini": {
        "display_name": "o3-mini",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_FAST, CAP_CHEAP],
        "context_window": 200_000,
    },
    "o3-mini-2025-01-31": {
        "display_name": "o3-mini (2025-01-31)",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_FAST, CAP_CHEAP],
        "context_window": 200_000,
    },
    "o1": {
        "display_name": "o1",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 200_000,
    },
    "o1-2024-12-17": {
        "display_name": "o1 (2024-12-17)",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 200_000,
    },
    "o1-mini": {
        "display_name": "o1-mini",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_FAST, CAP_CHEAP],
        "context_window": 128_000,
    },
    "gpt-4o": {
        "display_name": "GPT-4o",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION, CAP_FAST],
        "context_window": 128_000,
    },
    "gpt-4o-2024-11-20": {
        "display_name": "GPT-4o (2024-11-20)",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION, CAP_FAST],
        "context_window": 128_000,
    },
    "chatgpt-4o-latest": {
        "display_name": "ChatGPT-4o Dynamic",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 128_000,
    },
    "gpt-4o-mini": {
        "display_name": "GPT-4o mini",
        "capabilities": [CAP_CODE, CAP_VISION, CAP_FAST, CAP_CHEAP],
        "context_window": 128_000,
    },
    "gpt-4o-mini-2024-07-18": {
        "display_name": "GPT-4o mini (2024-07-18)",
        "capabilities": [CAP_CODE, CAP_VISION, CAP_FAST, CAP_CHEAP],
        "context_window": 128_000,
    },
    "gpt-4.1-mini": {
        "display_name": "GPT-4.1 mini",
        "capabilities": [CAP_CODE, CAP_FAST, CAP_CHEAP],
        "context_window": 1_047_576,
    },
    "gpt-4.1-nano": {
        "display_name": "GPT-4.1 nano",
        "capabilities": [CAP_FAST, CAP_CHEAP],
        "context_window": 1_047_576,
    },
    "gpt-4-turbo": {
        "display_name": "GPT-4 Turbo",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 128_000,
    },
    "gpt-4": {
        "display_name": "GPT-4",
        "capabilities": [CAP_REASONING, CAP_CODE],
        "context_window": 8_192,
    },
    "gpt-3.5-turbo": {
        "display_name": "GPT-3.5 Turbo",
        "capabilities": [CAP_FAST, CAP_CHEAP],
        "context_window": 16_385,
    },
    # ── xAI Grok ───────────────────────────────────────────────────────────
    "grok-4.6": {
        "display_name": "Grok 4.6",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_AGENTIC],
        "context_window": 500_000,
    },
    "grok-4.5": {
        "display_name": "Grok 4.5",
        "capabilities": [CAP_REASONING, CAP_AGENTIC],
        "context_window": 500_000,
    },
    "grok-4.3": {
        "display_name": "Grok 4.3",
        "capabilities": [CAP_REASONING, CAP_VISION, CAP_AGENTIC],
        "context_window": 1_000_000,
    },
    "grok-4.20": {
        "display_name": "Grok 4.20",
        "capabilities": [CAP_REASONING, CAP_AGENTIC],
        "context_window": 2_000_000,
    },
    "grok-3": {
        "display_name": "Grok 3",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION, CAP_AGENTIC],
        "context_window": 131_072,
    },
    "grok-3-mini": {
        "display_name": "Grok 3 mini",
        "capabilities": [CAP_FAST, CAP_CHEAP, CAP_REASONING],
        "context_window": 131_072,
    },
    # ── Google Gemini ──────────────────────────────────────────────────────
    "gemini-3.7-flash": {
        "display_name": "Gemini 3.7 Flash",
        "capabilities": [CAP_REASONING, CAP_VISION, CAP_FAST, CAP_CHEAP, CAP_AGENTIC],
        "context_window": 1_048_576,
    },
    "gemini-3.6-flash": {
        "display_name": "Gemini 3.6 Flash",
        "capabilities": [CAP_VISION, CAP_FAST, CAP_CHEAP, CAP_AGENTIC],
        "context_window": 1_048_576,
    },
    "gemini-3.5-flash": {
        "display_name": "Gemini 3.5 Flash",
        "capabilities": [CAP_REASONING, CAP_VISION, CAP_FAST, CAP_CHEAP, CAP_AGENTIC],
        "context_window": 1_048_576,
    },
    "gemini-3.5-flash-lite": {
        "display_name": "Gemini 3.5 Flash Lite",
        "capabilities": [CAP_VISION, CAP_FAST, CAP_CHEAP],
        "context_window": 1_048_576,
    },
    "gemini-3.1-pro-preview": {
        "display_name": "Gemini 3.1 Pro Preview",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION, CAP_AGENTIC],
        "context_window": 1_048_576,
    },
    "gemini-2.5-pro": {
        "display_name": "Gemini 2.5 Pro",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION, CAP_AGENTIC],
        "context_window": 1_048_576,
    },
    "gemini-2.5-flash": {
        "display_name": "Gemini 2.5 Flash",
        "capabilities": [CAP_VISION, CAP_CODE, CAP_FAST, CAP_CHEAP, CAP_REASONING],
        "context_window": 1_048_576,
    },
    # ── Mistral ────────────────────────────────────────────────────────────
    "mistral-medium-3-5": {
        "display_name": "Mistral Medium 3.5",
        "capabilities": [CAP_REASONING, CAP_VISION, CAP_AGENTIC],
        "context_window": 262_144,
    },
    "mistral-large-2512": {
        "display_name": "Mistral Large 3 (2512)",
        "capabilities": [CAP_REASONING, CAP_CODE],
        "context_window": 262_144,
    },
    "mistral-small-2603": {
        "display_name": "Mistral Small 4 (2603)",
        "capabilities": [CAP_FAST, CAP_CHEAP, CAP_REASONING],
        "context_window": 262_144,
    },
    "devstral-2512": {
        "display_name": "Devstral 2 (2512)",
        "capabilities": [CAP_CODE, CAP_AGENTIC],
        "context_window": 262_144,
    },
    "ministral-14b-2512": {
        "display_name": "Ministral 3 14B (2512)",
        "capabilities": [CAP_FAST, CAP_CHEAP],
        "context_window": 262_144,
    },
    "ministral-8b-2512": {
        "display_name": "Ministral 3 8B (2512)",
        "capabilities": [CAP_FAST, CAP_CHEAP, CAP_VISION],
        "context_window": 262_144,
    },
    "codestral-2508": {
        "display_name": "Codestral 2508",
        "capabilities": [CAP_CODE],
        "context_window": 256_000,
    },
    "gemini-2.0-flash": {
        "display_name": "Gemini 2.0 Flash",
        "capabilities": [CAP_VISION, CAP_FAST, CAP_CHEAP, CAP_CODE],
        "context_window": 1_048_576,
    },
    "gemini-2.0-flash-lite": {
        "display_name": "Gemini 2.0 Flash Lite",
        "capabilities": [CAP_FAST, CAP_CHEAP],
        "context_window": 1_048_576,
    },
    "gemini-2.0-pro-exp-02-05": {
        "display_name": "Gemini 2.0 Pro Experimental",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION, CAP_AGENTIC],
        "context_window": 2_097_152,
    },
    "gemini-1.5-pro": {
        "display_name": "Gemini 1.5 Pro",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 2_097_152,
    },
    "gemini-1.5-flash": {
        "display_name": "Gemini 1.5 Flash",
        "capabilities": [CAP_VISION, CAP_FAST, CAP_CHEAP],
        "context_window": 1_048_576,
    },
    # ── Mistral ────────────────────────────────────────────────────────────
    "mistral-large-latest": {
        "display_name": "Mistral Large",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 128_000,
    },
    "mistral-large-2501": {
        "display_name": "Mistral Large 2501",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_VISION],
        "context_window": 128_000,
    },
    "mistral-small-latest": {
        "display_name": "Mistral Small",
        "capabilities": [CAP_FAST, CAP_CHEAP],
        "context_window": 32_000,
    },
    "mistral-small-2501": {
        "display_name": "Mistral Small 2501",
        "capabilities": [CAP_FAST, CAP_CHEAP],
        "context_window": 32_000,
    },
    "codestral-latest": {
        "display_name": "Codestral",
        "capabilities": [CAP_CODE],
        "context_window": 256_000,
    },
    "codestral-2501": {
        "display_name": "Codestral 2501",
        "capabilities": [CAP_CODE],
        "context_window": 256_000,
    },
    "pixtral-large-latest": {
        "display_name": "Pixtral Large",
        "capabilities": [CAP_VISION, CAP_REASONING, CAP_CODE],
        "context_window": 128_000,
    },
    "pixtral-12b": {
        "display_name": "Pixtral 12B",
        "capabilities": [CAP_VISION, CAP_FAST, CAP_CHEAP],
        "context_window": 128_000,
    },
    "ministral-8b-latest": {
        "display_name": "Ministral 8B",
        "capabilities": [CAP_FAST, CAP_CHEAP],
        "context_window": 128_000,
    },
    "ministral-3b-latest": {
        "display_name": "Ministral 3B",
        "capabilities": [CAP_FAST, CAP_CHEAP],
        "context_window": 128_000,
    },
    "mistral-moderation-latest": {
        "display_name": "Mistral Moderation",
        "capabilities": [],
        "context_window": 32_000,
    },
    "mistral-embed": {
        "display_name": "Mistral Embed",
        "capabilities": [],
        "context_window": 8_192,
    },
    # ── LM Studio / Local & Open Weights ──────────────────────────────────
    "qwen2.5-coder-32b-instruct": {
        "display_name": "Qwen 2.5 Coder 32B",
        "capabilities": [CAP_CODE, CAP_REASONING],
        "context_window": 131_072,
    },
    "llama-3.3-70b-instruct": {
        "display_name": "Llama 3.3 70B Instruct",
        "capabilities": [CAP_REASONING, CAP_CODE],
        "context_window": 128_000,
    },
    "deepseek-r1-distill-qwen-32b": {
        "display_name": "DeepSeek-R1 Distill Qwen 32B",
        "capabilities": [CAP_REASONING, CAP_CODE],
        "context_window": 65_536,
    },
    "deepseek-r1-distill-llama-70b": {
        "display_name": "DeepSeek-R1 Distill Llama 70B",
        "capabilities": [CAP_REASONING, CAP_CODE],
        "context_window": 65_536,
    },
    "deepseek-reasoner": {
        "display_name": "DeepSeek-R1",
        "capabilities": [CAP_REASONING, CAP_CODE, CAP_CHEAP],
        "context_window": 64_000,
    },
    "deepseek-chat": {
        "display_name": "DeepSeek-V3",
        "capabilities": [CAP_CODE, CAP_FAST, CAP_CHEAP],
        "context_window": 64_000,
    },
    "phi-4": {
        "display_name": "Phi-4",
        "capabilities": [CAP_REASONING, CAP_FAST, CAP_CHEAP],
        "context_window": 16_384,
    },
}


def get_metadata(model_id: str) -> ModelMeta:
    """Return known metadata for *model_id*, or an empty dict."""
    return _MODEL_METADATA.get(model_id, {})


def enrich_model(model_id: str, provider: str) -> ModelMeta:
    """Build a metadata dict for a model, falling back to heuristics.

    When the model ID isn't in the curated mapping, derives a display name
    and guesses capabilities from the model name.
    """
    known = get_metadata(model_id)
    if known:
        return known

    return {
        "display_name": _derive_display_name(model_id, provider),
        "capabilities": _guess_capabilities(model_id),
        "context_window": None,
    }


def _derive_display_name(model_id: str, provider: str) -> str:
    """Turn a raw model ID into a human-readable name."""
    name = model_id.replace("-", " ").title()
    if provider == "openai":
        name = model_id.replace("gpt-", "GPT-").replace("o3", "o3").replace("o4", "o4")
        name = name.replace("-mini", " mini").replace("-turbo", " Turbo")
        name = name.replace("-nano", " nano")
    elif provider == "anthropic":
        name = model_id.replace("claude-", "Claude ").title()
        name = name.replace("Sonnet", "Sonnet").replace("Haiku", "Haiku").replace("Opus", "Opus")
    elif provider == "gemini":
        name = model_id.replace("gemini-", "Gemini ").title()
    elif provider == "grok":
        name = model_id.replace("grok-", "Grok ").title()
        name = name.replace("Vision", "Vision")
    elif provider == "mistral":
        name = model_id.replace("-", " ").title()
    return name


def _guess_capabilities(model_id: str) -> list[str]:
    """Guess capability tags from a model ID string."""
    caps: list[str] = []
    lower = model_id.lower()
    if any(t in lower for t in ("opus", "o3-", "o4-", "pro", "large", "reasoning")):
        caps.append(CAP_REASONING)
    if any(t in lower for t in ("vision", "multi", "gemini")):
        caps.append(CAP_VISION)
    if any(t in lower for t in ("code", "codestral", "claude")):
        caps.append(CAP_CODE)
    if any(t in lower for t in ("flash", "haiku", "mini", "small", "tiny", "nano", "lite")):
        caps.append(CAP_FAST)
    if any(t in lower for t in ("mini", "small", "flash", "lite", "nano", "haiku")):
        caps.append(CAP_CHEAP)
    return caps
