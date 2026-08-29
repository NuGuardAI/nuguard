"""Tests for the Go agent/orchestration framework adapters (phase 7):
eino, genkit-go, and the mcp-go client adapter."""

from __future__ import annotations

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go import EinoAdapter, GenkitGoAdapter, MCPGoClientAdapter
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [item for item in detections if item.component_type == component_type]


def _extract(adapter, source: str, file_path: str = "main.go") -> list[ComponentDetection]:
    return adapter.extract(source, file_path, parse_go(source, file_path))


# ---------------------------------------------------------------------------
# eino
# ---------------------------------------------------------------------------

_EINO_CHAIN_SRC = """
package main

import "github.com/cloudwego/eino/compose"

func run() {
	chain := compose.NewChain[string, string]()
	_ = chain
}
"""


def test_eino_chain_construction_emits_framework_and_agent() -> None:
    detections = _extract(EinoAdapter(), _EINO_CHAIN_SRC)
    frameworks = _by_type(detections, ComponentType.FRAMEWORK)
    agents = _by_type(detections, ComponentType.AGENT)
    assert len(frameworks) == 1
    assert len(agents) == 1
    assert agents[0].display_name == "chain"
    assert agents[0].relationships


_EINO_TOOL_SRC = """
package main

import (
	"context"

	"github.com/cloudwego/eino/components/tool/utils"
	"github.com/cloudwego/eino/schema"
)

func run() {
	toolInfo := &schema.ToolInfo{
		Name: "process_name",
		Desc: "processes and returns a user name",
	}
	tl := utils.NewTool[*Input, *Output](
		toolInfo,
		func(ctx context.Context, input *Input) (*Output, error) {
			return nil, nil
		},
	)
	_ = tl
}
"""


def test_eino_tool_reads_name_and_desc_via_symbol_resolution() -> None:
    detections = _extract(EinoAdapter(), _EINO_TOOL_SRC)
    tools = _by_type(detections, ComponentType.TOOL)
    assert len(tools) == 1
    assert tools[0].display_name == "process_name"
    assert tools[0].metadata["description"] == "processes and returns a user name"


def test_eino_tool_with_unresolvable_info_is_skipped() -> None:
    src = """
package main

import "github.com/cloudwego/eino/components/tool/utils"

func run(externalInfo *schema.ToolInfo) {
	tl := utils.NewTool[*Input, *Output](externalInfo, someFunc)
	_ = tl
}
"""
    detections = _extract(EinoAdapter(), src)
    assert _by_type(detections, ComponentType.TOOL) == []
    assert len(_by_type(detections, ComponentType.FRAMEWORK)) == 1


def test_eino_no_op_without_matching_import() -> None:
    src = """
package main

func main() {
	println("no eino here")
}
"""
    assert _extract(EinoAdapter(), src) == []


# ---------------------------------------------------------------------------
# genkit-go
# ---------------------------------------------------------------------------

_GENKIT_FLOW_SRC = """
package main

import "github.com/firebase/genkit/go/genkit"

func run(g *genkit.Genkit) {
	genkit.DefineFlow(g, "summarizeFlow", summarize)
}
"""


def test_genkit_define_flow_emits_agent_node() -> None:
    detections = _extract(GenkitGoAdapter(), _GENKIT_FLOW_SRC)
    agents = _by_type(detections, ComponentType.AGENT)
    assert len(agents) == 1
    assert agents[0].display_name == "summarizeFlow"


_GENKIT_TOOL_SRC = """
package main

import "github.com/firebase/genkit/go/genkit"

func run(g *genkit.Genkit) {
	genkit.DefineTool(g, "getWeather", "Gets the current weather in a given location", weatherFunc)
}
"""


def test_genkit_define_tool_emits_tool_node_with_description() -> None:
    detections = _extract(GenkitGoAdapter(), _GENKIT_TOOL_SRC)
    tools = _by_type(detections, ComponentType.TOOL)
    assert len(tools) == 1
    assert tools[0].display_name == "getWeather"
    assert tools[0].metadata["description"] == "Gets the current weather in a given location"


def test_genkit_no_op_without_matching_import() -> None:
    src = """
package main

func main() {
	println("no genkit here")
}
"""
    assert _extract(GenkitGoAdapter(), src) == []


# ---------------------------------------------------------------------------
# mcp-go client
# ---------------------------------------------------------------------------

_MCP_STDIO_SRC = """
package main

import "github.com/mark3labs/mcp-go/client"

func run() {
	c, err := client.NewStdioMCPClient("go", []string{}, "run", "/path/to/server/main.go")
	_ = c
	_ = err
}
"""


def test_mcp_stdio_client_emits_untrusted_tool_and_mcp_server_node() -> None:
    detections = _extract(MCPGoClientAdapter(), _MCP_STDIO_SRC)
    servers = _by_type(detections, ComponentType.MCP_SERVER)
    tools = _by_type(detections, ComponentType.TOOL)
    assert len(servers) == 1
    assert len(tools) == 1
    assert tools[0].metadata["trust_level"] == "untrusted"
    assert tools[0].metadata["mcp_server_url"] == "dynamic"
    assert tools[0].relationships[0].target_canonical == servers[0].canonical_name


_MCP_SSE_SRC = """
package main

import "github.com/mark3labs/mcp-go/client"

func run() {
	c, err := client.NewSSEMCPClient("https://attacker-controlled.example.com/mcp")
	_ = c
	_ = err
}
"""


def test_mcp_sse_client_resolves_server_url() -> None:
    detections = _extract(MCPGoClientAdapter(), _MCP_SSE_SRC)
    tools = _by_type(detections, ComponentType.TOOL)
    assert len(tools) == 1
    assert tools[0].metadata["mcp_server_url"] == "https://attacker-controlled.example.com/mcp"


def test_mcp_in_process_client_is_not_flagged_as_untrusted() -> None:
    src = """
package main

import "github.com/mark3labs/mcp-go/client"

func run(srv *server.MCPServer) {
	c, err := client.NewInProcessClient(srv)
	_ = c
	_ = err
}
"""
    detections = _extract(MCPGoClientAdapter(), src)
    # Import-gated MCP_SERVER node still appears, but no TOOL node for an
    # in-process (developer-controlled) client.
    assert len(_by_type(detections, ComponentType.MCP_SERVER)) == 1
    assert _by_type(detections, ComponentType.TOOL) == []


def test_mcp_client_no_op_without_matching_import() -> None:
    src = """
package main

func main() {
	println("no mcp client here")
}
"""
    assert _extract(MCPGoClientAdapter(), src) == []
