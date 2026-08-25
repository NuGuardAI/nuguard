"""Unit tests for the OpenAIFunctionSchemaAdapter.

Each test class targets one detection surface:

  TestCanHandle        — adapter activates on the right import prefixes
  TestToolDetection     — TOOL nodes from hand-built {"type": "function", ...} dicts
  TestDispatcherLookup  — evidence points at the dispatch implementation when found
  TestRelationshipEdges — FRAMEWORK -CALLS-> TOOL edges
  TestNegatives         — no false positives on unrelated dict literals
"""

from __future__ import annotations

from typing import Any

import pytest

from nuguard.sbom.adapters.base import RelationshipHint
from nuguard.sbom.adapters.python.openai_function_schema import OpenAIFunctionSchemaAdapter
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.types import ComponentType

_ADAPTER = OpenAIFunctionSchemaAdapter()


def _extract(code: str, file_path: str = "tools.py") -> list[Any]:
    pr = parse(code)
    return _ADAPTER.extract(code, file_path, pr)


def _tool_nodes(detections: list[Any]) -> list[Any]:
    return [d for d in detections if d.component_type == ComponentType.TOOL]


def _framework_nodes(detections: list[Any]) -> list[Any]:
    return [d for d in detections if d.component_type == ComponentType.FRAMEWORK]


def _all_hints(detections: list[Any]) -> list[RelationshipHint]:
    hints: list[RelationshipHint] = []
    for d in detections:
        hints.extend(d.relationships)
    return hints


class TestCanHandle:
    @pytest.mark.parametrize("imports", [{"openai"}, {"openai.types"}, {"litellm"}])
    def test_activates_on_known_imports(self, imports: set[str]) -> None:
        assert _ADAPTER.can_handle(imports) is True

    def test_activates_regardless_of_imports(self) -> None:
        # Schema-definition files commonly have no openai/litellm import at
        # all (the LLM SDK import lives in the caller) — can_handle() is
        # intentionally unconditional; the dict-literal shape match in
        # extract() is what stays selective.
        assert _ADAPTER.can_handle({"flask", "requests"}) is True
        assert _ADAPTER.can_handle(set()) is True


class TestToolDetection:
    def test_single_tool_schema_dict_in_list(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "TOOLS = [\n"
            "    {\n"
            "        'type': 'function',\n"
            "        'function': {\n"
            "            'name': 'get_weather',\n"
            "            'description': 'Get current weather',\n"
            "            'parameters': {'type': 'object', 'properties': {}},\n"
            "        },\n"
            "    },\n"
            "]\n"
        )
        tools = _tool_nodes(_extract(code))
        assert len(tools) == 1
        assert tools[0].display_name == "get_weather"
        assert tools[0].metadata["description"] == "Get current weather"
        assert tools[0].metadata["parameters"] == {"type": "object", "properties": {}}
        assert tools[0].confidence == 0.85

    def test_multiple_tool_schemas(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "TOOLS = [\n"
            "    {'type': 'function', 'function': {'name': 'search_notes'}},\n"
            "    {'type': 'function', 'function': {'name': 'create_letter'}},\n"
            "]\n"
        )
        tools = _tool_nodes(_extract(code))
        assert {t.display_name for t in tools} == {"search_notes", "create_letter"}

    def test_duplicate_tool_name_deduped_within_file(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "TOOLS_A = [{'type': 'function', 'function': {'name': 'ping'}}]\n"
            "TOOLS_B = [{'type': 'function', 'function': {'name': 'ping'}}]\n"
        )
        tools = _tool_nodes(_extract(code))
        assert len(tools) == 1

    def test_canonical_names_distinguish_different_tools(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "TOOLS = [\n"
            "    {'type': 'function', 'function': {'name': 'tool_a'}},\n"
            "    {'type': 'function', 'function': {'name': 'tool_b'}},\n"
            "]\n"
        )
        tools = _tool_nodes(_extract(code))
        assert len({t.canonical_name for t in tools}) == 2


class TestDispatcherLookup:
    def test_if_elif_dispatcher_overrides_evidence_location(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "TOOLS = [{'type': 'function', 'function': {'name': 'get_weather'}}]\n\n"
            "def dispatch(tool_name, args):\n"
            "    if tool_name == 'get_weather':\n"
            "        return fetch_weather(args)\n"
        )
        tools = _tool_nodes(_extract(code))
        assert len(tools) == 1
        assert tools[0].line == 6  # the `if tool_name == 'get_weather':` line
        assert "get_weather" in tools[0].snippet

    def test_dict_dispatch_table_overrides_evidence_location(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "TOOLS = [{'type': 'function', 'function': {'name': 'get_weather'}}]\n\n"
            "DISPATCH = {\n"
            "    'get_weather': fetch_weather,\n"
            "}\n"
        )
        tools = _tool_nodes(_extract(code))
        assert len(tools) == 1
        assert tools[0].line == 5  # the 'get_weather': fetch_weather entry line
        assert "fetch_weather" in tools[0].snippet

    def test_no_dispatcher_falls_back_to_schema_declaration_line(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "TOOLS = [{'type': 'function', 'function': {'name': 'orphan_tool'}}]\n"
        )
        tools = _tool_nodes(_extract(code))
        assert tools[0].line == 3


class TestRelationshipEdges:
    def test_framework_calls_each_tool(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "TOOLS = [\n"
            "    {'type': 'function', 'function': {'name': 'tool_a'}},\n"
            "    {'type': 'function', 'function': {'name': 'tool_b'}},\n"
            "]\n"
        )
        detections = _extract(code)
        fw_nodes = _framework_nodes(detections)
        assert len(fw_nodes) == 1
        hints = _all_hints(detections)
        calls_hints = [h for h in hints if h.relationship_type == "CALLS"]
        assert len(calls_hints) == 2
        for h in calls_hints:
            assert h.source_canonical == fw_nodes[0].canonical_name
            assert h.source_type == ComponentType.FRAMEWORK
            assert h.target_type == ComponentType.TOOL


class TestNegatives:
    def test_no_tools_no_framework_node(self) -> None:
        code = "from openai import OpenAI\n\nclient = OpenAI()\n"
        assert _extract(code) == []

    def test_dict_missing_function_key_ignored(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "config = {'type': 'function', 'not_function': {'name': 'x'}}\n"
        )
        assert _tool_nodes(_extract(code)) == []

    def test_dict_missing_name_ignored(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "config = {'type': 'function', 'function': {'description': 'no name here'}}\n"
        )
        assert _tool_nodes(_extract(code)) == []

    def test_unrelated_dict_shape_ignored(self) -> None:
        code = (
            "from openai import OpenAI\n\n"
            "headers = {'type': 'header', 'value': 'x-request-id'}\n"
        )
        assert _tool_nodes(_extract(code)) == []


class TestSchemaOnlyFileNoLlmImport:
    """Regression: a file that only defines OpenAI-style tool schema dicts,
    with no openai/litellm import at all (the LLM SDK import lives in the
    caller instead) — common in real apps that separate schema definitions
    from the code that calls chat.completions.create(tools=...)."""

    def test_detects_tools_with_no_llm_sdk_import(self) -> None:
        code = (
            "import logging\n\n"
            "def _get_built_in_tools():\n"
            "    return [\n"
            "        {\n"
            "            'type': 'function',\n"
            "            'function': {\n"
            "                'name': 'search_patient',\n"
            "                'description': 'Search for patients by name.',\n"
            "                'parameters': {'type': 'object', 'properties': {}},\n"
            "            },\n"
            "        },\n"
            "    ]\n"
        )
        tools = _tool_nodes(_extract(code, file_path="registry.py"))
        assert [t.display_name for t in tools] == ["search_patient"]

    def test_prefilter_skips_files_without_type_function_substrings(self) -> None:
        code = "import logging\n\ndef helper():\n    return {'a': 1}\n"
        assert _extract(code) == []
