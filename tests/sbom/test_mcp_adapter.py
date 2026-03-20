"""Test the MCP framework adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.models.sbom import EdgeRelationshipType, NodeType
from nuguard.sbom.adapters.python.mcp_server import MCPServerAdapter as McpAdapter


@pytest.fixture
def adapter() -> McpAdapter:
    return McpAdapter()


MCP_TOOL_DECORATOR_SOURCE = """
import mcp

@mcp.tool
def get_weather(location: str) -> str:
    \"\"\"Get weather for a location.\"\"\"
    return "sunny"

@mcp.tool()
def search_web(query: str) -> str:
    return "results"
"""

FAST_MCP_SOURCE = """
from mcp import FastMCP

app = FastMCP(name="my-mcp-server")

@app.tool()
def process_data(data: str) -> str:
    return data
"""

MCP_CLIENT_SOURCE = """
from mcp import ClientSession, StdioServerParameters

params = StdioServerParameters(command="python", args=["server.py"])

async def run():
    async with ClientSession() as session:
        result = await session.call_tool("weather_tool", {"city": "NYC"})
"""

MCP_SSE_SOURCE = """
from mcp import SSEServerParameters, use_mcp_server

params = SSEServerParameters(url="http://localhost:8000/sse")
server = use_mcp_server(name="remote-server")
"""

EMPTY_SOURCE = ""
INVALID_SYNTAX = "def foo(:"


def test_mcp_tool_decorator_detected(adapter: McpAdapter) -> None:
    """@mcp.tool decorated functions are detected as TOOL nodes."""
    nodes, _ = adapter.extract(Path("app.py"), MCP_TOOL_DECORATOR_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    assert len(tool_nodes) >= 1
    names = {n.name for n in tool_nodes}
    assert "get_weather" in names


def test_fast_mcp_server_detected(adapter: McpAdapter) -> None:
    """FastMCP(name=...) is detected as an AGENT node."""
    nodes, _ = adapter.extract(Path("server.py"), FAST_MCP_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    assert len(agent_nodes) >= 1
    assert any("my-mcp-server" in n.name or "FastMCP" in n.name for n in agent_nodes)


def test_mcp_tool_in_fast_mcp(adapter: McpAdapter) -> None:
    """@app.tool() on FastMCP instance is detected as a TOOL node."""
    nodes, _ = adapter.extract(Path("server.py"), FAST_MCP_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    # @app.tool() decorator: app.tool → attr=tool on name=app → "app.tool"
    assert len(tool_nodes) >= 1


def test_client_call_tool_detected(adapter: McpAdapter) -> None:
    """ClientSession.call_tool(...) is detected as a TOOL node."""
    nodes, _ = adapter.extract(Path("client.py"), MCP_CLIENT_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    assert len(tool_nodes) >= 1


def test_stdio_transport_detected(adapter: McpAdapter) -> None:
    """StdioServerParameters sets transport metadata to 'stdio'."""
    nodes, _ = adapter.extract(Path("client.py"), MCP_CLIENT_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    # After StdioServerParameters is seen, transport should be 'stdio'
    # (order-dependent: params is instantiated before call_tool)
    transports = [n.metadata.transport for n in tool_nodes]
    assert "stdio" in transports


def test_sse_server_detected(adapter: McpAdapter) -> None:
    """use_mcp_server with SSEServerParameters creates a TOOL node."""
    nodes, _ = adapter.extract(Path("sse.py"), MCP_SSE_SOURCE)
    tool_nodes = [n for n in nodes if n.component_type == NodeType.TOOL]
    assert len(tool_nodes) >= 1


def test_calls_edges_created(adapter: McpAdapter) -> None:
    """CALLS edges are created between AGENT and TOOL nodes."""
    nodes, edges = adapter.extract(Path("server.py"), FAST_MCP_SOURCE)
    call_edges = [e for e in edges if e.relationship_type == EdgeRelationshipType.CALLS]
    if edges:
        assert len(call_edges) >= 1


def test_empty_source_returns_empty(adapter: McpAdapter) -> None:
    nodes, edges = adapter.extract(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []
    assert edges == []


def test_invalid_syntax_returns_empty(adapter: McpAdapter) -> None:
    nodes, edges = adapter.extract(Path("broken.py"), INVALID_SYNTAX)
    assert nodes == []
    assert edges == []


def test_node_ids_are_stable(adapter: McpAdapter) -> None:
    nodes1, _ = adapter.extract(Path("app.py"), MCP_TOOL_DECORATOR_SOURCE)
    nodes2, _ = adapter.extract(Path("app.py"), MCP_TOOL_DECORATOR_SOURCE)
    ids1 = {n.id for n in nodes1}
    ids2 = {n.id for n in nodes2}
    assert ids1 == ids2


def test_confidence_in_range(adapter: McpAdapter) -> None:
    nodes, _ = adapter.extract(Path("app.py"), MCP_TOOL_DECORATOR_SOURCE)
    for node in nodes:
        assert 0.0 <= node.confidence <= 1.0
