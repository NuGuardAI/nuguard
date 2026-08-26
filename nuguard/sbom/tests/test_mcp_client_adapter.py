"""Unit tests for the MCPClientAdapter (untrusted, user-configured MCP servers).

Each test class targets one detection surface:

  TestCanHandle           — adapter activates on the right import prefixes
  TestServerDetection      — MCP_SERVER node from config model / client SDK usage
  TestDispatcherDetection  — TOOL node for the function driving the mcp client SDK
  TestRelationshipEdges    — TOOL -CALLS-> MCP_SERVER edge
  TestNegatives            — no false positives on unrelated code
  TestRedteamPickup        — the emitted TOOL node is picked up by existing
                              redteam scenario generation with no redteam changes
"""

from __future__ import annotations

from typing import Any

import pytest

from nuguard.sbom.adapters.python.mcp_client import MCPClientAdapter
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.types import ComponentType

_ADAPTER = MCPClientAdapter()


def _extract(code: str, file_path: str = "mcp_tool.py") -> list[Any]:
    pr = parse(code)
    return _ADAPTER.extract(code, file_path, pr)


def _by_type(detections: list[Any], ctype: ComponentType) -> list[Any]:
    return [d for d in detections if d.component_type == ctype]


_CONFIG_MODEL_CODE = (
    "from pydantic import BaseModel\n\n"
    "class MCPServerConfig(BaseModel):\n"
    "    name: str\n"
    "    url: str\n"
    "    enabled: bool = True\n"
)

_CLIENT_DISPATCHER_CODE = (
    "from mcp import ClientSession\n"
    "from mcp.client.sse import sse_client\n\n"
    "async def call_mcp_tool(server_url, tool_name, args):\n"
    "    async with sse_client(server_url) as (read, write):\n"
    "        async with ClientSession(read, write) as session:\n"
    "            return await session.call_tool(tool_name, args)\n"
)


class TestCanHandle:
    @pytest.mark.parametrize("imports", [{"mcp"}, {"mcp.client"}, {"mcp.client.sse"}])
    def test_activates_on_mcp_client_imports(self, imports: set[str]) -> None:
        assert _ADAPTER.can_handle(imports) is True

    def test_does_not_activate_on_unrelated_imports(self) -> None:
        assert _ADAPTER.can_handle({"flask", "pydantic"}) is False


class TestServerDetection:
    def test_config_model_alone_emits_mcp_server_node(self) -> None:
        detections = _extract(_CONFIG_MODEL_CODE, "mcp_manager.py")
        servers = _by_type(detections, ComponentType.MCP_SERVER)
        assert len(servers) == 1
        assert servers[0].metadata["trust_boundary"] == "user-configured/untrusted"

    def test_client_sdk_usage_emits_mcp_server_node(self) -> None:
        detections = _extract(_CLIENT_DISPATCHER_CODE)
        servers = _by_type(detections, ComponentType.MCP_SERVER)
        assert len(servers) == 1

    def test_both_signals_in_one_file_still_one_server_node(self) -> None:
        code = _CONFIG_MODEL_CODE + "\n" + _CLIENT_DISPATCHER_CODE
        detections = _extract(code)
        servers = _by_type(detections, ComponentType.MCP_SERVER)
        assert len(servers) == 1
        assert servers[0].confidence == 0.80

    def test_config_model_and_client_across_files_share_canonical_name(self) -> None:
        det_a = _extract(_CONFIG_MODEL_CODE, "mcp_manager.py")
        det_b = _extract(_CLIENT_DISPATCHER_CODE, "mcp_tool.py")
        canon_a = _by_type(det_a, ComponentType.MCP_SERVER)[0].canonical_name
        canon_b = _by_type(det_b, ComponentType.MCP_SERVER)[0].canonical_name
        assert canon_a == canon_b


class TestDispatcherDetection:
    def test_dispatcher_function_emits_tool_node(self) -> None:
        detections = _extract(_CLIENT_DISPATCHER_CODE)
        tools = _by_type(detections, ComponentType.TOOL)
        assert len(tools) == 1
        assert tools[0].metadata["trust_level"] == "untrusted"
        assert tools[0].metadata["mcp_server_url"] == "dynamic"

    def test_dispatcher_resolves_literal_url_when_present(self) -> None:
        code = (
            "from mcp.client.sse import sse_client\n\n"
            "async def connect():\n"
            "    async with sse_client('https://attacker.example/mcp') as (r, w):\n"
            "        pass\n"
        )
        detections = _extract(code)
        tools = _by_type(detections, ComponentType.TOOL)
        assert tools[0].metadata["mcp_server_url"] == "https://attacker.example/mcp"

    def test_no_client_call_no_tool_node(self) -> None:
        detections = _extract(_CONFIG_MODEL_CODE, "mcp_manager.py")
        assert _by_type(detections, ComponentType.TOOL) == []


class TestRelationshipEdges:
    def test_tool_calls_mcp_server(self) -> None:
        detections = _extract(_CLIENT_DISPATCHER_CODE)
        tool = _by_type(detections, ComponentType.TOOL)[0]
        server = _by_type(detections, ComponentType.MCP_SERVER)[0]
        assert len(tool.relationships) == 1
        hint = tool.relationships[0]
        assert hint.relationship_type == "CALLS"
        assert hint.source_canonical == tool.canonical_name
        assert hint.target_canonical == server.canonical_name


class TestNegatives:
    def test_unrelated_class_not_detected_as_config(self) -> None:
        code = (
            "from pydantic import BaseModel\n\n"
            "class UserProfile(BaseModel):\n"
            "    name: str\n"
            "    email: str\n"
        )
        assert _extract(code, "models.py") == []

    def test_mcp_import_without_client_symbols_or_config_model(self) -> None:
        code = "import mcp\n\nprint(mcp.__version__)\n"
        assert _extract(code) == []


class TestRedteamPickup:
    """Confirm the metadata contract this adapter writes (``trust_level``,
    ``mcp_server_url``) is exactly what the existing redteam scenario
    generators already key off of — i.e. no nuguard/redteam code changes are
    needed to pick up nodes this adapter emits.

    The adapter itself only detects TOOL/MCP_SERVER nodes and a TOOL-CALLS-
    MCP_SERVER edge; wiring an AGENT-CALLS-TOOL edge to the dispatcher is the
    surrounding app's own agent-framework adapter's job (out of scope here),
    so this test builds that one edge directly to isolate the pickup
    mechanism rather than depending on end-to-end extraction of an unrelated
    framework.
    """

    def test_mcp_toxic_flow_and_mcp_attack_scenarios_generated(self) -> None:
        import uuid

        from nuguard.redteam.scenarios.generator import ScenarioGenerator
        from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
        from nuguard.sbom.types import RelationshipType

        # Run the adapter for real to get its exact metadata shape, then wire
        # it into a minimal SBOM the way a real app's agent adapter would.
        detections = _extract(_CLIENT_DISPATCHER_CODE)
        tool_detection = _by_type(detections, ComponentType.TOOL)[0]

        agent_node = Node(
            id=uuid.uuid4(),
            name="chat_agent",
            component_type=ComponentType.AGENT,
            confidence=0.9,
            metadata=NodeMetadata(),
        )
        tool_node = Node(
            id=uuid.uuid4(),
            name=tool_detection.display_name,
            component_type=ComponentType.TOOL,
            confidence=tool_detection.confidence,
            metadata=NodeMetadata(
                trust_level=tool_detection.metadata["trust_level"],
                mcp_server_url=tool_detection.metadata["mcp_server_url"],
            ),
        )
        sink_node = Node(
            id=uuid.uuid4(),
            name="write_to_database",
            component_type=ComponentType.TOOL,
            confidence=0.9,
            metadata=NodeMetadata(high_privilege=True),
        )
        doc = AiSbomDocument(
            target="test://sample-app",
            nodes=[agent_node, tool_node, sink_node],
            edges=[Edge(source=agent_node.id, target=tool_node.id, relationship_type=RelationshipType.CALLS)],
        )

        scenarios = ScenarioGenerator(doc).generate()
        scenario_types = {s.chain.scenario_type.value if s.chain else None for s in scenarios}
        assert scenario_types & {"MCP_TOOL_INJECTION", "MCP_OUTPUT_POISONING"}
