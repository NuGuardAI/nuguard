"""AI model knowledge base: provider families, version patterns, and documentation URLs.

This module is the single source of truth for known model metadata within Xelo.
All framework adapters import from here to ensure consistent provider/version
attribution.
"""

from __future__ import annotations

import re as _re
from typing import Any

# ---------------------------------------------------------------------------
# Model family registry
# ---------------------------------------------------------------------------
# Key: canonical model family prefix (lowercase, as it appears in model names)
# Value: provider, base version string, family label

MODEL_FAMILIES: dict[str, dict[str, str]] = {
    # OpenAI GPT models
    "gpt-5-turbo": {"provider": "openai", "base_version": "5-turbo", "family": "gpt"},
    "gpt-5-mini": {"provider": "openai", "base_version": "5-mini", "family": "gpt"},
    "gpt-5": {"provider": "openai", "base_version": "5", "family": "gpt"},
    "gpt-4o": {"provider": "openai", "base_version": "4o", "family": "gpt"},
    "gpt-4-turbo": {"provider": "openai", "base_version": "4-turbo", "family": "gpt"},
    "gpt-4": {"provider": "openai", "base_version": "4", "family": "gpt"},
    "gpt-3.5-turbo": {"provider": "openai", "base_version": "3.5-turbo", "family": "gpt"},
    # OpenAI o-series reasoning models
    "o4-mini": {"provider": "openai", "base_version": "4-mini", "family": "o4"},
    "o4": {"provider": "openai", "base_version": "4", "family": "o4"},
    "o3-mini": {"provider": "openai", "base_version": "3-mini", "family": "o3"},
    "o3": {"provider": "openai", "base_version": "3", "family": "o3"},
    "o1-mini": {"provider": "openai", "base_version": "1-mini", "family": "o1"},
    "o1-preview": {"provider": "openai", "base_version": "1-preview", "family": "o1"},
    "o1": {"provider": "openai", "base_version": "1", "family": "o1"},
    # Anthropic Claude models (hyphenated date-suffix variants listed first for longest-match)
    "claude-4-opus": {"provider": "anthropic", "base_version": "4-opus", "family": "claude"},
    "claude-4-sonnet": {"provider": "anthropic", "base_version": "4-sonnet", "family": "claude"},
    "claude-4-haiku": {"provider": "anthropic", "base_version": "4-haiku", "family": "claude"},
    "claude-4": {"provider": "anthropic", "base_version": "4", "family": "claude"},
    "claude-3-7-sonnet": {
        "provider": "anthropic",
        "base_version": "3.7-sonnet",
        "family": "claude",
    },
    "claude-3.7-sonnet": {
        "provider": "anthropic",
        "base_version": "3.7-sonnet",
        "family": "claude",
    },
    "claude-3-5-sonnet": {
        "provider": "anthropic",
        "base_version": "3.5-sonnet",
        "family": "claude",
    },
    "claude-3-5-haiku": {"provider": "anthropic", "base_version": "3.5-haiku", "family": "claude"},
    "claude-3.5-sonnet": {
        "provider": "anthropic",
        "base_version": "3.5-sonnet",
        "family": "claude",
    },
    "claude-3.5-haiku": {"provider": "anthropic", "base_version": "3.5-haiku", "family": "claude"},
    "claude-3-opus": {"provider": "anthropic", "base_version": "3-opus", "family": "claude"},
    "claude-3-sonnet": {"provider": "anthropic", "base_version": "3-sonnet", "family": "claude"},
    "claude-3-haiku": {"provider": "anthropic", "base_version": "3-haiku", "family": "claude"},
    # Google Gemini models
    "gemini-3.5-pro": {"provider": "google", "base_version": "3.5-pro", "family": "gemini"},
    "gemini-3.5-flash": {"provider": "google", "base_version": "3.5-flash", "family": "gemini"},
    "gemini-3.0-pro": {"provider": "google", "base_version": "3.0-pro", "family": "gemini"},
    "gemini-3.0-flash": {"provider": "google", "base_version": "3.0-flash", "family": "gemini"},
    "gemini-3": {"provider": "google", "base_version": "3", "family": "gemini"},
    "gemini-2.5-pro": {"provider": "google", "base_version": "2.5-pro", "family": "gemini"},
    "gemini-2.5-flash": {"provider": "google", "base_version": "2.5-flash", "family": "gemini"},
    "gemini-2.0-flash": {"provider": "google", "base_version": "2.0-flash", "family": "gemini"},
    "gemini-1.5-pro": {"provider": "google", "base_version": "1.5-pro", "family": "gemini"},
    "gemini-1.5-flash": {"provider": "google", "base_version": "1.5-flash", "family": "gemini"},
    # Mistral models
    "mistral-large": {"provider": "mistral", "base_version": "large", "family": "mistral"},
    "mistral-small": {"provider": "mistral", "base_version": "small", "family": "mistral"},
    "mixtral-8x7b": {"provider": "mistral", "base_version": "8x7b", "family": "mixtral"},
    # Meta Llama models
    "llama-4-maverick": {"provider": "meta", "base_version": "4-maverick", "family": "llama"},
    "llama-4-scout": {"provider": "meta", "base_version": "4-scout", "family": "llama"},
    "llama-4": {"provider": "meta", "base_version": "4", "family": "llama"},
    "llama-3.3": {"provider": "meta", "base_version": "3.3", "family": "llama"},
    "llama-3.2": {"provider": "meta", "base_version": "3.2", "family": "llama"},
    "llama-3.1": {"provider": "meta", "base_version": "3.1", "family": "llama"},
    "llama-3": {"provider": "meta", "base_version": "3", "family": "llama"},
    # Cohere
    "command-r+": {"provider": "cohere", "base_version": "r+", "family": "command"},
    "command-r": {"provider": "cohere", "base_version": "r", "family": "command"},
    "command": {"provider": "cohere", "base_version": "latest", "family": "command"},
}

# ---------------------------------------------------------------------------
# Provider documentation URLs
# ---------------------------------------------------------------------------

MODEL_CARD_TEMPLATES: dict[str, str] = {
    "openai": "https://platform.openai.com/docs/models/{model_name}",
    "anthropic": "https://docs.anthropic.com/en/docs/about-claude/models",
    "google": "https://ai.google.dev/gemini-api/docs/models/{model_name}",
    "azure": "https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models",
    "huggingface": "https://huggingface.co/{model_name}",
    "mistral": "https://docs.mistral.ai/getting-started/models/",
    "cohere": "https://docs.cohere.com/docs/models",
    "meta": "https://huggingface.co/meta-llama/{model_name}",
    "groq": "https://console.groq.com/docs/models",
}

# Default API endpoint by provider (for known public endpoints)
DEFAULT_ENDPOINTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
    "mistral": "https://api.mistral.ai",
    "cohere": "https://api.cohere.com",
    "groq": "https://api.groq.com/openai/v1",
}

# ---------------------------------------------------------------------------
# LLM client patterns (by SDK/library)
# ---------------------------------------------------------------------------

LLM_CLIENT_PATTERNS: dict[str, dict[str, Any]] = {
    "openai": {
        "imports": ["openai"],
        "classes": ["OpenAI", "AsyncOpenAI", "AzureOpenAI", "AsyncAzureOpenAI"],
        "namespace": "openai",
    },
    "anthropic": {
        "imports": ["anthropic"],
        "classes": ["Anthropic", "AsyncAnthropic"],
        "namespace": "anthropic",
    },
    "google": {
        "imports": [
            "google.genai",
            "google.generativeai",
            "vertexai",
            "google.cloud.aiplatform",
            "google",
        ],
        "classes": ["Client", "GenerativeModel", "ChatModel", "TextGenerationModel"],
        "namespace": "google",
    },
    "cohere": {
        "imports": ["cohere"],
        "classes": ["Client", "AsyncClient"],
        "namespace": "cohere",
    },
    "mistral": {
        "imports": ["mistralai"],
        "classes": ["Mistral", "MistralClient"],
        "namespace": "mistral",
    },
    "groq": {
        "imports": ["groq"],
        "classes": ["Groq", "AsyncGroq"],
        "namespace": "groq",
    },
    "ollama": {
        "imports": ["ollama"],
        "classes": [],
        "namespace": "ollama",
    },
    "bedrock": {
        "imports": ["boto3", "botocore"],
        "classes": ["BedrockRuntimeClient"],
        "namespace": "bedrock",
    },
}

# Flat class → providers mapping (a class may appear in multiple providers)
_CLASS_TO_PROVIDERS: dict[str, list[str]] = {}
for _provider, _cfg in LLM_CLIENT_PATTERNS.items():
    for _cls in _cfg["classes"]:
        _CLASS_TO_PROVIDERS.setdefault(_cls, []).append(_provider)

ALL_LLM_CLASSES: list[str] = list(_CLASS_TO_PROVIDERS.keys())

# LangChain wrapper class → underlying provider
LANGCHAIN_LLM_CLASS_PROVIDERS: dict[str, str] = {
    "ChatOpenAI": "openai",
    "AzureChatOpenAI": "azure",
    "ChatAnthropic": "anthropic",
    "ChatGoogleGenerativeAI": "google",
    "ChatVertexAI": "google",
    "ChatOllama": "ollama",
    "ChatMistralAI": "mistral",
    "ChatCohere": "cohere",
    "ChatGroq": "groq",
    "ChatBedrock": "bedrock",
    "BedrockChat": "bedrock",
    # Legacy LangChain LLM classes (pre-chat API)
    "Bedrock": "bedrock",
    "BedrockLLM": "bedrock",
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def infer_provider(model_name: str) -> str:
    """Best-effort provider inference from a model name string."""
    ml = model_name.lower()
    if "gpt" in ml or _re.search(r"\bo\d\b", ml) or "davinci" in ml:
        return "openai"
    if "claude" in ml:
        return "anthropic"
    if "gemini" in ml or "palm" in ml or "bard" in ml:
        return "google"
    if "mistral" in ml or "mixtral" in ml:
        return "mistral"
    if "llama" in ml:
        return "meta"
    if "command" in ml:
        return "cohere"
    if "titan" in ml or "nova" in ml or "jurassic" in ml:
        return "bedrock"
    return "unknown"


def get_model_details(
    model_name: str, provider: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return version, api_endpoint, model_card_url, and model_family for a model."""
    args = args or {}
    details: dict[str, Any] = {
        "version": None,
        "api_endpoint": None,
        "model_card_url": None,
        "model_family": None,
    }

    if not model_name:
        return details

    ml = model_name.lower()
    # Normalize separators: treat "3-5" and "3.5" as equivalent
    ml_norm = ml.replace(".", "-")

    # Look up known families (longest prefix match wins)
    for family_key in sorted(MODEL_FAMILIES, key=len, reverse=True):
        fk_norm = family_key.replace(".", "-")
        if fk_norm in ml_norm:
            info = MODEL_FAMILIES[family_key]
            details["model_family"] = info["family"]
            base_version = info["base_version"]
            # Check for date suffix like -20241022 or -2024-04-09
            date_m = _re.search(r"-(\d{4}(?:-\d{2}-\d{2}|\d{4}))$", model_name)
            if date_m:
                details["version"] = f"{base_version}-{date_m.group(1)}"
            else:
                details["version"] = base_version
            break

    # Fallback version extraction
    if not details["version"]:
        vm = _re.search(r"(\d+(?:\.\d+)?(?:-\w+)?)", model_name)
        if vm:
            details["version"] = vm.group(1)

    # Model card URL
    normalized_provider = {"azure-openai": "azure", "langchain": "openai"}.get(provider, provider)
    template = MODEL_CARD_TEMPLATES.get(normalized_provider)
    if template:
        if "{model_name}" in template:
            if provider == "meta":
                hf_name = model_name.replace("llama", "Llama").replace("-instruct", "-Instruct")
                details["model_card_url"] = template.format(model_name=hf_name)
            else:
                details["model_card_url"] = template.format(model_name=model_name.lower())
        else:
            details["model_card_url"] = template

    # API endpoint
    for param in ("base_url", "azure_endpoint", "api_endpoint", "endpoint", "api_base"):
        if param in args:
            details["api_endpoint"] = str(args[param]).strip("'\"")
            break
    if not details["api_endpoint"] and provider != "azure":
        details["api_endpoint"] = DEFAULT_ENDPOINTS.get(provider)

    return details
