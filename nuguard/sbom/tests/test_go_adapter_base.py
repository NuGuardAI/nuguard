"""Tests for the GoFrameworkAdapter base class."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from nuguard.sbom.adapters.go._go_base import GoFrameworkAdapter
from nuguard.sbom.types import ComponentType


@dataclass
class DummyGoImport:
    path: str


@dataclass
class DummyGoParseResult:
    imports: list[DummyGoImport]


class SampleGoAdapter(GoFrameworkAdapter):
    name = "langchaingo"
    module_path = "github.com/tmc/langchaingo"


class TestGoFrameworkAdapterBase:
    """Unit tests for GoFrameworkAdapter base functionality."""

    def test_can_handle_matching_set(self) -> None:
        adapter = SampleGoAdapter()
        imports = {"github.com/tmc/langchaingo/llms", "fmt", "os"}
        assert adapter.can_handle(imports) is True

    def test_can_handle_non_matching_set(self) -> None:
        adapter = SampleGoAdapter()
        imports = {"github.com/gin-gonic/gin", "fmt"}
        assert adapter.can_handle(imports) is False

    def test_can_handle_parse_result_object(self) -> None:
        adapter = SampleGoAdapter()
        parse_res = DummyGoParseResult(
            imports=[DummyGoImport(path="github.com/tmc/langchaingo/vectorstores")]
        )
        assert adapter.can_handle(parse_res) is True

    def test_can_handle_invalid_input(self) -> None:
        adapter = SampleGoAdapter()
        assert adapter.can_handle(12345) is False  # type: ignore[arg-type]

    def test_clean_string_literals(self) -> None:
        adapter = SampleGoAdapter()
        assert adapter._clean('"hello world"') == "hello world"
        assert adapter._clean("`raw string`") == "raw string"
        assert adapter._clean(" plain text ") == "plain text"

    def test_resolve_variables(self) -> None:
        adapter = SampleGoAdapter()
        var_map = {"myVar": "gpt-4o"}
        assert adapter._resolve("myVar", var_map) == "gpt-4o"
        assert adapter._resolve('"literal"', var_map) == "literal"

    def test_template_vars_extraction(self) -> None:
        adapter = SampleGoAdapter()
        prompt = "Hello {{ name }}, welcome to {{ location }}!"
        vars_found = adapter._template_vars(prompt)
        assert vars_found == ["name", "location"]

    def test_framework_node_generation(self) -> None:
        adapter = SampleGoAdapter()
        node = adapter._fw_node("main.go")
        assert node.component_type == ComponentType.FRAMEWORK
        assert node.canonical_name == "framework:langchaingo"
        assert node.display_name == "Langchaingo"
        assert node.metadata["language"] == "golang"

    def test_extract_returns_framework_node_when_handled(self) -> None:
        adapter = SampleGoAdapter()
        parse_res = DummyGoParseResult(
            imports=[DummyGoImport(path="github.com/tmc/langchaingo")]
        )
        nodes = adapter.extract("package main", "main.go", parse_res)
        assert len(nodes) == 1
        assert nodes[0].component_type == ComponentType.FRAMEWORK

    def test_extract_returns_empty_when_not_handled(self) -> None:
        adapter = SampleGoAdapter()
        parse_res = DummyGoParseResult(imports=[DummyGoImport(path="net/http")])
        nodes = adapter.extract("package main", "main.go", parse_res)
        assert nodes == []
