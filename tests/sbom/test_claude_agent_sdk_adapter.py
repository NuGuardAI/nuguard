"""Tests for the Claude Agent SDK SBOM adapters (Python + TypeScript)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.python.claude_agent_sdk import ClaudeAgentSDKAdapter
from nuguard.sbom.adapters.typescript.claude_agent_sdk import ClaudeAgentSDKTSAdapter
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.core.ts_parser import parse_typescript
from nuguard.sbom.types import ComponentType

_PY_ADAPTER = ClaudeAgentSDKAdapter()
_TS_ADAPTER = ClaudeAgentSDKTSAdapter()

CHIEF_OF_STAFF_DIR = (
    Path(__file__).parent.parent
    / "apps"
    / "claude-cookbooks"
    / "claude_agent_sdk"
    / "chief_of_staff_agent"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _py_extract(code: str, file_path: str = "agent.py") -> list[ComponentDetection]:
    pr = parse(code)
    return _PY_ADAPTER.extract(code, file_path, pr)


def _ts_extract(code: str, file_path: str = "agent.ts") -> list[ComponentDetection]:
    pr = parse_typescript(code, file_path)
    return _TS_ADAPTER.extract(code, file_path, pr)


def _by_type(dets: list[ComponentDetection], ct: ComponentType) -> list[ComponentDetection]:
    return [d for d in dets if d.component_type == ct]


def _all_rel_hints(dets: list[ComponentDetection]) -> list[Any]:
    return [r for d in dets for r in d.relationships]


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


class TestCanHandle:
    def test_py_matches_claude_agent_sdk(self):
        assert _PY_ADAPTER.can_handle({"claude_agent_sdk"})

    def test_py_matches_submodule(self):
        assert _PY_ADAPTER.can_handle({"claude_agent_sdk.client"})

    def test_py_no_match_on_other_frameworks(self):
        assert not _PY_ADAPTER.can_handle({"openai", "langchain", "anthropic"})

    def test_ts_matches_npm_package(self):
        assert _TS_ADAPTER.can_handle({"@anthropic-ai/claude-code"})

    def test_ts_no_match_on_bare_anthropic(self):
        assert not _TS_ADAPTER.can_handle({"@anthropic-ai/sdk"})

    def test_ts_no_match_on_unrelated(self):
        assert not _TS_ADAPTER.can_handle({"openai", "langchain"})


# ---------------------------------------------------------------------------
# Python adapter — FRAMEWORK node
# ---------------------------------------------------------------------------


class TestPyFrameworkNode:
    def test_emits_framework_node(self):
        code = "from claude_agent_sdk import ClaudeSDKClient\n"
        dets = _py_extract(code)
        fw = _by_type(dets, ComponentType.FRAMEWORK)
        assert len(fw) == 1
        assert fw[0].canonical_name == "framework:claude_agent_sdk"
        assert fw[0].adapter_name == "claude_agent_sdk"
        assert fw[0].confidence == pytest.approx(0.95)

    def test_none_parse_result_returns_empty(self):
        assert _PY_ADAPTER.extract("", "x.py", None) == []


# ---------------------------------------------------------------------------
# Python adapter — ClaudeAgentOptions → MODEL
# ---------------------------------------------------------------------------


class TestPyModel:
    def test_detects_model_from_options(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions
options = ClaudeAgentOptions(model="claude-opus-4-6", allowed_tools=["Read"])
"""
        dets = _py_extract(code)
        models = _by_type(dets, ComponentType.MODEL)
        assert len(models) == 1
        assert models[0].display_name == "claude-opus-4-6"
        assert models[0].metadata["provider"] == "anthropic"
        assert models[0].metadata["framework"] == "claude_agent_sdk"

    def test_no_model_when_not_specified(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions
options = ClaudeAgentOptions(allowed_tools=["Read"])
"""
        dets = _py_extract(code)
        assert not _by_type(dets, ComponentType.MODEL)

    def test_sonnet_model_detected(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions
opts = ClaudeAgentOptions(model="claude-sonnet-4-6")
"""
        dets = _py_extract(code)
        models = _by_type(dets, ComponentType.MODEL)
        assert any("sonnet" in m.display_name.lower() for m in models)


# ---------------------------------------------------------------------------
# Python adapter — ClaudeAgentOptions → PROMPT
# ---------------------------------------------------------------------------


class TestPyPrompt:
    def test_detects_long_system_prompt(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions
options = ClaudeAgentOptions(
    model="claude-opus-4-6",
    system_prompt="You are the Chief of Staff for TechStart Inc, a 50-person startup.",
)
"""
        dets = _py_extract(code)
        prompts = _by_type(dets, ComponentType.PROMPT)
        assert len(prompts) == 1
        assert prompts[0].metadata["role"] == "system"
        assert len(prompts[0].metadata["content"]) >= 40

    def test_skips_short_prompt(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions
options = ClaudeAgentOptions(system_prompt="Hello")
"""
        dets = _py_extract(code)
        assert not _by_type(dets, ComponentType.PROMPT)

    def test_prompt_display_name_uses_var_name(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions
my_options = ClaudeAgentOptions(
    system_prompt="You are an expert financial analyst with deep knowledge of startup metrics."
)
"""
        dets = _py_extract(code)
        prompts = _by_type(dets, ComponentType.PROMPT)
        assert len(prompts) == 1
        assert "my_options" in prompts[0].display_name.lower()


# ---------------------------------------------------------------------------
# Python adapter — ClaudeAgentOptions → TOOL nodes
# ---------------------------------------------------------------------------


class TestPyTools:
    def test_detects_allowed_tools(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions
options = ClaudeAgentOptions(allowed_tools=["Task", "Read", "Write", "Edit", "Bash"])
"""
        dets = _py_extract(code)
        tools = _by_type(dets, ComponentType.TOOL)
        tool_names = {t.display_name for t in tools}
        assert {"Task", "Read", "Write", "Edit", "Bash"}.issubset(tool_names)

    def test_websearch_tool(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions
options = ClaudeAgentOptions(allowed_tools=["WebSearch"])
"""
        dets = _py_extract(code)
        tools = _by_type(dets, ComponentType.TOOL)
        assert any(t.display_name == "WebSearch" for t in tools)

    def test_tools_metadata(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions
options = ClaudeAgentOptions(allowed_tools=["Read"])
"""
        dets = _py_extract(code)
        tools = _by_type(dets, ComponentType.TOOL)
        assert tools[0].metadata["tool_source"] == "allowed_tools"
        assert tools[0].metadata["framework"] == "claude_agent_sdk"


# ---------------------------------------------------------------------------
# Python adapter — ClaudeAgentOptions → MCP_SERVER
# ---------------------------------------------------------------------------


class TestPyMcpServers:
    def test_detects_mcp_servers_configured(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions, McpServerConfig
options = ClaudeAgentOptions(
    model="claude-opus-4-6",
    mcp_servers={"github": McpServerConfig(command="docker", args=["run", "..."])},
)
"""
        dets = _py_extract(code)
        mcp = _by_type(dets, ComponentType.MCP_SERVER)
        assert len(mcp) >= 1
        assert mcp[0].metadata.get("mcp_servers_configured") is True


# ---------------------------------------------------------------------------
# Python adapter — ClaudeSDKClient → AGENT
# ---------------------------------------------------------------------------


class TestPyClient:
    def test_detects_agent_from_sdk_client(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
options = ClaudeAgentOptions(
    model="claude-opus-4-6",
    system_prompt="You are an assistant for data analysis with Python and SQL expertise.",
    allowed_tools=["Read", "Bash"],
)
async with ClaudeSDKClient(options=options) as agent:
    await agent.query(prompt="analyze this")
"""
        dets = _py_extract(code)
        agents = _by_type(dets, ComponentType.AGENT)
        assert len(agents) >= 1
        assert all(a.adapter_name == "claude_agent_sdk" for a in agents)

    def test_agent_has_model_relationship(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
options = ClaudeAgentOptions(model="claude-opus-4-6")
async with ClaudeSDKClient(options=options) as agent:
    pass
"""
        dets = _py_extract(code)
        uses = [
            r for r in _all_rel_hints(dets)
            if r.relationship_type == "USES" and r.target_type == ComponentType.MODEL
        ]
        assert len(uses) >= 1

    def test_agent_has_tool_calls_relationships(self):
        code = """
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
options = ClaudeAgentOptions(allowed_tools=["Read", "Write"])
async with ClaudeSDKClient(options=options) as agent:
    pass
"""
        dets = _py_extract(code)
        calls = [
            r for r in _all_rel_hints(dets)
            if r.relationship_type == "CALLS" and r.target_type == ComponentType.TOOL
        ]
        assert len(calls) >= 2  # Read + Write


# ---------------------------------------------------------------------------
# Python adapter — standalone query() → AGENT
# ---------------------------------------------------------------------------


class TestPyQueryFunction:
    def test_detects_query_agent(self):
        code = """
from claude_agent_sdk import query, ClaudeAgentOptions
options = ClaudeAgentOptions(model="claude-sonnet-4-6")
result = await query(prompt="do something", options=options)
"""
        dets = _py_extract(code)
        agents = _by_type(dets, ComponentType.AGENT)
        assert len(agents) >= 1
        assert any(a.metadata.get("is_oneshot") for a in agents)

    def test_skips_method_calls(self):
        # agent.query() should not produce a second standalone agent node
        code = """
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
options = ClaudeAgentOptions(model="claude-opus-4-6")
async with ClaudeSDKClient(options=options) as agent:
    await agent.query(prompt="hello")
"""
        dets = _py_extract(code)
        oneshot = [a for a in _by_type(dets, ComponentType.AGENT) if a.metadata.get("is_oneshot")]
        assert len(oneshot) == 0


# ---------------------------------------------------------------------------
# Python adapter — AgentDefinition → subagent AGENT nodes
# ---------------------------------------------------------------------------


class TestPyAgentDefinition:
    def test_detects_subagents(self):
        code = """
from claude_agent_sdk import AgentDefinition
agents = [
    AgentDefinition(name="financial-analyst", description="Finance expert", tools=["Read", "Bash"]),
    AgentDefinition(name="recruiter", description="HR expert", tools=["WebSearch"]),
]
"""
        dets = _py_extract(code)
        agents = _by_type(dets, ComponentType.AGENT)
        names = {a.display_name for a in agents}
        assert "financial-analyst" in names
        assert "recruiter" in names

    def test_subagents_have_is_subagent_flag(self):
        code = """
from claude_agent_sdk import AgentDefinition
a = AgentDefinition(name="specialist", description="Domain expert", tools=["Read"])
"""
        dets = _py_extract(code)
        agents = _by_type(dets, ComponentType.AGENT)
        assert all(a.metadata.get("is_subagent") is True for a in agents)

    def test_subagent_tool_relationships(self):
        code = """
from claude_agent_sdk import AgentDefinition
a = AgentDefinition(name="analyst", description="Analyst", tools=["Read", "Bash"])
"""
        dets = _py_extract(code)
        calls = [
            r for r in _all_rel_hints(dets)
            if r.relationship_type == "CALLS" and r.target_type == ComponentType.TOOL
        ]
        assert len(calls) >= 2


# ---------------------------------------------------------------------------
# Python adapter — negatives
# ---------------------------------------------------------------------------


class TestPyNegatives:
    def test_can_handle_rejects_non_claude_imports(self):
        # The extractor gates adapter execution via can_handle(); verify it rejects
        # non-claude imports so this adapter never runs on openai-agents code.
        code = "from agents import Agent\nagent = Agent(name='test')\n"
        pr = parse(code)
        assert not _PY_ADAPTER.can_handle({imp.module for imp in pr.imports})

    def test_none_parse_result_returns_empty(self):
        assert _PY_ADAPTER.extract("", "x.py", None) == []


# ---------------------------------------------------------------------------
# TypeScript adapter — FRAMEWORK node
# ---------------------------------------------------------------------------


class TestTSFrameworkNode:
    def test_emits_framework_node(self):
        code = "import { query } from '@anthropic-ai/claude-code';\n"
        dets = _ts_extract(code)
        fw = _by_type(dets, ComponentType.FRAMEWORK)
        assert len(fw) == 1
        assert fw[0].canonical_name == "framework:claude_agent_sdk_ts"
        assert fw[0].metadata.get("language") == "typescript"

    def test_no_match_on_wrong_package(self):
        code = "import Anthropic from '@anthropic-ai/sdk';\n"
        dets = _ts_extract(code)
        claude_dets = [d for d in dets if d.adapter_name == "claude_agent_sdk_ts"]
        assert not claude_dets


# ---------------------------------------------------------------------------
# TypeScript adapter — query() function
# ---------------------------------------------------------------------------


class TestTSQuery:
    def test_detects_query_agent(self):
        code = """
import { query } from '@anthropic-ai/claude-code';
const result = query({ prompt: "do something useful here" });
"""
        dets = _ts_extract(code)
        agents = _by_type(dets, ComponentType.AGENT)
        assert len(agents) >= 1
        assert agents[0].adapter_name == "claude_agent_sdk_ts"

    def test_query_agent_metadata(self):
        code = """
import { query } from '@anthropic-ai/claude-code';
for await (const msg of query({ prompt: "research this" })) { }
"""
        dets = _ts_extract(code)
        agents = _by_type(dets, ComponentType.AGENT)
        assert any(a.metadata.get("agent_function") == "query" for a in agents)


# ---------------------------------------------------------------------------
# TypeScript adapter — new ClaudeCode({...}) instantiation
# ---------------------------------------------------------------------------


class TestTSInstantiation:
    def test_detects_claude_code_class(self):
        code = """
import { ClaudeCode } from '@anthropic-ai/claude-code';
const sdk = new ClaudeCode({ model: "claude-opus-4-6" });
"""
        dets = _ts_extract(code)
        agents = _by_type(dets, ComponentType.AGENT)
        assert len(agents) >= 1


# ---------------------------------------------------------------------------
# TypeScript adapter — negatives
# ---------------------------------------------------------------------------


class TestTSNegatives:
    def test_no_nodes_from_openai_code(self):
        code = """
import { Agent } from 'openai-agents';
const agent = new Agent({ name: "test", instructions: "help" });
"""
        dets = _ts_extract(code)
        claude_dets = [d for d in dets if d.adapter_name == "claude_agent_sdk_ts"]
        assert not claude_dets


# ---------------------------------------------------------------------------
# E2E integration test — chief_of_staff_agent
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not CHIEF_OF_STAFF_DIR.exists(),
    reason="chief_of_staff_agent test fixture not available",
)
def test_e2e_chief_of_staff_sbom():
    """Run the full SBOM extractor on the chief_of_staff_agent and assert key nodes."""
    from nuguard.sbom.extractor import AiSbomExtractor
    from nuguard.sbom.extractor.config import AiSbomConfig

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False)
    doc = AiSbomExtractor().extract_from_path(CHIEF_OF_STAFF_DIR, config)

    node_types = {n.component_type for n in doc.nodes}
    fw_names = {n.name for n in doc.nodes if n.component_type == ComponentType.FRAMEWORK}
    model_names = {n.name for n in doc.nodes if n.component_type == ComponentType.MODEL}
    agent_nodes = [n for n in doc.nodes if n.component_type == ComponentType.AGENT]
    tool_names = {n.name for n in doc.nodes if n.component_type == ComponentType.TOOL}
    prompt_nodes = [n for n in doc.nodes if n.component_type == ComponentType.PROMPT]

    # FRAMEWORK node for claude_agent_sdk must be present.
    # normalize_display_name() converts "framework:claude_agent_sdk" → "Claude Agent Sdk".
    assert any("claude" in fw.lower() and "agent" in fw.lower() for fw in fw_names), (
        f"Expected claude_agent_sdk FRAMEWORK node, got: {fw_names}"
    )

    # claude-opus-4-6 model must be detected
    assert any("claude" in m.lower() for m in model_names), (
        f"Expected a Claude model node, got: {model_names}"
    )

    # At least one AGENT node
    assert len(agent_nodes) >= 1, "Expected at least one AGENT node"

    # Tools from allowed_tools — at least one of the core built-in tools
    expected_tools = {"Task", "Read", "Write", "Edit", "Bash", "WebSearch"}
    assert expected_tools & tool_names, (
        f"Expected at least one of {expected_tools} in tool nodes, got: {tool_names}"
    )

    # System prompt detected
    assert len(prompt_nodes) >= 1, "Expected at least one PROMPT node"

    # SBOM document is well-formed
    assert doc.schema_version
    assert ComponentType.FRAMEWORK in node_types
