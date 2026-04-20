"""Tests for SBOM-driven redteam scenario builders and ScenarioGenerator integration.

Covers:
- _classify_tool keyword routing (all 7 categories)
- build_tool_scenarios per category — correct goal_type, scenario_type, OWASP refs,
  payload content, and target_node_id wiring
- ScenarioGenerator._sbom_driven_scenarios — tools with descriptions produce attack
  chains; tools without descriptions are skipped
- build_guided_tool_redteam — structure, sbom_path, max_turns
- ScenarioGenerator._guided_conversation_scenarios — tool portion
"""
from __future__ import annotations

import uuid

import pytest

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.redteam.scenarios.guided_conversations import build_guided_tool_redteam
from nuguard.redteam.scenarios.sbom_driven import _classify_tool, build_tool_scenarios
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata, ScanSummary
from nuguard.sbom.types import ComponentType, RelationshipType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_node(name: str, description: str, node_key: str | None = None) -> Node:
    return Node(
        id=uuid.uuid5(uuid.NAMESPACE_URL, node_key or name),
        name=name,
        component_type=ComponentType.TOOL,
        confidence=0.9,
        metadata=NodeMetadata(description=description),
    )


def _agent_node(name: str, description: str = "", node_key: str | None = None) -> Node:
    return Node(
        id=uuid.uuid5(uuid.NAMESPACE_URL, node_key or name),
        name=name,
        component_type=ComponentType.AGENT,
        confidence=0.9,
        metadata=NodeMetadata(description=description),
    )


def _make_sbom(nodes: list[Node], edges: list[Edge] | None = None) -> AiSbomDocument:
    return AiSbomDocument(
        target="test-app",
        nodes=nodes,
        edges=edges or [],
        summary=ScanSummary(),
    )


# ---------------------------------------------------------------------------
# _classify_tool — keyword routing
# ---------------------------------------------------------------------------


def test_classify_file_category_pdf():
    assert _classify_tool("process_pdf", "extracts text from PDF documents") == "file"


def test_classify_file_category_ocr():
    assert _classify_tool("ocr_tool", "uses OCR to extract text from attachments") == "file"


def test_classify_file_category_doc():
    assert _classify_tool("doc_parser", "parses doc files") == "file"


def test_classify_sql_category():
    assert _classify_tool("query_db", "runs database queries") == "sql"
    assert _classify_tool("record_lookup", "filters table records") == "sql"


def test_classify_ssrf_category():
    assert _classify_tool("url_fetcher", "fetches URLs from the web") == "ssrf"
    assert _classify_tool("web_scraper", "crawls and scrapes web pages") == "ssrf"


def test_classify_email_category():
    assert _classify_tool("send_email", "sends email notifications") == "email"
    assert _classify_tool("smtp_notify", "SMTP message sender") == "email"


def test_classify_path_category_standalone_file_keyword():
    # Per design doc, 'file' keyword belongs to the path (traversal) category, not document injection
    assert _classify_tool("read_file", "reads file from disk") == "path"
    assert _classify_tool("storage_tool", "filesystem path access") == "path"


def test_classify_cmd_category():
    assert _classify_tool("run_script", "executes shell commands") == "cmd"
    assert _classify_tool("eval_tool", "evaluates and runs code") == "cmd"


def test_classify_generic_fallback():
    assert _classify_tool("calendar_tool", "manages appointments and meetings") == "generic"
    assert _classify_tool("unknown_thing", "does something unrelated") == "generic"


def test_classify_prefers_first_matching_category():
    # 'pdf' hits 'file' before anything else even if other keywords also present
    assert _classify_tool("pdf_query_tool", "pdf document that queries a database") == "file"


# ---------------------------------------------------------------------------
# build_tool_scenarios — per category
# ---------------------------------------------------------------------------


def test_build_tool_scenarios_file_category():
    node = _tool_node("process_pdf", "Extracts text from PDF documents")
    scenarios = build_tool_scenarios(node, "TestAgent")

    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.scenario_type == ScenarioType.INDIRECT_INJECTION
    assert s.goal_type == GoalType.PROMPT_DRIVEN_THREAT
    assert s.chain is not None
    assert len(s.chain.steps) == 2
    # Second step injects malicious document content
    combined = s.chain.steps[0].payload + s.chain.steps[1].payload
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in combined or "BEGIN DOCUMENT" in combined
    assert s.chain.owasp_asi_ref == "ASI02"
    assert s.chain.owasp_llm_ref == "LLM02"
    assert str(node.id) in s.target_node_ids
    assert "process_pdf" in s.title


def test_build_tool_scenarios_sql_category():
    node = _tool_node("query_orders", "runs SQL queries against order database")
    scenarios = build_tool_scenarios(node, "TestAgent")

    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.scenario_type == ScenarioType.SQL_INJECTION
    assert s.goal_type == GoalType.TOOL_ABUSE
    assert s.chain is not None
    assert len(s.chain.steps) == 2
    # Step 1 uses classic SQLi
    assert "1'='1" in s.chain.steps[0].payload or "OR" in s.chain.steps[0].payload
    assert s.chain.owasp_asi_ref == "ASI05"
    assert s.chain.owasp_llm_ref == "LLM05"
    assert s.impact_score >= 8.0


def test_build_tool_scenarios_ssrf_category():
    node = _tool_node("fetch_url", "fetches content from any URL for the user")
    scenarios = build_tool_scenarios(node, "TestAgent")

    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.scenario_type == ScenarioType.SSRF
    assert s.goal_type == GoalType.TOOL_ABUSE
    assert s.chain is not None
    # Payload targets AWS metadata endpoint
    assert "169.254.169.254" in s.chain.steps[0].payload
    assert s.impact_score >= 7.5


def test_build_tool_scenarios_email_category():
    node = _tool_node("send_notification", "sends email notifications to users via SMTP")
    scenarios = build_tool_scenarios(node, "TestAgent")

    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.goal_type == GoalType.DATA_EXFILTRATION
    assert s.chain is not None
    assert len(s.chain.steps) == 1
    payload = s.chain.steps[0].payload
    assert "SYSTEM OVERRIDE" in payload or "Disregard" in payload or "OVERRIDE" in payload


def test_build_tool_scenarios_path_category():
    node = _tool_node("read_file", "reads file content from a storage path")
    scenarios = build_tool_scenarios(node, "TestAgent")

    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.goal_type == GoalType.TOOL_ABUSE
    assert s.scenario_type == ScenarioType.RESTRICTED_ACTION
    assert s.chain is not None
    assert "../../../etc/passwd" in s.chain.steps[0].payload
    assert s.chain.owasp_asi_ref == "ASI05"
    assert s.chain.owasp_llm_ref == "LLM05"


def test_build_tool_scenarios_cmd_category():
    node = _tool_node("run_code", "executes Python code or shell commands")
    scenarios = build_tool_scenarios(node, "TestAgent")

    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.goal_type == GoalType.PRIVILEGE_ESCALATION
    assert s.scenario_type == ScenarioType.PRIVILEGE_CHAIN
    assert s.chain is not None
    assert "os.system" in s.chain.steps[0].payload
    assert s.impact_score >= 8.5


def test_build_tool_scenarios_generic_fallback():
    node = _tool_node("calendar_tool", "schedules calendar events and manages appointments")
    scenarios = build_tool_scenarios(node, "TestAgent")

    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.goal_type == GoalType.PROMPT_DRIVEN_THREAT
    assert s.scenario_type == ScenarioType.MULTI_TURN_REDIRECTION
    assert s.chain is not None
    assert len(s.chain.steps) == 2
    assert s.chain.owasp_asi_ref == "ASI01"
    assert s.chain.owasp_llm_ref == "LLM01"


def test_build_tool_scenarios_wires_sbom_path():
    node = _tool_node("process_pdf", "parses PDF documents")
    scenarios = build_tool_scenarios(node, "SomeAgent")

    assert len(scenarios) == 1
    assert str(node.id) in scenarios[0].chain.sbom_path  # type: ignore[union-attr]


def test_build_tool_scenarios_on_failure_mutate_for_injections():
    """Steps that are the main injection vector should use on_failure='mutate'."""
    node = _tool_node("process_pdf", "Extracts text from PDF documents")
    scenarios = build_tool_scenarios(node, "A")

    last_step = scenarios[0].chain.steps[-1]  # type: ignore[union-attr]
    assert last_step.on_failure == "mutate"


# ---------------------------------------------------------------------------
# ScenarioGenerator._sbom_driven_scenarios integration
# ---------------------------------------------------------------------------


def test_generator_sbom_driven_tool_with_description_produces_scenario():
    pdf_tool = _tool_node("process_pdf", "Extracts text from PDF documents")
    sbom = _make_sbom([pdf_tool])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()

    titles = [s.title for s in scenarios]
    assert any("process_pdf" in t for t in titles), "Expected sbom_driven scenario for process_pdf"


def test_generator_sbom_driven_skips_tool_without_description():
    bare_tool = _tool_node("bare_tool", "")
    sbom = _make_sbom([bare_tool])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()

    titles = [s.title for s in scenarios]
    # bare_tool has no description — no sbom_driven scenario should be produced
    assert not any("bare_tool" in t for t in titles)


def test_generator_sbom_driven_all_categories_each_get_one_scenario():
    """One TOOL per category → one sbom_driven attack chain per tool."""
    tools = [
        _tool_node("pdf_tool", "parse and extract text from PDF attachments", "n1"),
        _tool_node("db_tool", "query records from SQL database tables", "n2"),
        _tool_node("web_tool", "fetch and scrape URLs from the web", "n3"),
        _tool_node("mail_tool", "send email notifications via SMTP", "n4"),
        _tool_node("fs_tool", "read and write files from filesystem path", "n5"),
        _tool_node("exec_tool", "execute shell commands and run scripts", "n6"),
        _tool_node("sched_tool", "schedule and manage calendar appointments", "n7"),
    ]
    sbom = _make_sbom(tools)
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()

    # Each tool should produce exactly one sbom_driven attack scenario.
    # Identify them by title — build_tool_scenarios sets title as "{Attack Type} — {tool_name}"
    tool_names = {t.name for t in tools}
    sbom_driven = [
        s for s in scenarios
        if s.chain is not None
        and any(t_name in s.title for t_name in tool_names)
    ]
    assert len(sbom_driven) >= 7, f"Expected ≥7 sbom_driven scenarios, got {len(sbom_driven)}"


def test_generator_find_owning_agent_name_with_calls_edge():
    agent = _agent_node("OrderBot", "helps with orders", "agent-1")
    tool = _tool_node("process_pdf", "extracts text from PDF documents", "tool-1")
    edge = Edge(
        source=agent.id,
        target=tool.id,
        relationship_type=RelationshipType.CALLS,
    )
    sbom = _make_sbom([agent, tool], edges=[edge])
    gen = ScenarioGenerator(sbom)

    assert gen._find_owning_agent_name(tool) == "OrderBot"


def test_generator_find_owning_agent_name_no_edge():
    tool = _tool_node("process_pdf", "extracts text from PDF documents")
    sbom = _make_sbom([tool])
    gen = ScenarioGenerator(sbom)

    assert gen._find_owning_agent_name(tool) == ""


# ---------------------------------------------------------------------------
# build_guided_tool_redteam
# ---------------------------------------------------------------------------


def test_build_guided_tool_redteam_structure():
    s = build_guided_tool_redteam(
        tool_node_id="tool-abc",
        tool_name="process_pdf",
        tool_description="Extracts text from PDF documents for downstream analysis.",
        agent_node_id="agent-xyz",
    )

    assert s.guided_conversation is not None
    assert s.chain is None
    assert s.goal_type == GoalType.PROMPT_DRIVEN_THREAT
    assert s.scenario_type == ScenarioType.INDIRECT_INJECTION
    assert "process_pdf" in s.title
    assert "process_pdf" in s.guided_conversation.goal_description
    assert "tool-abc" in s.guided_conversation.sbom_path
    assert "agent-xyz" in s.guided_conversation.sbom_path
    assert s.guided_conversation.max_turns == 8
    assert s.guided_conversation.owasp_asi_ref == "ASI02"
    assert s.guided_conversation.owasp_llm_ref == "LLM02"


def test_build_guided_tool_redteam_no_agent_id():
    s = build_guided_tool_redteam(
        tool_node_id="tool-abc",
        tool_name="url_fetcher",
        tool_description="Fetches content from any URL.",
    )

    assert s.guided_conversation is not None
    assert s.guided_conversation.sbom_path == ["tool-abc"]


def test_build_guided_tool_redteam_description_in_goal():
    s = build_guided_tool_redteam(
        tool_node_id="t1",
        tool_name="send_email",
        tool_description="sends email notifications to users.",
    )

    goal = s.guided_conversation.goal_description  # type: ignore[union-attr]
    assert "send_email" in goal
    assert "email" in goal.lower() or "sends" in goal.lower()


# ---------------------------------------------------------------------------
# ScenarioGenerator._guided_conversation_scenarios — tool portion
# ---------------------------------------------------------------------------


def test_generator_guided_produces_tool_scenario_with_description():
    tool = _tool_node("process_pdf", "Extracts text from PDF documents for downstream analysis.")
    sbom = _make_sbom([tool])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate(with_guided=True)

    guided_tool = [
        s for s in scenarios
        if s.guided_conversation is not None and "process_pdf" in s.title
    ]
    assert len(guided_tool) >= 1
    assert guided_tool[0].scenario_type == ScenarioType.INDIRECT_INJECTION


def test_generator_guided_skips_tool_without_description():
    bare_tool = _tool_node("bare_tool", "")
    sbom = _make_sbom([bare_tool])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate(with_guided=True)

    guided_titles = [s.title for s in scenarios if s.guided_conversation is not None]
    assert not any("bare_tool" in t for t in guided_titles)


def test_generator_with_guided_false_excludes_guided_scenarios():
    tool = _tool_node("process_pdf", "Extracts text from PDF documents.")
    agent = _agent_node("TestAgent", "Assists users")
    sbom = _make_sbom([agent, tool])
    gen = ScenarioGenerator(sbom)

    no_guided = gen.generate(with_guided=False)
    with_guided = gen.generate(with_guided=True)

    assert not any(s.guided_conversation is not None for s in no_guided)
    assert any(s.guided_conversation is not None for s in with_guided)


def test_generator_guided_agent_scope_also_includes_tool():
    """When an agent CALLS a described tool, guided scenarios cover both agent and tool."""
    agent = _agent_node("OrderBot", "Helps customers manage orders", "agent-gt-1")
    tool = _tool_node("send_email", "sends email notifications via SMTP", "tool-gt-1")
    edge = Edge(
        source=agent.id,
        target=tool.id,
        relationship_type=RelationshipType.CALLS,
    )
    sbom = _make_sbom([agent, tool], edges=[edge])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate(with_guided=True)

    guided = [s for s in scenarios if s.guided_conversation is not None]
    titles = [s.title for s in guided]

    # Tool-level guided scenario
    assert any("send_email" in t for t in titles)
    # Agent-level guided scenario (system prompt leak / tool coercion)
    assert any("OrderBot" in t for t in titles)
