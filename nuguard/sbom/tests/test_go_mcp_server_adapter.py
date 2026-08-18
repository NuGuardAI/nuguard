"Tests for the mcp-go framework adapter."

from __future__ import annotations

from pathlib import Path

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go import MCPGoServerAdapter
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType

_FIXTURES = Path(__file__).parent / "fixtures" / "apps"
_ADAPTER = MCPGoServerAdapter()


def _extract(source: str, file_path: str = "main.go") -> list[ComponentDetection]:
    return _ADAPTER.extract(source, file_path, parse_go(source, file_path))


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [item for item in detections if item.component_type == component_type]


def test_can_handle_mcp_go_module_and_subpackages() -> None:
    assert _ADAPTER.can_handle({"github.com/mark3labs/mcp-go"}) is True
    assert _ADAPTER.can_handle({"github.com/mark3labs/mcp-go/mcp"}) is True
    assert _ADAPTER.can_handle({"github.com/mark3labs/mcp-go/server"}) is True


def test_can_handle_rejects_lookalike_modules() -> None:
    assert _ADAPTER.can_handle({"github.com/mark3labs/mcp-go-extra/mcp"}) is False
    assert _ADAPTER.can_handle({"evilgithub.com/mark3labs/mcp-go/server"}) is False
    assert _ADAPTER.can_handle({"github.com/Mark3Labs/mcp-go/mcp"}) is False


def test_fixture_emits_framework_tools_and_registration_edges() -> None:
    fixture = _FIXTURES / "go_mcp_server" / "main.go"
    source = fixture.read_text(encoding="utf-8")

    detections = _extract(source, str(fixture))
    frameworks = _by_type(detections, ComponentType.FRAMEWORK)
    tools = _by_type(detections, ComponentType.TOOL)

    assert len(frameworks) == 1
    assert {tool.display_name for tool in tools} == {"get_weather", "search_docs"}
    assert {tool.canonical_name for tool in tools} == {
        "mcp_go_tool_get_weather",
        "mcp_go_tool_search_docs",
    }

    framework = frameworks[0]
    assert framework.canonical_name == "framework:mcp_go"
    assert framework.display_name == "security-tools"
    assert framework.metadata["framework"] == "mcp-go"
    assert framework.metadata["language"] == "golang"
    assert framework.metadata["server_name"] == "security-tools"
    assert framework.metadata["server_version"] == "1.0.0"
    assert framework.confidence == 0.95
    assert framework.evidence_kind == "ast_import"
    assert framework.line == 6
    assert framework.snippet == 'import "github.com/mark3labs/mcp-go/mcp"'

    relationships = framework.relationships
    assert len(relationships) == 2
    assert {relationship.target_canonical for relationship in relationships} == {
        "mcp_go_tool_get_weather",
        "mcp_go_tool_search_docs",
    }
    assert all(
        relationship.source_canonical == "framework:mcp_go"
        and relationship.source_type == ComponentType.FRAMEWORK
        and relationship.target_type == ComponentType.TOOL
        and relationship.relationship_type == "CALLS"
        for relationship in relationships
    )

    assert all(tool.confidence == 0.93 for tool in tools)
    assert all(tool.metadata["registered"] is True for tool in tools)
    assert all(tool.metadata["registration_method"] == "AddTool" for tool in tools)
    assert all(tool.metadata["server_variable"] == "srv" for tool in tools)
    assert all(tool.evidence_kind == "ast_instantiation" for tool in tools)


def test_alias_imports_are_supported() -> None:
    source = """package main

import (
    mcptypes "github.com/mark3labs/mcp-go/mcp"
    mcpserver "github.com/mark3labs/mcp-go/server"
)

func main() {
    service := mcpserver.NewMCPServer("aliased", "2.0.0")
    lookup := mcptypes.NewTool("lookup")
    service.AddTool(lookup, handler)
}
"""

    detections = _extract(source)
    framework = _by_type(detections, ComponentType.FRAMEWORK)[0]
    tool = _by_type(detections, ComponentType.TOOL)[0]

    assert framework.display_name == "aliased"
    assert framework.metadata["server_version"] == "2.0.0"
    assert tool.display_name == "lookup"
    assert tool.metadata["registered"] is True
    assert framework.relationships[0].target_canonical == tool.canonical_name


def test_unregistered_new_tool_is_emitted_without_relationship() -> None:
    source = """package main

import "github.com/mark3labs/mcp-go/mcp"

func main() {
    unused := mcp.NewTool("not_registered")
    _ = unused
}
"""

    detections = _extract(source)
    framework = _by_type(detections, ComponentType.FRAMEWORK)[0]
    tool = _by_type(detections, ComponentType.TOOL)[0]

    assert framework.display_name == "MCP Go"
    assert framework.relationships == []
    assert tool.display_name == "not_registered"
    assert tool.metadata["registered"] is False


def test_add_tool_registration_without_local_new_tool_uses_variable_name() -> None:
    source = """package main

import (
    "github.com/mark3labs/mcp-go/mcp"
    "github.com/mark3labs/mcp-go/server"
)

func buildTool() mcp.Tool {
    return mcp.Tool{Name: "external"}
}

func main() {
    srv := server.NewMCPServer("external-tools", "1.0.0")
    externalTool := buildTool()
    srv.AddTool(externalTool, handler)
}
"""

    detections = _extract(source)
    framework = _by_type(detections, ComponentType.FRAMEWORK)[0]
    tools = _by_type(detections, ComponentType.TOOL)

    assert len(tools) == 1
    assert tools[0].display_name == "externalTool"
    assert tools[0].canonical_name == "mcp_go_tool_externaltool"
    assert tools[0].metadata["name_source"] == "registered_variable"
    assert tools[0].metadata["registered"] is True
    assert tools[0].confidence == 0.78
    assert tools[0].evidence_kind == "ast_call"
    assert framework.relationships[0].target_canonical == tools[0].canonical_name


def test_known_server_receiver_filters_unrelated_add_tool_calls() -> None:
    source = """package main

import (
    "github.com/mark3labs/mcp-go/mcp"
    "github.com/mark3labs/mcp-go/server"
)

func main() {
    srv := server.NewMCPServer("filtered", "1.0.0")
    tool := mcp.NewTool("only_mcp")
    other.AddTool(tool, handler)
    _ = srv
}
"""

    detections = _extract(source)
    framework = _by_type(detections, ComponentType.FRAMEWORK)[0]
    tool = _by_type(detections, ComponentType.TOOL)[0]

    assert framework.relationships == []
    assert tool.metadata["registered"] is False


def test_extract_parses_content_when_parse_result_is_not_go_result() -> None:
    source = """package main
import "github.com/mark3labs/mcp-go/mcp"
func main() { tool := mcp.NewTool("fallback_parse"); _ = tool }
"""

    detections = _ADAPTER.extract(source, "main.go", None)

    assert _by_type(detections, ComponentType.TOOL)[0].display_name == "fallback_parse"


def test_lookalike_fixture_emits_no_detections() -> None:
    fixture = _FIXTURES / "go_mcp_server_negative" / "main.go"
    source = fixture.read_text(encoding="utf-8")

    assert _extract(source, str(fixture)) == []


def test_dynamic_unassigned_tool_name_is_not_emitted() -> None:
    source = """package main
import "github.com/mark3labs/mcp-go/mcp"
func build(name string) { mcp.NewTool(name) }
"""

    detections = _extract(source)

    assert len(_by_type(detections, ComponentType.FRAMEWORK)) == 1
    assert _by_type(detections, ComponentType.TOOL) == []
