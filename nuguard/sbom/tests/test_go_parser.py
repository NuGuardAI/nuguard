"""Tests for structured Go source parsing."""

from __future__ import annotations

import pytest

from nuguard.sbom.core import go_parser
from nuguard.sbom.core.go_parser import parse_go


def test_tree_sitter_parser_is_available_and_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert go_parser.HAS_TREE_SITTER is True
    assert go_parser.get_go_parser() is not None

    def fail_if_called(
        *_args: object,
        **_kwargs: object,
    ) -> object:
        raise AssertionError("regex fallback should not run")

    monkeypatch.setattr(
        go_parser,
        "_parse_with_regex",
        fail_if_called,
    )

    result = parse_go('package main\nimport "fmt"\n')

    assert result.used_tree_sitter is True
    assert result.parse_error is None
    assert [item.path for item in result.imports] == ["fmt"]


def test_extracts_single_and_grouped_imports_with_aliases() -> None:
    result = parse_go(
        """package main

import "fmt"
import (
    openai "github.com/sashabaranov/go-openai"
    _ "github.com/lib/pq"
    . "github.com/example/helpers"
)
"""
    )

    imports = {(item.path, item.alias) for item in result.imports}

    assert imports == {
        ("fmt", None),
        (
            "github.com/sashabaranov/go-openai",
            "openai",
        ),
        ("github.com/lib/pq", "_"),
        ("github.com/example/helpers", "."),
    }


def test_extracts_struct_literal_and_resolves_simple_values() -> None:
    result = parse_go(
        """package main

const model = "gpt-4o-mini"

func build() {
    request := openai.ChatCompletionRequest{
        Model: model,
        Temperature: 0.2,
        Stream: true,
    }
    _ = request
}
"""
    )

    request = next(
        item for item in result.instantiations if item.class_name == "openai.ChatCompletionRequest"
    )

    assert request.kind == "struct_literal"
    assert request.assigned_to == "request"
    assert request.args["Model"] == "gpt-4o-mini"
    assert request.args["Temperature"] == 0.2
    assert request.args["Stream"] is True


def test_extracts_constructor_and_method_calls() -> None:
    result = parse_go(
        """package main

func build(apiKey string, server *Server) {
    client, err := openai.NewClient(
        openai.WithAPIKey(apiKey),
    )
    server.AddTool(mcp.NewTool("search"))
    _, _ = client, err
}
"""
    )

    calls = {item.full_name: item for item in result.function_calls}

    constructors = {
        item.class_name: item for item in result.instantiations if item.kind == "constructor_call"
    }

    assert calls["openai.NewClient"].assigned_to == "client"
    assert calls["server.AddTool"].receiver == "server"
    assert calls["server.AddTool"].function_name == "AddTool"

    assert "openai.NewClient" in constructors
    assert constructors["openai.NewClient"].assigned_to == "client"

    assert "mcp.NewTool" in constructors


def test_extracts_raw_and_interpreted_strings_with_context() -> None:
    result = parse_go(
        r"""package main

import "fmt"

const systemPrompt = `You are a careful assistant.`

type Tagged struct {
    Name string `json:"name"`
}

func run() {
    prompt := "hello\nworld"
    fmt.Println(prompt)
}
"""
    )

    values = {item.value: item for item in result.string_literals}

    assert "fmt" not in values
    assert 'json:"name"' not in values

    assert values["You are a careful assistant."].is_raw is True

    assert values["You are a careful assistant."].assigned_to == "systemPrompt"

    assert values["hello\nworld"].is_raw is False
    assert values["hello\nworld"].context == "run"
    assert values["hello\nworld"].assigned_to == "prompt"


def test_malformed_source_returns_partial_result_with_error() -> None:
    result = parse_go(
        """package main

import "fmt"

func main( {
    prompt := "hello"
"""
    )

    assert result.used_tree_sitter is True
    assert result.parse_error is not None

    assert any(item.path == "fmt" for item in result.imports)


def test_regex_fallback_extracts_core_patterns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        go_parser,
        "get_go_parser",
        lambda: None,
    )

    result = parse_go(
        """package main

import (
    openai "github.com/sashabaranov/go-openai"
)

const model = "gpt-4o-mini"

func (server *Server) Register() {
    config := openai.ClientConfig{
        Model: model,
        Temperature: 0.2,
        Debug: true,
    }
    client, err := openai.NewClient(config)
    server.AddTool(mcp.NewTool(`search`))
    _, _ = client, err
}
"""
    )

    assert result.used_tree_sitter is False
    assert result.parse_error is None

    assert [(item.path, item.alias) for item in result.imports] == [
        (
            "github.com/sashabaranov/go-openai",
            "openai",
        )
    ]

    structs = {item.class_name: item for item in result.instantiations}

    calls = {item.full_name: item for item in result.function_calls}

    assert structs["openai.ClientConfig"].args == {
        "Model": "gpt-4o-mini",
        "Temperature": 0.2,
        "Debug": True,
    }

    assert structs["openai.ClientConfig"].assigned_to == "config"

    assert structs["openai.NewClient"].assigned_to == "client"

    assert "server.AddTool" in calls
    assert "mcp.NewTool" in calls

    # A method declaration is not a function call.
    assert "server.Register" not in calls

    assert any(item.value == "search" and item.is_raw for item in result.string_literals)


def test_empty_source_returns_empty_result() -> None:
    result = parse_go(
        "",
        file_path="empty.go",
    )

    assert result.file_path == "empty.go"
    assert not result
    assert result.parse_error is None
