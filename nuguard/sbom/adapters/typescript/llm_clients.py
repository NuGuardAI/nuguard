"""Common LLM Clients TypeScript Adapter for Xelo SBOM.

Parsing is performed by ``xelo.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).

Supports:
- OpenAI SDK (openai) — including the ``new OpenAI({ baseURL: "..." })``
  proxy pattern used to access Groq, Gemini, Ollama, and similar providers
  through the OpenAI-compatible interface
- Anthropic SDK (@anthropic-ai/sdk)
- Google Generative AI (@google/generative-ai, @google-cloud/vertexai)
- Azure OpenAI
- Cohere, Mistral, Groq, Together AI, Ollama
- DeepSeek, OpenRouter, Cerebras, Fireworks AI, Perplexity
"""

from __future__ import annotations

import logging
import re
from typing import Any

from xelo.adapters.base import ComponentDetection
from xelo.adapters.typescript._ts_regex import TSFrameworkAdapter
from xelo.core.ts_parser import TSParseResult, parse_typescript
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, dict[str, list[str]]] = {
    "openai": {
        "packages": ["openai"],
        "classes": ["OpenAI", "AzureOpenAI", "AsyncOpenAI"],
    },
    "anthropic": {
        "packages": ["@anthropic-ai/sdk", "anthropic"],
        "classes": ["Anthropic", "AnthropicClient"],
    },
    "google": {
        "packages": ["@google/generative-ai", "@google/genai", "@google-cloud/vertexai"],
        "classes": ["GoogleGenerativeAI", "GoogleGenAI", "GenerativeModel", "VertexAI"],
    },
    "cohere": {
        "packages": ["cohere-ai", "@cohere-ai/cohere-js"],
        "classes": ["CohereClient", "Cohere"],
    },
    "mistral": {
        "packages": ["@mistralai/mistralai", "mistralai"],
        "classes": ["Mistral", "MistralClient"],
    },
    "groq": {
        "packages": ["groq-sdk", "@groq/groq-sdk"],
        "classes": ["Groq", "GroqClient"],
    },
    # Aligned to Python adapter: key is "togetherai" not "together"
    "togetherai": {
        "packages": ["together-ai", "@together-ai/together", "together"],
        "classes": ["Together", "TogetherClient", "TogetherAI"],
    },
    "ollama": {
        "packages": ["ollama", "ollama-js", "@ollama/client"],
        "classes": ["Ollama", "OllamaClient"],
    },
    "deepseek": {
        "packages": ["deepseek", "@deepseek-ai/sdk"],
        "classes": ["DeepSeek", "DeepSeekClient"],
    },
    "openrouter": {
        "packages": ["openrouter", "@openrouter/ai-sdk-provider"],
        "classes": ["OpenRouter", "OpenRouterClient"],
    },
    "cerebras": {
        "packages": ["@cerebras/cerebras_cloud_sdk"],
        "classes": ["Cerebras", "CerebrasClient"],
    },
    "fireworks": {
        "packages": ["fireworks-ai", "@fireworks-ai/inference-sdk"],
        "classes": ["FireworksAI", "Fireworks"],
    },
    "perplexity": {
        "packages": ["perplexity-sdk", "@perplexity-ai/sdk"],
        "classes": ["Perplexity", "PerplexityClient"],
    },
}

_ALL_PACKAGES: list[str] = []
_ALL_CLASSES: list[str] = []
_PKG_TO_PROVIDER: dict[str, str] = {}
_CLS_TO_PROVIDER: dict[str, str] = {}

for _prov, _cfg in _PROVIDERS.items():
    for _pkg in _cfg["packages"]:
        _ALL_PACKAGES.append(_pkg)
        _PKG_TO_PROVIDER[_pkg] = _prov
    for _cls in _cfg["classes"]:
        _ALL_CLASSES.append(_cls)
        _CLS_TO_PROVIDER[_cls] = _prov

_MODEL_CARD_URLS: dict[str, str] = {
    "openai": "https://platform.openai.com/docs/models",
    "anthropic": "https://docs.anthropic.com/en/docs/about-claude/models",
    "google": "https://ai.google.dev/gemini-api/docs/models",
    "azure": "https://learn.microsoft.com/azure/ai-services/openai/concepts/models",
    "mistral": "https://docs.mistral.ai/getting-started/models/",
    "cohere": "https://docs.cohere.com/docs/models",
    "groq": "https://console.groq.com/docs/models",
    "togetherai": "https://docs.together.ai/docs/inference-models",
    "ollama": "https://ollama.com/library",
    "deepseek": "https://api-docs.deepseek.com/",
    "openrouter": "https://openrouter.ai/models",
    "cerebras": "https://inference-docs.cerebras.ai/introduction",
    "fireworks": "https://fireworks.ai/models",
    "perplexity": "https://docs.perplexity.ai/guides/model-cards",
}

_DEFAULT_ENDPOINTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
    "cohere": "https://api.cohere.ai",
    "mistral": "https://api.mistral.ai",
    "groq": "https://api.groq.com/openai/v1",
    "togetherai": "https://api.together.xyz",
    "ollama": "http://localhost:11434/v1",
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "cerebras": "https://inference.cerebras.ai/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "perplexity": "https://api.perplexity.ai",
}

# ---------------------------------------------------------------------------
# base_url → provider resolution for OpenAI-compatible proxy pattern
# ---------------------------------------------------------------------------
# Many TS/JS apps use: const client = new OpenAI({ baseURL: "https://api.groq.com/..." })
# The table is order-sensitive — more specific substrings first.
# Kept in sync with the Python adapter's _BASE_URL_TO_PROVIDER table.

_BASE_URL_TO_PROVIDER: list[tuple[str, str]] = [
    ("api.groq.com", "groq"),
    ("generativelanguage.googleapis.com", "google"),
    ("aiplatform.googleapis.com", "google"),
    ("localhost:11434", "ollama"),
    ("127.0.0.1:11434", "ollama"),
    ("0.0.0.0:11434", "ollama"),
    ("api.anthropic.com", "anthropic"),
    ("api.mistral.ai", "mistral"),
    ("api.together.xyz", "togetherai"),
    ("api.deepseek.com", "deepseek"),
    ("openrouter.ai", "openrouter"),
    ("api.cohere.ai", "cohere"),
    ("inference.cerebras.ai", "cerebras"),
    ("api.fireworks.ai", "fireworks"),
    ("api.perplexity.ai", "perplexity"),
]


def _resolve_provider_from_base_url(base_url: str) -> str | None:
    """Resolve a provider string from an OpenAI-compatible ``baseURL`` value.

    Returns the provider name (e.g. ``"groq"``) or ``None`` if the URL doesn't
    match any known provider. Kept in sync with the Python adapter.
    """
    if not base_url:
        return None
    url_lower = base_url.lower()
    for substring, provider in _BASE_URL_TO_PROVIDER:
        if substring in url_lower:
            _log.debug("TS base_url %r → provider %r", base_url, provider)
            return provider
    return None


_MODEL_CALL_RE = re.compile(
    r"\b(chat\.completions\.create|completions\.create|messages\.create"
    r"|generateContent|getGenerativeModel|getTextEmbeddingModel)\b"
)


class LLMClientTSAdapter(TSFrameworkAdapter):
    """Detect common LLM SDK client usage in TypeScript/JavaScript files."""

    name = "llm_clients_ts"
    priority = 30
    handles_imports = _ALL_PACKAGES

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result: TSParseResult = (
            parse_result
            if isinstance(parse_result, TSParseResult)
            else parse_typescript(content, file_path)
        )
        if not self._detect(result):
            return []

        detected: list[ComponentDetection] = []

        imported_providers: set[str] = set()
        for imp in result.imports:
            for pkg, prov in _PKG_TO_PROVIDER.items():
                if pkg in imp.module or imp.module == pkg:
                    imported_providers.add(prov)

        # --- Class instantiations: new OpenAI({ model: "..." }) ---
        for inst in result.instantiations:
            provider = _CLS_TO_PROVIDER.get(inst.class_name)
            if provider is None:
                continue

            # Resolve provider from baseURL for the OpenAI-compatible proxy pattern:
            #   const client = new OpenAI({ baseURL: "https://api.groq.com/..." })
            # TypeScript uses camelCase "baseURL" but also accept "baseUrl" / "base_url".
            base_url_provider: str | None = None
            if inst.class_name in {"OpenAI", "AsyncOpenAI", "AzureOpenAI"}:
                raw_url = self._resolve(inst, "baseURL", "baseUrl", "base_url")
                if raw_url:
                    base_url_provider = _resolve_provider_from_base_url(raw_url)
                    if base_url_provider:
                        provider = base_url_provider
                        _log.debug(
                            "%s: OpenAI TS proxy → provider=%r (baseURL=%r)",
                            file_path,
                            base_url_provider,
                            raw_url,
                        )

            # resolved_arguments has variable-referenced model names expanded
            model_name = self._resolve(inst, "model", "modelName", "modelId")

            # No explicit model: if base_url resolved to a known provider, emit a
            # FRAMEWORK node so the proxied provider is visible in the SBOM
            # (mirrors the Python adapter behaviour).
            if not model_name:
                if base_url_provider:
                    raw_url = self._resolve(inst, "baseURL", "baseUrl", "base_url")
                    detected.append(
                        ComponentDetection(
                            component_type=ComponentType.FRAMEWORK,
                            canonical_name=f"framework:{base_url_provider}",
                            display_name=base_url_provider,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.88,
                            metadata={
                                "framework": base_url_provider,
                                "via_openai_proxy": True,
                                "base_url": raw_url,
                                "language": "typescript",
                            },
                            file_path=file_path,
                            line=inst.line_start,
                            snippet=inst.source_snippet
                            or f"new {inst.class_name}({{ baseURL: {raw_url!r} }})",
                            evidence_kind="ast_instantiation",
                        )
                    )
                continue

            is_azure = "Azure" in inst.class_name or "azure" in str(inst.resolved_arguments).lower()
            effective_provider = "azure" if is_azure else provider
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=canonicalize_text(model_name.lower()),
                    display_name=model_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": effective_provider,
                        "client_class": inst.class_name,
                        "provider": effective_provider,
                        "is_azure": is_azure,
                        "model_card_url": _MODEL_CARD_URLS.get(effective_provider),
                        "api_endpoint": _DEFAULT_ENDPOINTS.get(effective_provider),
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or "",
                    evidence_kind="ast_instantiation",
                )
            )

        # --- API call patterns: client.chat.completions.create({ model: "gpt-4o" }) ---
        for call in result.function_calls:
            if not _MODEL_CALL_RE.search(call.function_name):
                continue
            model_name = self._resolve(call, "model", "modelId")
            if not model_name and call.positional_args:
                model_name = self._clean(call.positional_args[0])
            if not model_name:
                continue
            fn = call.function_name
            receiver = (call.receiver or "").lower()
            # Infer provider from function name semantics or receiver object name
            if "messages.create" in fn:
                provider = "anthropic"
            elif (
                "generateContent" in fn
                or "getGenerativeModel" in fn
                or "getTextEmbeddingModel" in fn
            ):
                provider = "google"
            elif "ollama" in receiver or "ollama" in fn.lower():
                provider = "ollama"
            else:
                # Fall back to the imported provider when exactly one non-generic SDK is present
                non_openai = imported_providers - {"openai"}
                provider = next(iter(non_openai)) if len(non_openai) == 1 else "openai"
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=canonicalize_text(model_name.lower()),
                    display_name=model_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "framework": provider,
                        "api_call": fn,
                        "provider": provider,
                        "model_card_url": _MODEL_CARD_URLS.get(provider),
                        "api_endpoint": _DEFAULT_ENDPOINTS.get(provider),
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=call.line_start,
                    snippet=call.source_snippet or f"{fn}(...)",
                    evidence_kind="ast_call",
                )
            )

        return detected


# Backwards-compatible exports
LLM_CLIENT_TS_PACKAGES = _ALL_PACKAGES
LLM_CLIENT_TS_CLASSES = _ALL_CLASSES
