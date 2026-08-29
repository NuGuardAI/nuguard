"""Tests for Go direct-HTTP LLM call detection (phase 8, no SDK import)."""

from __future__ import annotations

from nuguard.sbom.adapters.go import extract_go_direct_http_llm_calls
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType


def _extract(source: str, file_path: str = "chat.go"):
    result = parse_go(source, file_path)
    return extract_go_direct_http_llm_calls(result, file_path)


def test_hand_rolled_anthropic_client_emits_model_node() -> None:
    src = """
package main

const anthropicURL = "https://api.anthropic.com/v1/messages"
const chatModel = "claude-sonnet-4-6"

func callAnthropic() {
	_ = anthropicURL
	_ = chatModel
}
"""
    detections = _extract(src)
    assert len(detections) == 1
    node = detections[0]
    assert node.component_type == ComponentType.MODEL
    assert node.display_name == "claude-sonnet-4-6"
    assert node.metadata["provider"] == "anthropic"
    assert node.evidence_kind == "ast_string_literal"
    assert node.line > 0


def test_openai_host_resolves_provider() -> None:
    src = """
package main

const openaiURL = "https://api.openai.com/v1/chat/completions"
const model = "gpt-4-turbo"

func run() {
	_ = openaiURL
	_ = model
}
"""
    detections = _extract(src)
    assert len(detections) == 1
    assert detections[0].metadata["provider"] == "openai"
    assert detections[0].display_name == "gpt-4-turbo"


def test_no_known_llm_host_yields_no_detections() -> None:
    src = """
package main

const someURL = "https://example.com/api"
const modelLike = "claude-sonnet-4-6"

func run() {
	_ = someURL
	_ = modelLike
}
"""
    # Model-shaped string present, but no known LLM API host in the file —
    # not enough signal on its own to avoid false positives.
    assert _extract(src) == []


def test_known_host_without_model_string_yields_no_detections() -> None:
    src = """
package main

const anthropicURL = "https://api.anthropic.com/v1/messages"

func run() {
	_ = anthropicURL
}
"""
    assert _extract(src) == []


def test_multiple_model_strings_each_get_a_node() -> None:
    src = """
package main

const anthropicURL = "https://api.anthropic.com/v1/messages"

const (
	fastModel = "claude-haiku-4-5"
	smartModel = "claude-opus-4-8"
)

func run() {
	_ = fastModel
	_ = smartModel
}
"""
    detections = _extract(src)
    names = {d.display_name for d in detections}
    assert names == {"claude-haiku-4-5", "claude-opus-4-8"}
    assert all(d.metadata["provider"] == "anthropic" for d in detections)
