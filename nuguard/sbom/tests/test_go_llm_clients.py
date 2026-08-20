"""Tests for the Go LLM client adapter and extractor wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go.llm_clients import GoLLMClientsAdapter
from nuguard.sbom.adapters.registry import default_framework_adapters
from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.types import ComponentType

_ADAPTER = GoLLMClientsAdapter()


def _extract(source: str, path: str = "main.go") -> list[ComponentDetection]:
    return _ADAPTER.extract(source, path, parse_go(source, path))


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [item for item in detections if item.component_type == component_type]


def _frameworks(detections: list[ComponentDetection]) -> dict[str, ComponentDetection]:
    return {
        item.metadata["provider"]: item
        for item in _by_type(detections, ComponentType.FRAMEWORK)
    }


def _models(detections: list[ComponentDetection]) -> dict[str, ComponentDetection]:
    return {item.display_name: item for item in _by_type(detections, ComponentType.MODEL)}


def test_adapter_is_registered() -> None:
    names = {adapter.name for adapter in default_framework_adapters()}

    assert "llm_clients_go" in names
    assert _ADAPTER.priority == 90


@pytest.mark.parametrize(
    "module_path",
    [
        "github.com/sashabaranov/go-openai",
        "github.com/anthropics/anthropic-sdk-go",
        "github.com/google/generative-ai-go/genai",
        "github.com/ollama/ollama/api",
    ],
)
def test_can_handle_supported_modules(module_path: str) -> None:
    result = parse_go(f'package main\nimport "{module_path}"\n', "main.go")

    assert _ADAPTER.can_handle(result) is True


@pytest.mark.parametrize(
    "module_path",
    [
        "github.com/sashabaranov/go-openai2",
        "github.com/tmc/langchaingo",
        "github.com/ollama/ollama",
        "github.com/openai/openai-go",
        "google.golang.org/genai",
    ],
)
def test_can_handle_rejects_unrelated_and_lookalike_modules(module_path: str) -> None:
    result = parse_go(f'package main\nimport "{module_path}"\n', "main.go")

    assert _ADAPTER.can_handle(result) is False


def test_go_openai_chat_completion_string_model() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    _ = openai.ChatCompletionRequest{Model: "gpt-4o"}
}
"""
    detections = _extract(source)
    frameworks = _frameworks(detections)
    models = _models(detections)

    assert "openai" in frameworks
    assert frameworks["openai"].canonical_name == "framework:openai"
    assert frameworks["openai"].metadata["language"] == "golang"
    assert frameworks["openai"].metadata["framework"] == "openai"
    assert frameworks["openai"].metadata["provider"] == "openai"
    assert frameworks["openai"].metadata["client_kind"] == "llm_sdk"
    assert frameworks["openai"].evidence_kind == "ast_import"
    assert "gpt-4o" in models
    assert models["gpt-4o"].metadata["provider"] == "openai"
    assert models["gpt-4o"].metadata["framework"] == "openai"
    assert models["gpt-4o"].metadata["language"] == "golang"
    assert models["gpt-4o"].metadata["client_class"] == "ChatCompletionRequest"
    assert models["gpt-4o"].evidence_kind == "ast_instantiation"
    assert [item.canonical_name for item in _by_type(detections, ComponentType.FRAMEWORK)] == [
        "framework:openai"
    ]


def test_go_openai_bare_new_client_is_framework_only() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    client := openai.NewClient("sk-test")
    _ = client
}
"""
    detections = _extract(source)

    assert "openai" in _frameworks(detections)
    assert _by_type(detections, ComponentType.MODEL) == []


def test_explicit_go_openai_alias_import() -> None:
    source = """package main

import oai "github.com/sashabaranov/go-openai"

func main() {
    _ = oai.ChatCompletionRequest{Model: "gpt-4o-mini"}
}
"""
    detections = _extract(source)
    frameworks = _frameworks(detections)

    assert "gpt-4o-mini" in _models(detections)
    assert frameworks["openai"].snippet == 'import oai "github.com/sashabaranov/go-openai"'


def test_const_resolved_string_model() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

const modelName = "gpt-4o-mini"

func main() {
    _ = openai.ChatCompletionRequest{Model: modelName}
}
"""
    detections = _extract(source)

    assert "gpt-4o-mini" in _models(detections)


def test_dynamic_model_does_not_emit_model_node() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    _ = openai.ChatCompletionRequest{Model: runtimeModel}
}
"""
    detections = _extract(source)

    assert "openai" in _frameworks(detections)
    assert _by_type(detections, ComponentType.MODEL) == []


def test_sdk_constant_model_does_not_emit_model_node() -> None:
    """Parser limitation: selector constants stay unresolved (``$openai.GPT4o``)."""
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    _ = openai.ChatCompletionRequest{Model: openai.GPT4o}
}
"""
    detections = _extract(source)

    assert "openai" in _frameworks(detections)
    assert _by_type(detections, ComponentType.MODEL) == []


def test_anthropic_string_model() -> None:
    source = """package main

import "github.com/anthropics/anthropic-sdk-go"

func main() {
    _ = anthropic.MessageNewParams{Model: "claude-3-5-sonnet"}
}
"""
    detections = _extract(source)
    models = _models(detections)

    assert "anthropic" in _frameworks(detections)
    assert "claude-3-5-sonnet" in models
    assert models["claude-3-5-sonnet"].metadata["provider"] == "anthropic"
    assert models["claude-3-5-sonnet"].metadata["framework"] == "anthropic"


def test_anthropic_message_request_string_model() -> None:
    source = """package main

import "github.com/anthropics/anthropic-sdk-go"

func main() {
    _ = anthropic.MessageRequest{Model: "claude-3-haiku"}
}
"""
    detections = _extract(source)

    assert "claude-3-haiku" in _models(detections)


def test_google_generative_model_call() -> None:
    source = """package main

import "github.com/google/generative-ai-go/genai"

func main() {
    model := client.GenerativeModel("gemini-1.5-flash")
    _ = model
}
"""
    detections = _extract(source)
    models = _models(detections)

    assert "google" in _frameworks(detections)
    assert "gemini-1.5-flash" in models
    assert models["gemini-1.5-flash"].metadata["provider"] == "google"
    assert models["gemini-1.5-flash"].metadata["framework"] == "google"
    assert models["gemini-1.5-flash"].evidence_kind == "ast_call"
    assert models["gemini-1.5-flash"].metadata["client_class"] == "GenerativeModel"


def test_google_embedding_model_call() -> None:
    source = """package main

import "github.com/google/generative-ai-go/genai"

func main() {
    _ = client.EmbeddingModel("text-embedding-004")
}
"""
    detections = _extract(source)
    models = _models(detections)

    assert "text-embedding-004" in models
    assert models["text-embedding-004"].metadata["provider"] == "google"


def test_ollama_requests_keep_ollama_provider() -> None:
    source = """package main

import "github.com/ollama/ollama/api"

func main() {
    _ = api.ChatRequest{Model: "llama3.2"}
    _ = api.GenerateRequest{Model: "llama3.2"}
    _ = api.EmbedRequest{Model: "nomic-embed-text"}
}
"""
    detections = _extract(source)
    models = _models(detections)

    assert "ollama" in _frameworks(detections)
    assert models["llama3.2"].metadata["provider"] == "ollama"
    assert models["llama3.2"].metadata["framework"] == "ollama"
    assert models["nomic-embed-text"].metadata["provider"] == "ollama"
    assert models["nomic-embed-text"].metadata["framework"] == "ollama"


def test_client_config_base_url_emits_proxy_framework() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    cfg := openai.ClientConfig{
        BaseURL: "https://api.groq.com/openai/v1",
    }
    _ = openai.NewClientWithConfig(cfg)
}
"""
    detections = _extract(source)
    frameworks = _frameworks(detections)

    assert "openai" in frameworks
    assert "groq" in frameworks
    assert frameworks["groq"].metadata["via_openai_proxy"] is True
    assert frameworks["groq"].metadata["base_url"] == "https://api.groq.com/openai/v1"
    assert frameworks["groq"].metadata["language"] == "golang"
    assert _by_type(detections, ComponentType.MODEL) == []


def test_nested_client_config_emits_proxy_framework_without_remapping_model() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    _ = openai.NewClientWithConfig(openai.ClientConfig{
        BaseURL: "https://api.groq.com/openai/v1",
    })
    _ = openai.ChatCompletionRequest{Model: "llama-3.3-70b-versatile"}
}
"""
    detections = _extract(source)
    models = _models(detections)

    assert "groq" in _frameworks(detections)
    assert "llama-3.3-70b-versatile" in models
    assert models["llama-3.3-70b-versatile"].metadata["framework"] == "openai"
    assert models["llama-3.3-70b-versatile"].metadata["provider"] == "meta"
    assert models["llama-3.3-70b-versatile"].metadata["provider"] != "groq"
    assert "api.groq.com" not in (models["llama-3.3-70b-versatile"].metadata.get("api_endpoint") or "")


def test_proxy_client_config_does_not_remap_unrelated_openai_model() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    cfg := openai.ClientConfig{
        BaseURL: "https://api.groq.com/openai/v1",
    }
    _ = openai.NewClientWithConfig(cfg)
    _ = openai.NewClient("sk-test")
    _ = openai.ChatCompletionRequest{Model: "gpt-4o"}
}
"""
    detections = _extract(source)
    models = _models(detections)
    frameworks = _frameworks(detections)

    assert "openai" in frameworks
    assert "groq" in frameworks
    assert frameworks["groq"].metadata["via_openai_proxy"] is True
    assert "gpt-4o" in models
    assert models["gpt-4o"].metadata["provider"] == "openai"
    assert models["gpt-4o"].metadata["framework"] == "openai"
    assert "api.groq.com" not in (models["gpt-4o"].metadata.get("api_endpoint") or "")


def test_localhost_ollama_base_url_mapping() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    _ = openai.ClientConfig{BaseURL: "http://localhost:11434/v1"}
}
"""
    detections = _extract(source)
    frameworks = _frameworks(detections)

    assert "ollama" in frameworks
    assert frameworks["ollama"].metadata["via_openai_proxy"] is True


def test_default_config_field_assignment_is_not_invented() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    cfg := openai.DefaultConfig("sk-test")
    cfg.BaseURL = "https://api.groq.com/openai/v1"
    _ = openai.NewClientWithConfig(cfg)
}
"""
    detections = _extract(source)

    assert "openai" in _frameworks(detections)
    assert "groq" not in _frameworks(detections)


def test_local_type_without_sdk_import_is_ignored() -> None:
    source = """package main

type ChatCompletionRequest struct {
    Model string
}

func main() {
    _ = ChatCompletionRequest{Model: "gpt-4o"}
}
"""
    detections = _extract(source)

    assert detections == []
    assert _ADAPTER.can_handle(parse_go(source, "main.go")) is False


def test_local_unqualified_chat_request_is_not_ollama_model() -> None:
    source = """package main

import "github.com/ollama/ollama/api"

type ChatRequest struct {
    Model string
}

func main() {
    _ = ChatRequest{Model: "local-model"}
}
"""
    detections = _extract(source)

    assert "ollama" in _frameworks(detections)
    assert _by_type(detections, ComponentType.MODEL) == []


def test_local_unqualified_chat_completion_request_is_not_openai_model() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

type ChatCompletionRequest struct {
    Model string
}

func main() {
    _ = ChatCompletionRequest{Model: "gpt-4o"}
}
"""
    detections = _extract(source)

    assert "openai" in _frameworks(detections)
    assert _by_type(detections, ComponentType.MODEL) == []


def test_aliased_openai_request_is_detected() -> None:
    source = """package main

import oai "github.com/sashabaranov/go-openai"

func main() {
    _ = oai.ChatCompletionRequest{Model: "gpt-4o"}
}
"""
    detections = _extract(source)
    models = _models(detections)

    assert "gpt-4o" in models
    assert models["gpt-4o"].metadata["framework"] == "openai"
    assert models["gpt-4o"].metadata["provider"] == "openai"


def test_dot_imported_openai_request_is_detected() -> None:
    source = """package main

import . "github.com/sashabaranov/go-openai"

func main() {
    _ = ChatCompletionRequest{Model: "gpt-4o"}
}
"""
    detections = _extract(source)
    models = _models(detections)

    assert "gpt-4o" in models
    assert models["gpt-4o"].metadata["framework"] == "openai"
    assert models["gpt-4o"].metadata["provider"] == "openai"


def test_wrong_qualifier_is_not_attributed_to_imported_sdk() -> None:
    source = """package main

import "github.com/ollama/ollama/api"

func main() {
    _ = local.ChatRequest{Model: "local-model"}
}
"""
    detections = _extract(source)

    assert "ollama" in _frameworks(detections)
    assert _by_type(detections, ComponentType.MODEL) == []


def test_blank_import_does_not_match_unqualified_request() -> None:
    source = """package main

import _ "github.com/ollama/ollama/api"

func main() {
    _ = ChatRequest{Model: "local-model"}
}
"""
    detections = _extract(source)

    assert "ollama" in _frameworks(detections)
    assert _by_type(detections, ComponentType.MODEL) == []


def test_openai_sdk_llama_model_keeps_openai_framework() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    _ = openai.ChatCompletionRequest{Model: "llama3.2"}
}
"""
    detections = _extract(source)
    models = _models(detections)

    assert "llama3.2" in models
    assert models["llama3.2"].metadata["framework"] == "openai"
    assert models["llama3.2"].metadata["provider"] == "meta"
    assert len(_by_type(detections, ComponentType.MODEL)) == 1


def test_local_unqualified_client_config_is_not_proxy_framework() -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

type ClientConfig struct {
    BaseURL string
}

func main() {
    _ = ClientConfig{BaseURL: "https://api.groq.com/openai/v1"}
}
"""
    detections = _extract(source)
    frameworks = _frameworks(detections)

    assert "openai" in frameworks
    assert "groq" not in frameworks


def test_extractor_go_file_emits_framework_and_model(tmp_path: Path) -> None:
    source = """package main

import "github.com/sashabaranov/go-openai"

func main() {
    _ = openai.ChatCompletionRequest{Model: "gpt-4o"}
}
"""
    (tmp_path / "main.go").write_text(source)
    doc = AiSbomExtractor().extract_from_path(
        tmp_path,
        AiSbomConfig(include_extensions={".go"}, enable_llm=False),
    )

    adapters = {node.metadata.extras.get("adapter") for node in doc.nodes}
    models = [node for node in doc.nodes if node.component_type == ComponentType.MODEL]
    frameworks = [
        node for node in doc.nodes if node.component_type == ComponentType.FRAMEWORK
    ]

    assert "llm_clients_go" in adapters
    assert any(node.metadata.extras.get("provider") == "openai" for node in frameworks)
    assert any(node.metadata.extras.get("language") == "golang" for node in frameworks)
    assert any("gpt-4o" in node.name.lower() for node in models)
    assert any(node.metadata.extras.get("provider") == "openai" for node in models)
