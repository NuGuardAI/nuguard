"""Regression tests for the AGENT-node fallback synthesis in AiSbomExtractor.

A plain FastAPI (or Flask) backend that calls an LLM API directly — with no
agentic framework (LangGraph/CrewAI/AutoGen/etc.) — produces zero AGENT
nodes from the framework adapters, which starves every AGENT-gated redteam
scenario family (PROMPT_DRIVEN_THREAT, DATA_EXFILTRATION). The extractor
should synthesize a single fallback AGENT node representing the app itself
whenever MODEL/TOOL/MCP_SERVER nodes exist but no AGENT node was detected.
"""

from __future__ import annotations

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.types import ComponentType

_CONFIG = AiSbomConfig(include_extensions={".py"}, enable_llm=False)


def test_synthesizes_agent_node_when_model_present_but_no_framework(tmp_path) -> None:
    source_dir = tmp_path / "plain-llm-app"
    (source_dir / "server").mkdir(parents=True)
    (source_dir / "server" / "chat.py").write_text(
        "from openai import OpenAI\n\n"
        "client = OpenAI()\n\n"
        "def ask(question: str) -> str:\n"
        "    resp = client.chat.completions.create(\n"
        "        model='gpt-4o',\n"
        "        messages=[{'role': 'user', 'content': question}],\n"
        "    )\n"
        "    return resp.choices[0].message.content\n",
        encoding="utf-8",
    )

    doc = AiSbomExtractor().extract_from_path(source_dir, _CONFIG)

    assert any(n.component_type == ComponentType.MODEL for n in doc.nodes)
    agent_nodes = [n for n in doc.nodes if n.component_type == ComponentType.AGENT]
    assert len(agent_nodes) == 1
    assert agent_nodes[0].metadata.extras.get("source") == "auto_enrichment"


def test_does_not_synthesize_when_real_agent_framework_detected(tmp_path) -> None:
    source_dir = tmp_path / "crewai-app"
    source_dir.mkdir()
    (source_dir / "agent.py").write_text(
        "from crewai import Agent\n\n"
        "researcher = Agent(role='Researcher', goal='find things', backstory='...')\n",
        encoding="utf-8",
    )

    doc = AiSbomExtractor().extract_from_path(source_dir, _CONFIG)

    agent_nodes = [n for n in doc.nodes if n.component_type == ComponentType.AGENT]
    assert len(agent_nodes) == 1
    assert agent_nodes[0].metadata.extras.get("source") != "auto_enrichment"


def test_does_not_synthesize_when_no_model_tool_or_mcp_nodes(tmp_path) -> None:
    source_dir = tmp_path / "no-llm-app"
    source_dir.mkdir()
    (source_dir / "util.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )

    doc = AiSbomExtractor().extract_from_path(source_dir, _CONFIG)

    assert not any(n.component_type == ComponentType.AGENT for n in doc.nodes)
