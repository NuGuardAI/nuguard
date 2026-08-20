"""Go adapters for direct LLM SDK client usage.

Detects FRAMEWORK and MODEL components from:

- ``github.com/sashabaranov/go-openai``
- ``github.com/anthropics/anthropic-sdk-go``
- ``github.com/google/generative-ai-go``
- ``github.com/ollama/ollama/api``

Matching is suffix-based after an SDK import is confirmed so explicit aliases
and the go-openai import-path / package-name mismatch still work. Unresolved
parser values (``$runtimeModel``, ``$openai.GPT4o``) are not turned into MODEL
nodes. ``ClientConfig.BaseURL`` values emit a proxy FRAMEWORK node using the
same OpenAI-compatible table as the Python adapter. The current parser has no
client→config→request dataflow, so those URLs do not remap unrelated MODEL
nodes. Field assignment after ``DefaultConfig`` is out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nuguard.common.logging import get_logger

from ...core.go_parser import GoImport, GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ..models_kb import get_model_details, infer_provider
from ._go_base import GoFrameworkAdapter

_log = get_logger(__name__)

# Kept in sync with the Python adapter's ``_BASE_URL_TO_PROVIDER`` table.
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
    """Resolve a provider string from an OpenAI-compatible ``BaseURL`` value."""
    if not base_url:
        return None
    url_lower = base_url.lower()
    for substring, provider in _BASE_URL_TO_PROVIDER:
        if substring in url_lower:
            _log.debug("Go BaseURL %r → provider %r", base_url, provider)
            return provider
    return None


@dataclass(frozen=True)
class _ProviderSpec:
    modules: tuple[str, ...]
    request_types: tuple[str, ...]
    config_types: tuple[str, ...] = ()
    model_methods: tuple[str, ...] = ()
    display_name: str = ""


_PROVIDERS: dict[str, _ProviderSpec] = {
    "openai": _ProviderSpec(
        modules=("github.com/sashabaranov/go-openai",),
        request_types=("ChatCompletionRequest", "CompletionRequest", "EmbeddingRequest"),
        config_types=("ClientConfig",),
        display_name="OpenAI",
    ),
    "anthropic": _ProviderSpec(
        modules=("github.com/anthropics/anthropic-sdk-go",),
        request_types=("MessageNewParams", "MessageRequest"),
        display_name="Anthropic",
    ),
    "google": _ProviderSpec(
        modules=("github.com/google/generative-ai-go",),
        request_types=(),
        model_methods=("GenerativeModel", "EmbeddingModel"),
        display_name="Google",
    ),
    "ollama": _ProviderSpec(
        modules=("github.com/ollama/ollama/api",),
        request_types=("ChatRequest", "GenerateRequest", "EmbedRequest"),
        display_name="Ollama",
    ),
}

_REQUEST_TYPE_TO_PROVIDER: dict[str, str] = {
    type_name: provider
    for provider, spec in _PROVIDERS.items()
    for type_name in spec.request_types
}
_CONFIG_TYPE_TO_PROVIDER: dict[str, str] = {
    type_name: provider
    for provider, spec in _PROVIDERS.items()
    for type_name in spec.config_types
}
_MODEL_METHOD_TO_PROVIDER: dict[str, str] = {
    method: provider
    for provider, spec in _PROVIDERS.items()
    for method in spec.model_methods
}


def _type_suffix(name: str) -> str:
    """Return the identifier after the last package qualifier."""
    cleaned = name.replace("*", "").replace("&", "").strip()
    return cleaned.rsplit(".", 1)[-1] if cleaned else ""


class GoLLMClientsAdapter(GoFrameworkAdapter):
    """Detect direct Go usage of supported LLM client libraries."""

    name = "llm_clients_go"
    priority = 90
    handles_imports = [
        "github.com/sashabaranov/go-openai",
        "github.com/anthropics/anthropic-sdk-go",
        "github.com/google/generative-ai-go",
        "github.com/ollama/ollama/api",
    ]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = (
            parse_result
            if isinstance(parse_result, GoParseResult)
            else parse_go(content, file_path)
        )
        imported = self._imported_providers(result)
        if not imported:
            return []

        detections: list[ComponentDetection] = []
        for provider, matched_import in imported.items():
            detections.append(
                self._provider_framework_node(file_path, matched_import, provider)
            )

        for base_url, proxy_provider in self._openai_proxy_configs(result, imported):
            detections.append(
                self._proxy_framework_node(
                    file_path,
                    proxy_provider,
                    base_url,
                    result,
                )
            )

        for inst in result.instantiations:
            if inst.kind != "struct_literal":
                continue
            type_name = _type_suffix(inst.class_name)
            sdk_provider = _REQUEST_TYPE_TO_PROVIDER.get(type_name)
            if sdk_provider is None or sdk_provider not in imported:
                continue
            model_name = self._resolve(inst, "Model")
            if not model_name:
                continue
            detections.append(
                self._model_node(
                    file_path=file_path,
                    model_name=model_name,
                    sdk_provider=sdk_provider,
                    client_class=type_name,
                    line=inst.line,
                    snippet=inst.source_snippet or f"{inst.class_name}{{Model: {model_name!r}}}",
                    evidence_kind="ast_instantiation",
                )
            )

        for call in result.function_calls:
            sdk_provider = _MODEL_METHOD_TO_PROVIDER.get(call.function_name)
            if sdk_provider is None or sdk_provider not in imported:
                continue
            model_name = self._resolve(call, 0)
            if not model_name:
                continue
            detections.append(
                self._model_node(
                    file_path=file_path,
                    model_name=model_name,
                    sdk_provider=sdk_provider,
                    client_class=call.function_name,
                    line=call.line,
                    snippet=call.source_snippet or f"{call.full_name}({model_name!r})",
                    evidence_kind="ast_call",
                )
            )

        return detections

    def _imported_providers(self, parse_result: GoParseResult) -> dict[str, GoImport]:
        """Return the first matching import for each supported SDK provider."""
        imported: dict[str, GoImport] = {}
        for item in parse_result.imports:
            for provider, spec in _PROVIDERS.items():
                if provider in imported:
                    continue
                if any(self._matches_module_path(item.path, module) for module in spec.modules):
                    imported[provider] = item
        return imported

    def _openai_proxy_configs(
        self,
        parse_result: GoParseResult,
        imported: dict[str, GoImport],
    ) -> list[tuple[str, str]]:
        """Return unique ``(BaseURL, proxy provider)`` pairs from ClientConfig literals.

        The parser does not link a config to a later request, so these values
        are used only for proxy FRAMEWORK nodes.
        """
        if "openai" not in imported:
            return []

        found: list[tuple[str, str]] = []
        seen_providers: set[str] = set()
        for inst in parse_result.instantiations:
            if inst.kind != "struct_literal":
                continue
            if _CONFIG_TYPE_TO_PROVIDER.get(_type_suffix(inst.class_name)) != "openai":
                continue
            url = self._resolve(inst, "BaseURL")
            proxy = _resolve_provider_from_base_url(url)
            if not proxy or proxy in seen_providers:
                continue
            seen_providers.add(proxy)
            found.append((url, proxy))
        return found

    def _provider_framework_node(
        self,
        file_path: str,
        matched_import: GoImport,
        provider: str,
    ) -> ComponentDetection:
        """Emit a per-SDK FRAMEWORK node using import provenance from ``_fw_node``."""
        spec = _PROVIDERS[provider]
        node = self._fw_node(
            file_path,
            matched_import,
            display_name=spec.display_name or provider,
        )
        node.canonical_name = f"framework:{provider}"
        node.metadata.update(
            {
                "framework": provider,
                "provider": provider,
                "client_kind": "llm_sdk",
            }
        )
        return node

    def _proxy_framework_node(
        self,
        file_path: str,
        provider: str,
        base_url: str,
        parse_result: GoParseResult,
    ) -> ComponentDetection:
        """Emit a FRAMEWORK node for an OpenAI-compatible proxy BaseURL."""
        evidence = next(
            (
                inst
                for inst in parse_result.instantiations
                if inst.kind == "struct_literal"
                and _CONFIG_TYPE_TO_PROVIDER.get(_type_suffix(inst.class_name)) == "openai"
                and self._resolve(inst, "BaseURL") == base_url
            ),
            None,
        )
        return ComponentDetection(
            component_type=ComponentType.FRAMEWORK,
            canonical_name=f"framework:{provider}",
            display_name=provider,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.88,
            metadata={
                "framework": provider,
                "provider": provider,
                "language": "golang",
                "via_openai_proxy": True,
                "base_url": base_url,
            },
            file_path=file_path,
            line=evidence.line if evidence is not None else 0,
            snippet=(
                evidence.source_snippet
                if evidence is not None and evidence.source_snippet
                else f"ClientConfig{{BaseURL: {base_url!r}}}"
            ),
            evidence_kind="ast_instantiation",
        )

    def _model_node(
        self,
        *,
        file_path: str,
        model_name: str,
        sdk_provider: str,
        client_class: str,
        line: int,
        snippet: str,
        evidence_kind: str,
    ) -> ComponentDetection:
        provider = self._effective_provider(model_name, sdk_provider)
        details = get_model_details(model_name, provider)
        canonical = canonicalize_text(model_name.lower())
        return ComponentDetection(
            component_type=ComponentType.MODEL,
            canonical_name=canonical,
            display_name=model_name,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.90 if evidence_kind == "ast_instantiation" else 0.95,
            metadata={
                "framework": provider,
                "provider": provider,
                "client_class": client_class,
                "language": "golang",
                **{key: value for key, value in details.items() if value is not None},
            },
            file_path=file_path,
            line=line,
            snippet=snippet,
            evidence_kind=evidence_kind,
        )

    @staticmethod
    def _effective_provider(
        model_name: str,
        sdk_provider: str,
    ) -> str:
        if sdk_provider == "ollama":
            return "ollama"
        inferred = infer_provider(model_name)
        if inferred != "unknown":
            return inferred
        return sdk_provider


__all__ = ["GoLLMClientsAdapter"]
