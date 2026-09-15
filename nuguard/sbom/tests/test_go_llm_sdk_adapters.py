"""Tests for the Go LLM SDK framework adapters."""

from __future__ import annotations

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go import (
    AnthropicSDKGoAdapter,
    GoogleGenAIAdapter,
    GoOpenAIAdapter,
    LangChainGoAdapter,
    OllamaSDKGoAdapter,
)
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [item for item in detections if item.component_type == component_type]


def _extract(adapter, source: str, file_path: str = "main.go") -> list[ComponentDetection]:
    return adapter.extract(source, file_path, parse_go(source, file_path))


_GENAI_SRC = """
package main

import "github.com/google/generative-ai-go/genai"

func run(client *genai.Client) {
	model := client.GenerativeModel("gemini-1.5-flash")
	_ = model
}
"""


def test_google_genai_emits_framework_and_model() -> None:
    detections = _extract(GoogleGenAIAdapter(), _GENAI_SRC)
    frameworks = _by_type(detections, ComponentType.FRAMEWORK)
    models = _by_type(detections, ComponentType.MODEL)

    assert len(frameworks) == 1
    assert len(models) == 1
    assert models[0].display_name == "gemini-1.5-flash"
    assert models[0].relationships


_GO_OPENAI_SRC = """
package main

import openai "github.com/sashabaranov/go-openai"

func run(client *openai.Client) {
	req := openai.ChatCompletionRequest{
		Model: "gpt-4-turbo",
	}
	client.CreateChatCompletion(req)
}
"""


def test_go_openai_reads_model_field_from_request_struct() -> None:
    detections = _extract(GoOpenAIAdapter(), _GO_OPENAI_SRC)
    models = _by_type(detections, ComponentType.MODEL)
    assert len(models) == 1
    assert models[0].display_name == "gpt-4-turbo"


def test_go_openai_unresolved_constant_yields_no_model_node() -> None:
    src = """
package main

import openai "github.com/sashabaranov/go-openai"

func run() {
	req := openai.ChatCompletionRequest{
		Model: openai.GPT4,
	}
	_ = req
}
"""
    detections = _extract(GoOpenAIAdapter(), src)
    assert _by_type(detections, ComponentType.MODEL) == []
    # Framework presence is still reported even without a resolvable model.
    assert len(_by_type(detections, ComponentType.FRAMEWORK)) == 1


_ANTHROPIC_SRC = """
package main

import "github.com/anthropics/anthropic-sdk-go"

func run(client *anthropic.Client) {
	params := anthropic.MessageNewParams{
		Model: "claude-3-5-sonnet-20241022",
	}
	client.Messages.New(params)
}
"""


def test_anthropic_sdk_go_reads_model_field() -> None:
    detections = _extract(AnthropicSDKGoAdapter(), _ANTHROPIC_SRC)
    models = _by_type(detections, ComponentType.MODEL)
    assert len(models) == 1
    assert models[0].display_name == "claude-3-5-sonnet-20241022"


_LANGCHAINGO_SRC = """
package main

import "github.com/tmc/langchaingo/llms/openai"

func run() {
	llm, err := openai.New(openai.WithModel("gpt-4o"))
	_ = llm
	_ = err
}
"""


def test_langchaingo_reads_with_model_option_regardless_of_provider() -> None:
    detections = _extract(LangChainGoAdapter(), _LANGCHAINGO_SRC)
    frameworks = _by_type(detections, ComponentType.FRAMEWORK)
    models = _by_type(detections, ComponentType.MODEL)
    assert len(frameworks) == 1
    assert len(models) == 1
    assert models[0].display_name == "gpt-4o"


def test_adapters_no_op_without_matching_import() -> None:
    src = """
package main

func main() {
	println("no llm sdk here")
}
"""
    for adapter in (
        GoogleGenAIAdapter(),
        GoOpenAIAdapter(),
        AnthropicSDKGoAdapter(),
        LangChainGoAdapter(),
        OllamaSDKGoAdapter(),
    ):
        assert _extract(adapter, src) == []


# ---------------------------------------------------------------------------
# go-openai Tool/FunctionDefinition schema detection
# ---------------------------------------------------------------------------

_GO_OPENAI_TOOL_SRC = """
package main

import openai "github.com/sashabaranov/go-openai"

func run(client *openai.Client) {
	tool := openai.Tool{
		Type: openai.ToolTypeFunction,
		Function: &openai.FunctionDefinition{
			Name:        "get_weather",
			Description: "Retrieves current weather conditions",
		},
	}
	req := openai.ChatCompletionRequest{
		Model: "gpt-4-turbo",
		Tools: []openai.Tool{tool},
	}
	client.CreateChatCompletion(req)
}
"""


def test_go_openai_tool_struct_emits_tool_node() -> None:
    detections = _extract(GoOpenAIAdapter(), _GO_OPENAI_TOOL_SRC)
    tools = _by_type(detections, ComponentType.TOOL)
    assert len(tools) == 1
    assert tools[0].display_name == "get_weather"
    assert tools[0].metadata["description"] == "Retrieves current weather conditions"
    assert tools[0].relationships


_GO_OPENAI_STANDALONE_FUNCTION_DEF_SRC = """
package main

import openai "github.com/sashabaranov/go-openai"

func run() {
	fn := openai.FunctionDefinition{
		Name:        "search_docs",
		Description: "Searches internal documentation",
	}
	_ = fn
}
"""


def test_go_openai_standalone_function_definition_emits_tool_node() -> None:
    detections = _extract(GoOpenAIAdapter(), _GO_OPENAI_STANDALONE_FUNCTION_DEF_SRC)
    tools = _by_type(detections, ComponentType.TOOL)
    assert len(tools) == 1
    assert tools[0].display_name == "search_docs"
    assert tools[0].metadata["description"] == "Searches internal documentation"


def test_go_openai_tool_missing_description_still_emits_node() -> None:
    src = """
package main

import openai "github.com/sashabaranov/go-openai"

func run() {
	tool := openai.Tool{
		Function: &openai.FunctionDefinition{
			Name: "no_description_tool",
		},
	}
	_ = tool
}
"""
    detections = _extract(GoOpenAIAdapter(), src)
    tools = _by_type(detections, ComponentType.TOOL)
    assert len(tools) == 1
    assert tools[0].display_name == "no_description_tool"
    assert "description" not in tools[0].metadata


# ---------------------------------------------------------------------------
# ollama/ollama/api
# ---------------------------------------------------------------------------

_ADAPTER = OllamaSDKGoAdapter()


def test_ollama_sdk_chat_request_emits_framework_and_model() -> None:
    src = """
package main

import "github.com/ollama/ollama/api"

func run(client *api.Client) {
	req := api.ChatRequest{
		Model: "llama3.2",
	}
	_ = req
}
"""
    detections = _extract(_ADAPTER, src)
    frameworks = _by_type(detections, ComponentType.FRAMEWORK)
    models = _by_type(detections, ComponentType.MODEL)

    assert len(frameworks) == 1
    assert frameworks[0].canonical_name == "framework:ollama_sdk_go"
    assert frameworks[0].metadata["framework"] == "ollama_sdk_go"
    assert frameworks[0].metadata["provider"] == "ollama"
    assert len(models) == 1
    assert models[0].display_name == "llama3.2"
    assert models[0].metadata["framework"] == "ollama_sdk_go"
    assert models[0].metadata["provider"] == "ollama"
    assert models[0].metadata["language"] == "golang"
    assert models[0].confidence == 0.85
    assert models[0].relationships
    assert models[0].relationships[0].relationship_type == "USES"


def test_ollama_sdk_generate_request_reads_model_field() -> None:
    src = """
package main

import "github.com/ollama/ollama/api"

func run() {
	_ = api.GenerateRequest{Model: "mistral"}
}
"""
    detections = _extract(_ADAPTER, src)
    models = _by_type(detections, ComponentType.MODEL)

    assert len(models) == 1
    assert models[0].display_name == "mistral"
    assert models[0].metadata["provider"] == "ollama"


def test_ollama_sdk_embed_request_reads_model_field() -> None:
    src = """
package main

import "github.com/ollama/ollama/api"

func run() {
	_ = api.EmbedRequest{Model: "nomic-embed-text"}
}
"""
    detections = _extract(_ADAPTER, src)
    models = _by_type(detections, ComponentType.MODEL)

    assert len(models) == 1
    assert models[0].display_name == "nomic-embed-text"
    assert models[0].metadata["provider"] == "ollama"


def test_ollama_sdk_embedding_request_reads_model_field() -> None:
    src = """
package main

import "github.com/ollama/ollama/api"

func run() {
	_ = api.EmbeddingRequest{Model: "all-minilm"}
}
"""
    detections = _extract(_ADAPTER, src)
    models = _by_type(detections, ComponentType.MODEL)

    assert len(models) == 1
    assert models[0].display_name == "all-minilm"
    assert models[0].metadata["provider"] == "ollama"


def test_ollama_sdk_explicit_alias_is_detected() -> None:
    src = """
package main

import ollamaapi "github.com/ollama/ollama/api"

func run() {
	_ = ollamaapi.ChatRequest{Model: "llama3.2"}
}
"""
    detections = _extract(_ADAPTER, src)
    models = _by_type(detections, ComponentType.MODEL)

    assert len(models) == 1
    assert models[0].display_name == "llama3.2"
    assert models[0].metadata["provider"] == "ollama"


def test_ollama_sdk_dot_import_unqualified_request_is_detected() -> None:
    src = """
package main

import . "github.com/ollama/ollama/api"

func run() {
	_ = ChatRequest{Model: "llama3.2"}
}
"""
    detections = _extract(_ADAPTER, src)
    models = _by_type(detections, ComponentType.MODEL)

    assert len(models) == 1
    assert models[0].display_name == "llama3.2"


def test_ollama_sdk_client_from_environment_is_framework_only() -> None:
    src = """
package main

import "github.com/ollama/ollama/api"

func run() {
	client, _ := api.ClientFromEnvironment()
	_ = client
}
"""
    detections = _extract(_ADAPTER, src)

    assert len(_by_type(detections, ComponentType.FRAMEWORK)) == 1
    assert _by_type(detections, ComponentType.MODEL) == []


def test_ollama_sdk_unresolved_model_yields_no_model_node() -> None:
    src = """
package main

import "github.com/ollama/ollama/api"

func run() {
	_ = api.ChatRequest{Model: runtimeModel}
}
"""
    detections = _extract(_ADAPTER, src)

    assert len(_by_type(detections, ComponentType.FRAMEWORK)) == 1
    assert _by_type(detections, ComponentType.MODEL) == []


def test_ollama_sdk_ignores_root_module_without_api() -> None:
    src = """
package main

import "github.com/ollama/ollama"

func run() {
	_ = api.ChatRequest{Model: "llama3.2"}
}
"""
    result = parse_go(src, "main.go")

    assert _ADAPTER.can_handle(result) is False
    assert _extract(_ADAPTER, src) == []
    assert _by_type(_extract(_ADAPTER, src), ComponentType.MODEL) == []


def test_ollama_sdk_ignores_langchaingo_ollama_import() -> None:
    src = """
package main

import "github.com/tmc/langchaingo/llms/ollama"

func run() {
	llm, _ := ollama.New()
	_ = llm
}
"""
    result = parse_go(src, "main.go")

    assert _ADAPTER.can_handle(result) is False
    assert _extract(_ADAPTER, src) == []
    assert _by_type(_extract(_ADAPTER, src), ComponentType.MODEL) == []


def test_ollama_sdk_local_unqualified_chat_request_is_not_a_model() -> None:
    src = """
package main

import "github.com/ollama/ollama/api"

type ChatRequest struct {
	Model string
}

func run() {
	_ = ChatRequest{Model: "local-model"}
}
"""
    detections = _extract(_ADAPTER, src)

    assert len(_by_type(detections, ComponentType.FRAMEWORK)) == 1
    assert _by_type(detections, ComponentType.MODEL) == []


def test_ollama_sdk_wrong_qualifier_is_not_a_model() -> None:
    src = """
package main

import "github.com/ollama/ollama/api"

func run() {
	_ = local.ChatRequest{Model: "local-model"}
}
"""
    detections = _extract(_ADAPTER, src)

    assert len(_by_type(detections, ComponentType.FRAMEWORK)) == 1
    assert _by_type(detections, ComponentType.MODEL) == []


def test_ollama_sdk_blank_import_does_not_match_unqualified_request() -> None:
    src = """
package main

import _ "github.com/ollama/ollama/api"

func run() {
	_ = ChatRequest{Model: "local-model"}
}
"""
    detections = _extract(_ADAPTER, src)

    assert len(_by_type(detections, ComponentType.FRAMEWORK)) == 1
    assert _by_type(detections, ComponentType.MODEL) == []
