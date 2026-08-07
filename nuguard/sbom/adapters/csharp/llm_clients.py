"""C# adapters for the Azure OpenAI, OpenAI, and Anthropic SDKs."""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ..models_kb import get_model_details, infer_provider
from ._csharp_base import CSharpFrameworkAdapter
from ._source import (
    find_calls,
    first_argument,
    line_number,
    resolve_expression,
    string_constants,
)

_PROVIDER_NAMESPACES: dict[str, tuple[str, ...]] = {
    "azure": ("Azure.AI.OpenAI",),
    "openai": ("OpenAI",),
    "anthropic": ("Anthropic",),
}
_CLIENT_CLASSES: dict[str, str] = {
    "AzureOpenAIClient": "azure",
    "OpenAIClient": "openai",
    "ChatClient": "openai",
    "ResponsesClient": "openai",
    "EmbeddingClient": "openai",
    "AssistantClient": "openai",
    "AnthropicClient": "anthropic",
}
_MODEL_CLIENT_CLASSES = {
    "ChatClient",
    "ResponsesClient",
    "EmbeddingClient",
}
_MODEL_FACTORY_CALLS = {
    "GetChatClient",
    "GetResponsesClient",
    "GetEmbeddingClient",
    "GetAssistantClient",
}
_MODEL_API_CALLS = {
    "CompleteChat",
    "CompleteChatAsync",
    "Create",
    "CreateAsync",
    "GenerateResponse",
    "GenerateResponseAsync",
}
_MODEL_ASSIGNMENT_RE = re.compile(
    r"\bModel\s*=\s*(?P<value>"
    r'\$?@?"(?:""|\\.|[^"])*"'
    r"|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
    re.MULTILINE,
)


class CSharpLLMClientsAdapter(CSharpFrameworkAdapter):
    """Detect direct C# usage of supported LLM client libraries."""

    name = "csharp_llm_clients"
    priority = 90
    handles_namespaces = [
        "Azure.AI.OpenAI",
        "OpenAI",
        "Anthropic",
    ]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = self._parse_result(
            content,
            file_path,
            parse_result,
        )
        providers = _providers_from_result(result)

        strong_markers = (
            "AzureOpenAIClient",
            "AnthropicClient",
            "OpenAI.Chat.ChatClient",
            "OpenAI.Responses.ResponsesClient",
            "OpenAI.Embeddings.EmbeddingClient",
        )

        if not providers and not any(marker in content for marker in strong_markers):
            return []

        constants = string_constants(result)
        detections: list[ComponentDetection] = []
        provider_lines: dict[str, int] = {}

        for directive in result.using_directives:
            provider = _provider_for_namespace(directive.namespace)

            if provider is not None:
                provider_lines.setdefault(
                    provider,
                    directive.line,
                )

        calls = find_calls(
            content,
            set(_CLIENT_CLASSES) | _MODEL_FACTORY_CALLS | _MODEL_API_CALLS,
        )

        client_providers: dict[str, str] = {}

        for call in calls:
            provider = _provider_for_call(
                call.name,
                call.receiver,
                providers,
                client_providers,
            )

            if provider is not None and call.assigned_to:
                client_providers[call.assigned_to] = provider

        for call in calls:
            provider = _provider_for_call(
                call.name,
                call.receiver,
                providers,
                client_providers,
            )

            if provider is None:
                continue

            provider_lines.setdefault(
                provider,
                call.line,
            )
            model_name = ""

            if call.name in _MODEL_CLIENT_CLASSES and call.is_constructor:
                model_name = first_argument(
                    call,
                    (
                        "model",
                        "modelId",
                        "model_id",
                        "deploymentName",
                    ),
                    constants,
                    0,
                )
            elif call.name in _MODEL_FACTORY_CALLS:
                model_name = first_argument(
                    call,
                    (
                        "model",
                        "modelId",
                        "deploymentName",
                        "deployment",
                    ),
                    constants,
                    0,
                )
            elif call.name in _MODEL_API_CALLS:
                model_name = first_argument(
                    call,
                    (
                        "model",
                        "modelId",
                        "model_id",
                    ),
                    constants,
                    None,
                )

            if model_name:
                detections.append(
                    _model_node(
                        adapter=self,
                        provider=provider,
                        model_name=model_name,
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        client_class=call.name,
                    )
                )

        if "anthropic" in providers or "AnthropicClient" in content:
            for match in _MODEL_ASSIGNMENT_RE.finditer(content):
                model_name = resolve_expression(
                    match.group("value"),
                    constants,
                )

                if not model_name:
                    continue

                line = line_number(
                    content,
                    match.start(),
                )
                provider_lines.setdefault(
                    "anthropic",
                    line,
                )
                detections.append(
                    _model_node(
                        adapter=self,
                        provider="anthropic",
                        model_name=model_name,
                        file_path=file_path,
                        line=line,
                        snippet=" ".join(match.group(0).split()),
                        client_class="AnthropicClient",
                    )
                )

        for provider in sorted(providers | set(provider_lines)):
            detections.insert(
                0,
                _framework_node(
                    adapter=self,
                    provider=provider,
                    file_path=file_path,
                    line=provider_lines.get(
                        provider,
                        0,
                    ),
                ),
            )

        return _dedupe(detections)


def _providers_from_result(
    result: Any,
) -> set[str]:
    providers: set[str] = set()

    for directive in result.using_directives:
        provider = _provider_for_namespace(directive.namespace)

        if provider is not None:
            providers.add(provider)

    return providers


def _provider_for_namespace(
    namespace: str,
) -> str | None:
    clean = namespace.removeprefix("global::")

    for provider, prefixes in _PROVIDER_NAMESPACES.items():
        if any(clean == prefix or clean.startswith(prefix + ".") for prefix in prefixes):
            return provider

    return None


def _provider_for_call(
    name: str,
    receiver: str | None,
    imported: set[str],
    client_providers: dict[str, str],
) -> str | None:
    if name == "OpenAIClient" and imported == {"azure"}:
        return "azure"

    if name in _CLIENT_CLASSES:
        return _CLIENT_CLASSES[name]

    receiver_text = receiver or ""
    receiver_root = receiver_text.split(
        ".",
        1,
    )[0]

    if receiver_root in client_providers:
        return client_providers[receiver_root]

    if "AzureOpenAI" in receiver_text:
        return "azure"

    if "Anthropic" in receiver_text or "Messages" in receiver_text:
        return "anthropic"

    if "OpenAI" in receiver_text or "Chat" in receiver_text or "Responses" in receiver_text:
        return "openai"

    if len(imported) == 1:
        return next(iter(imported))

    return None


def _framework_node(
    adapter: CSharpLLMClientsAdapter,
    provider: str,
    file_path: str,
    line: int,
) -> ComponentDetection:
    framework = "azure_openai" if provider == "azure" else provider
    display = {
        "azure": "Azure OpenAI .NET SDK",
        "openai": "OpenAI .NET SDK",
        "anthropic": "Anthropic C# SDK",
    }[provider]

    return ComponentDetection(
        component_type=ComponentType.FRAMEWORK,
        canonical_name=(f"framework:{framework}"),
        display_name=display,
        adapter_name=adapter.name,
        priority=adapter.priority,
        confidence=0.95,
        metadata={
            "framework": framework,
            "provider": provider,
            "language": "csharp",
            "client_kind": "llm_sdk",
        },
        file_path=file_path,
        line=line,
        snippet=f"using {display}",
        evidence_kind="ast_import",
    )


def _model_node(
    adapter: CSharpLLMClientsAdapter,
    provider: str,
    model_name: str,
    file_path: str,
    line: int,
    snippet: str,
    client_class: str,
) -> ComponentDetection:
    inferred = infer_provider(model_name)
    effective_provider = provider if provider != "azure" else "azure"

    if effective_provider == "openai" and inferred not in {"unknown", "openai"}:
        effective_provider = inferred

    details = get_model_details(
        model_name,
        effective_provider,
        {},
    )
    canonical = canonicalize_text(model_name.lower())
    framework = "azure_openai" if provider == "azure" else provider

    return ComponentDetection(
        component_type=ComponentType.MODEL,
        canonical_name=canonical,
        display_name=model_name,
        adapter_name=adapter.name,
        priority=adapter.priority,
        confidence=0.93,
        metadata={
            "framework": framework,
            "provider": effective_provider,
            "client_class": client_class,
            "language": "csharp",
            **{key: value for key, value in details.items() if value is not None},
        },
        file_path=file_path,
        line=line,
        snippet=snippet,
        evidence_kind="ast_call",
        relationships=[
            RelationshipHint(
                source_canonical=(f"framework:{framework}"),
                source_type=ComponentType.FRAMEWORK,
                target_canonical=canonical,
                target_type=ComponentType.MODEL,
                relationship_type="USES",
            )
        ],
    )


def _dedupe(
    detections: list[ComponentDetection],
) -> list[ComponentDetection]:
    seen: set[tuple[ComponentType, str]] = set()
    result: list[ComponentDetection] = []

    for detection in detections:
        key = (
            detection.component_type,
            detection.canonical_name,
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(detection)

    return result


__all__ = ["CSharpLLMClientsAdapter"]
