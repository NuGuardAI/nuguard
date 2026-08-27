"""Tests for the Go LLM SDK framework adapters."""

from __future__ import annotations

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go import (
    AnthropicSDKGoAdapter,
    GoogleGenAIAdapter,
    GoOpenAIAdapter,
    LangChainGoAdapter,
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
    ):
        assert _extract(adapter, src) == []
