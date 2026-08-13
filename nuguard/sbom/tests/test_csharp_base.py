"""Tests for the shared C# framework-adapter base class."""

from __future__ import annotations

import inspect
from typing import Any

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.csharp import CSharpFrameworkAdapter
from nuguard.sbom.core.csharp_parser import (
    CSharpParseResult,
    parse_csharp,
)


class _ExampleAdapter(CSharpFrameworkAdapter):
    name = "example_csharp"
    priority = 25

    handles_namespaces = [
        "Azure.AI.OpenAI",
        "Microsoft.SemanticKernel",
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

        if not self._detect(result):
            return []

        line = result.using_directives[0].line if result.using_directives else 0

        return [
            self._fw_node(
                file_path,
                line,
            )
        ]


def test_base_class_is_abstract() -> None:
    assert inspect.isabstract(CSharpFrameworkAdapter)


def test_can_handle_exact_descendant_and_global_namespace() -> None:
    adapter = _ExampleAdapter()

    assert adapter.can_handle({"Azure.AI.OpenAI"})
    assert adapter.can_handle({"Azure.AI.OpenAI.Chat"})
    assert adapter.can_handle({"global::Microsoft.SemanticKernel.Connectors.OpenAI"})


def test_can_handle_rejects_siblings_and_substrings() -> None:
    adapter = _ExampleAdapter()

    assert not adapter.can_handle({"Azure.AI"})
    assert not adapter.can_handle({"Contoso.Azure.AI.OpenAI"})
    assert not adapter.can_handle({"Microsoft.SemanticKernelish"})


def test_detect_and_extract_use_structural_parse_result() -> None:
    adapter = _ExampleAdapter()

    source = "using Azure.AI.OpenAI;\npublic class App {}\n"

    result = parse_csharp(
        source,
        "App.cs",
    )

    assert adapter._detect(result)

    detections = adapter.extract(
        source,
        "App.cs",
        result,
    )

    assert len(detections) == 1
    assert detections[0].file_path == "App.cs"
    assert detections[0].line == 1


def test_parse_result_reuses_typed_result_or_parses_content() -> None:
    existing = CSharpParseResult(file_path="Existing.cs")

    assert (
        _ExampleAdapter._parse_result(
            "ignored",
            "Other.cs",
            existing,
        )
        is existing
    )

    parsed = _ExampleAdapter._parse_result(
        "using Microsoft.SemanticKernel;",
        "Kernel.cs",
        None,
    )

    assert parsed.file_path == "Kernel.cs"
    assert parsed.using_directives[0].namespace == "Microsoft.SemanticKernel"


def test_framework_node_uses_csharp_metadata() -> None:
    node = _ExampleAdapter()._fw_node(
        "Program.cs",
        7,
    )

    assert node.canonical_name == "framework:example_csharp"
    assert node.adapter_name == "example_csharp"
    assert node.priority == 25
    assert node.metadata == {
        "framework": "example_csharp",
        "language": "csharp",
    }
    assert node.evidence_kind == "ast_import"


def test_common_helpers_clean_assignments_and_template_variables() -> None:
    assert _ExampleAdapter._clean('$"gpt-4o"') == "gpt-4o"

    assert _ExampleAdapter._clean("$(ModelName)") == ""

    assert (
        _ExampleAdapter._assignment_name(
            'var client = new AzureOpenAIClient("endpoint");',
            1,
        )
        == "client"
    )

    assert _ExampleAdapter._template_vars(
        "Hello {user.Name}; total {amount:N2}; escaped {{literal}}"
    ) == [
        "user.Name",
        "amount",
    ]
