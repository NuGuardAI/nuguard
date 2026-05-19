"""MCP protocol-level tests — verify all 7 tools are registered with correct metadata."""

from __future__ import annotations

import inspect

import pytest

from nuguard.mcp.server import mcp
from nuguard.mcp.__main__ import main

_EXPECTED_TOOLS = {
    "nuguard_init",
    "nuguard_sbom_generate",
    "nuguard_analyze",
    "nuguard_scan",
    "nuguard_behavior",
    "nuguard_redteam",
    "nuguard_policy_check",
}


@pytest.fixture
async def tools():
    return await mcp.list_tools()


@pytest.mark.asyncio
async def test_all_seven_tools_registered(tools) -> None:
    names = {t.name for t in tools}
    assert names == _EXPECTED_TOOLS, f"Missing: {_EXPECTED_TOOLS - names}, Extra: {names - _EXPECTED_TOOLS}"


@pytest.mark.asyncio
async def test_each_tool_has_description(tools) -> None:
    for tool in tools:
        assert tool.description, f"Tool '{tool.name}' has no description"
        assert len(tool.description) > 20, f"Tool '{tool.name}' description is too short"


@pytest.mark.asyncio
async def test_each_tool_has_input_schema(tools) -> None:
    for tool in tools:
        schema = tool.inputSchema
        assert schema is not None, f"Tool '{tool.name}' has no inputSchema"
        assert schema.get("type") == "object", f"Tool '{tool.name}' inputSchema type is not 'object'"


@pytest.mark.asyncio
async def test_timeout_seconds_in_all_tools(tools) -> None:
    for tool in tools:
        props = tool.inputSchema.get("properties", {})
        assert "timeout_seconds" in props, (
            f"Tool '{tool.name}' is missing 'timeout_seconds' parameter"
        )


@pytest.mark.asyncio
async def test_init_tool_has_project_dir(tools) -> None:
    init = next(t for t in tools if t.name == "nuguard_init")
    assert "project_dir" in init.inputSchema.get("properties", {})


@pytest.mark.asyncio
async def test_analyze_tool_has_sbom_param(tools) -> None:
    analyze = next(t for t in tools if t.name == "nuguard_analyze")
    assert "sbom" in analyze.inputSchema.get("properties", {})


@pytest.mark.asyncio
async def test_redteam_tool_has_profile_param(tools) -> None:
    redteam = next(t for t in tools if t.name == "nuguard_redteam")
    assert "profile" in redteam.inputSchema.get("properties", {})


# ---------------------------------------------------------------------------
# Transport — main() must call mcp.run with transport="stdio"
# ---------------------------------------------------------------------------

def test_main_calls_run_with_stdio_transport() -> None:
    """main() must explicitly request stdio transport so Smithery / Claude Code
    don't accidentally fall through to an SSE or HTTP listener."""
    from unittest.mock import patch, MagicMock
    mock_run = MagicMock()
    with patch("nuguard.mcp.server.mcp.run", mock_run):
        main()
    mock_run.assert_called_once_with(transport="stdio")
