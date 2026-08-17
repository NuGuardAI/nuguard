"""Tests for the GoFrameworkAdapter base class."""

from __future__ import annotations

import pytest

from nuguard.sbom.adapters.go._go_base import GoFrameworkAdapter
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType


class SampleGoAdapter(GoFrameworkAdapter):
    name = "langchaingo"
    handles_imports = ["github.com/tmc/langchaingo"]


@pytest.mark.parametrize(
    "module_path",
    [
        "github.com/tmc/langchaingo",
        "github.com/tmc/langchaingo/llms",
    ],
)
def test_can_handle_real_parse_result_for_exact_module_and_subpackage(
    module_path: str,
) -> None:
    adapter = SampleGoAdapter()
    result = parse_go(f'package main\nimport "{module_path}"\n', "main.go")

    assert adapter.can_handle(result) is True


@pytest.mark.parametrize(
    "module_path",
    [
        "github.com/tmc/langchaingo-malicious",
        "github.com/tmc/langchaingo2",
        "evilgithub.com/tmc/langchaingo",
        "github.com/TMC/langchaingo",
    ],
)
def test_can_handle_rejects_lookalikes_and_case_changes(module_path: str) -> None:
    adapter = SampleGoAdapter()

    assert adapter.can_handle({module_path}) is False


def test_can_handle_accepts_raw_import_collections() -> None:
    adapter = SampleGoAdapter()

    assert adapter.can_handle(["fmt", "github.com/tmc/langchaingo/chains"]) is True
    assert adapter.can_handle(1234) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('"gpt-4o"', "gpt-4o"),
        ("`gpt-4o-mini`", "gpt-4o-mini"),
        (" 'claude-sonnet' ", "claude-sonnet"),
        (" plain ", "plain"),
        (None, ""),
        (42, ""),
        (True, ""),
        (["gpt-4o"], ""),
        ({"model": "gpt-4o"}, ""),
        ("$runtimeModel", ""),
    ],
)
def test_clean_handles_parser_value_types(value: object, expected: str) -> None:
    assert SampleGoAdapter._clean(value) == expected


def test_resolve_uses_real_named_and_positional_parser_arguments() -> None:
    result = parse_go(
        """package main

const modelName = "gpt-4o-mini"

func build() {
    request := openai.ChatCompletionRequest{
        Model: modelName,
        Temperature: 1,
        Stream: true,
        Metadata: map[string]string{"team": "security"},
        Optional: nil,
    }
    client := openai.NewClient(modelName, true, 5)
    unresolved := openai.ChatCompletionRequest{Model: runtimeModel}
    _, _, _ = request, client, unresolved
}
""",
        "main.go",
    )
    adapter = SampleGoAdapter()
    requests = [
        item for item in result.instantiations if item.class_name == "openai.ChatCompletionRequest"
    ]
    resolved_request = next(item for item in requests if item.args["Model"] == "gpt-4o-mini")
    unresolved_request = next(item for item in requests if item.args["Model"] == "$runtimeModel")
    constructor = next(
        item for item in result.instantiations if item.class_name == "openai.NewClient"
    )
    call = next(item for item in result.function_calls if item.full_name == "openai.NewClient")

    assert adapter._resolve(resolved_request, "Missing", "Model") == "gpt-4o-mini"
    assert adapter._resolve(constructor, 0) == "gpt-4o-mini"
    assert adapter._resolve(call, 0) == "gpt-4o-mini"
    assert adapter._resolve(resolved_request, "Temperature") == ""
    assert adapter._resolve(resolved_request, "Stream") == ""
    assert adapter._resolve(resolved_request, "Metadata") == ""
    assert adapter._resolve(resolved_request, "Optional") == ""
    assert adapter._resolve(unresolved_request, "Model") == ""
    assert adapter._resolve(constructor, -1) == ""
    assert adapter._resolve(constructor, 99) == ""


def test_framework_node_preserves_import_provenance() -> None:
    source = """package main

import (
    "github.com/tmc/langchaingo/llms"
)
"""
    adapter = SampleGoAdapter()
    result = parse_go(source, "main.go")
    matched_import = adapter._matching_import(result)

    assert matched_import is not None

    node = adapter._fw_node("main.go", matched_import)

    assert node.component_type == ComponentType.FRAMEWORK
    assert node.canonical_name == "framework:langchaingo"
    assert node.display_name == "Langchaingo"
    assert node.adapter_name == "langchaingo"
    assert node.file_path == "main.go"
    assert node.line == 4
    assert node.snippet == 'import "github.com/tmc/langchaingo/llms"'
    assert node.evidence_kind == "ast_import"
    assert node.metadata["framework"] == "langchaingo"
    assert node.metadata["language"] == "golang"


def test_framework_node_preserves_import_alias() -> None:
    adapter = SampleGoAdapter()
    result = parse_go(
        'package main\nimport lc "github.com/tmc/langchaingo"\n',
        "main.go",
    )
    matched_import = adapter._matching_import(result)

    assert matched_import is not None

    node = adapter._fw_node("main.go", matched_import)

    assert node.line == 2
    assert node.snippet == 'import lc "github.com/tmc/langchaingo"'


def test_template_vars_support_common_go_and_prompt_forms() -> None:
    template = (
        "Hello {{ .Name }} from ${region}; input={input}; "
        "owner={{ $owner }}; nested={{ .User.Name }}; again={{ .Name }}"
    )

    assert SampleGoAdapter._template_vars(template) == [
        "Name",
        "region",
        "input",
        "owner",
        "User.Name",
    ]


def test_extract_remains_unimplemented() -> None:
    adapter = SampleGoAdapter()
    result = parse_go(
        'package main\nimport "github.com/tmc/langchaingo"\n',
        "main.go",
    )

    with pytest.raises(NotImplementedError):
        adapter.extract(result.source, result.file_path, result)
