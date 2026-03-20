"""Extraction tests against realistic AI application fixtures.

Two sets of fixtures are covered:

``fixtures/apps/`` — detailed scenario fixtures (multi-file, richer metadata):
  customer_service_bot   LangGraph multi-agent routing, OpenAI + Anthropic models
  research_assistant     OpenAI Agents SDK, two agents with function tools
  rag_pipeline           LlamaIndex RAG, vector store, two LLM providers
  code_review_crew       Mixed CrewAI + AutoGen

``fixtures/`` (root) — focused integration fixtures (single-file, framework-specific):
  langgraph_research_agent  StateGraph + researcher/tools/writer + ChatAnthropic
  openai_agents_triage      Three Agent instances, @function_tool, handoffs
  crewai_blog_team          Two Agents, two Tasks, one Crew
  llamaindex_rag            VectorStoreIndex, ChromaVectorStore, OpenAI + Anthropic

Cross-cutting quality tests are in ``TestQuality`` at the bottom.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xelo.adapters.registry import default_framework_adapters
from xelo.extractor import AiSbomExtractor
from xelo.models import AiSbomDocument
from xelo.types import ComponentType, RelationshipType
from conftest import APPS, FIXTURES, PY_ONLY, adapters, extract, names, nodes


# ═══════════════════════════════════════════════════════════════════════════
# fixtures/apps/ — scenario tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCustomerServiceBot:
    """LangGraph multi-agent routing system with two LLM providers."""

    @pytest.fixture(scope="class")
    def doc(self) -> AiSbomDocument:
        return extract(APPS / "customer_service_bot")

    def test_framework_detected(self, doc: AiSbomDocument) -> None:
        fw = nodes(doc, ComponentType.FRAMEWORK)
        adapter_names = {n.metadata.extras.get("adapter") for n in fw}
        assert "langgraph" in adapter_names, "Expected LangGraph FRAMEWORK node"

    def test_agents_detected(self, doc: AiSbomDocument) -> None:
        agents = nodes(doc, ComponentType.AGENT)
        agent_names = {n.name.lower() for n in agents}
        assert any(a in agent_names for a in {"billing", "technical", "triage", "tools"}), (
            f"Expected at least one named graph node, got: {agent_names}"
        )

    def test_two_model_providers(self, doc: AiSbomDocument) -> None:
        models = nodes(doc, ComponentType.MODEL)
        providers = {n.metadata.extras.get("provider") for n in models}
        assert "openai" in providers, f"OpenAI model not found. providers={providers}"
        assert "anthropic" in providers, f"Anthropic model not found. providers={providers}"

    def test_model_metadata_enriched(self, doc: AiSbomDocument) -> None:
        for m in nodes(doc, ComponentType.MODEL):
            extras = m.metadata.extras
            assert extras.get("model_card_url"), f"Model {m.name!r} missing model_card_url"
            assert extras.get("provider"), f"Model {m.name!r} missing provider"

    def test_tools_detected(self, doc: AiSbomDocument) -> None:
        assert nodes(doc, ComponentType.TOOL), "Expected at least one TOOL node (ToolNode)"

    def test_edges_present(self, doc: AiSbomDocument) -> None:
        assert doc.edges, "Expected relationship edges between components"
        rel_types = {e.relationship_type for e in doc.edges}
        assert RelationshipType.USES in rel_types or RelationshipType.CALLS in rel_types

    def test_deterministic(self) -> None:
        doc1 = extract(APPS / "customer_service_bot")
        doc2 = extract(APPS / "customer_service_bot")
        assert len(doc1.nodes) == len(doc2.nodes)
        assert len(doc1.edges) == len(doc2.edges)
        assert sorted(n.name for n in doc1.nodes) == sorted(n.name for n in doc2.nodes)


class TestResearchAssistant:
    """OpenAI Agents SDK: two agents with function tools and handoff."""

    @pytest.fixture(scope="class")
    def doc(self) -> AiSbomDocument:
        return extract(APPS / "research_assistant")

    def test_framework_detected(self, doc: AiSbomDocument) -> None:
        assert nodes(doc, ComponentType.FRAMEWORK), "Expected openai_agents FRAMEWORK node"

    def test_two_agents_found(self, doc: AiSbomDocument) -> None:
        agent_names = names(doc, ComponentType.AGENT)
        assert "research_assistant" in agent_names, f"research_assistant not found: {agent_names}"
        assert "report_writer" in agent_names, f"report_writer not found: {agent_names}"

    def test_gpt4o_model_extracted(self, doc: AiSbomDocument) -> None:
        model_names = names(doc, ComponentType.MODEL)
        assert any("gpt-4o" in n for n in model_names), f"Expected gpt-4o model, got: {model_names}"

    def test_function_tools_detected(self, doc: AiSbomDocument) -> None:
        tools = nodes(doc, ComponentType.TOOL)
        assert len(tools) >= 2, (
            f"Expected ≥2 function tools, got {len(tools)}: {[t.name for t in tools]}"
        )

    def test_system_prompt_extracted(self, doc: AiSbomDocument) -> None:
        prompts = nodes(doc, ComponentType.PROMPT)
        assert prompts, "Expected at least one PROMPT node from agent instructions"
        enriched = [
            p
            for p in prompts
            if p.metadata.extras.get("content") or p.metadata.extras.get("char_count")
        ]
        assert enriched, (
            "Expected at least one PROMPT with content or char_count; "
            f"got prompts: {[p.metadata.extras for p in prompts]}"
        )

    def test_model_family_enrichment(self, doc: AiSbomDocument) -> None:
        gpt_nodes = [m for m in nodes(doc, ComponentType.MODEL) if "gpt" in m.name.lower()]
        if gpt_nodes:
            m = gpt_nodes[0]
            assert m.metadata.extras.get("model_family") == "gpt"
            assert m.metadata.extras.get("model_card_url", "").startswith(
                "https://platform.openai.com"
            )


class TestRagPipeline:
    """LlamaIndex RAG pipeline: vector store, two LLM providers, ReAct agent."""

    @pytest.fixture(scope="class")
    def doc(self) -> AiSbomDocument:
        return extract(APPS / "rag_pipeline")

    def test_framework_detected(self, doc: AiSbomDocument) -> None:
        fw_adapters = {
            n.metadata.extras.get("adapter") for n in nodes(doc, ComponentType.FRAMEWORK)
        }
        assert "llamaindex" in fw_adapters

    def test_vector_store_as_datastore(self, doc: AiSbomDocument) -> None:
        assert nodes(doc, ComponentType.DATASTORE), (
            "Expected at least one DATASTORE node (VectorStoreIndex / ChromaVectorStore)"
        )

    def test_anthropic_model_detected(self, doc: AiSbomDocument) -> None:
        providers = {n.metadata.extras.get("provider") for n in nodes(doc, ComponentType.MODEL)}
        assert "anthropic" in providers, f"Expected Anthropic LLM, got providers: {providers}"

    def test_agent_detected(self, doc: AiSbomDocument) -> None:
        assert nodes(doc, ComponentType.AGENT), "Expected ReActAgent to produce an AGENT node"

    def test_tools_detected(self, doc: AiSbomDocument) -> None:
        tools = nodes(doc, ComponentType.TOOL)
        assert len(tools) >= 1, (
            f"Expected ≥1 tool (QueryEngineTool, FunctionTool), got {len(tools)}"
        )

    def test_claude_model_card_url(self, doc: AiSbomDocument) -> None:
        anthropic_models = [
            m
            for m in nodes(doc, ComponentType.MODEL)
            if m.metadata.extras.get("provider") == "anthropic"
        ]
        if anthropic_models:
            url = anthropic_models[0].metadata.extras.get("model_card_url", "")
            assert "anthropic" in url.lower(), f"Expected Anthropic model card URL, got: {url!r}"


class TestCodeReviewCrew:
    """Mixed CrewAI + AutoGen: three CrewAI agents, AutoGen assistant/proxy."""

    @pytest.fixture(scope="class")
    def doc(self) -> AiSbomDocument:
        return extract(APPS / "code_review_crew")

    def test_crewai_framework_detected(self, doc: AiSbomDocument) -> None:
        assert "crewai" in adapters(doc)

    def test_autogen_framework_detected(self, doc: AiSbomDocument) -> None:
        assert "autogen" in adapters(doc)

    def test_three_crewai_agents(self, doc: AiSbomDocument) -> None:
        crewai_agents = [
            a
            for a in nodes(doc, ComponentType.AGENT)
            if a.metadata.extras.get("adapter") == "crewai"
            and a.metadata.extras.get("class_name") != "Crew"
        ]
        assert len(crewai_agents) >= 3, (
            f"Expected ≥3 CrewAI Agent nodes (reviewer, auditor, tester), "
            f"got {len(crewai_agents)}: {[a.name for a in crewai_agents]}"
        )

    def test_autogen_agents_detected(self, doc: AiSbomDocument) -> None:
        autogen_agents = [
            a
            for a in nodes(doc, ComponentType.AGENT)
            if a.metadata.extras.get("adapter") == "autogen"
        ]
        assert autogen_agents, "Expected AutoGen AssistantAgent / UserProxyAgent"

    def test_multi_provider_models(self, doc: AiSbomDocument) -> None:
        providers = {n.metadata.extras.get("provider") for n in nodes(doc, ComponentType.MODEL)}
        assert "anthropic" in providers or "openai" in providers

    def test_crewai_tasks_as_tools(self, doc: AiSbomDocument) -> None:
        crewai_tools = [
            t
            for t in nodes(doc, ComponentType.TOOL)
            if t.metadata.extras.get("adapter") == "crewai"
        ]
        assert crewai_tools, "Expected CrewAI Task nodes mapped to TOOL components"


# ═══════════════════════════════════════════════════════════════════════════
# fixtures/ (root) — integration tests
# ═══════════════════════════════════════════════════════════════════════════


class TestLangGraphResearchAgent:
    """agents.py: StateGraph + researcher/tools/writer nodes + ChatAnthropic."""

    @pytest.fixture(scope="class")
    def doc(self) -> AiSbomDocument:
        return extract(FIXTURES / "langgraph_research_agent")

    def test_detects_framework(self, doc: AiSbomDocument) -> None:
        fw_adapters = {
            n.metadata.extras.get("adapter") for n in nodes(doc, ComponentType.FRAMEWORK)
        }
        assert "langgraph" in fw_adapters

    def test_detects_graph_nodes_as_agents(self, doc: AiSbomDocument) -> None:
        agent_names = names(doc, ComponentType.AGENT)
        assert "researcher" in agent_names or "workflow" in agent_names

    def test_detects_tool_node(self, doc: AiSbomDocument) -> None:
        assert names(doc, ComponentType.TOOL), "Expected at least one TOOL node"

    def test_detects_claude_model(self, doc: AiSbomDocument) -> None:
        model_names = names(doc, ComponentType.MODEL)
        assert any("claude" in n for n in model_names), (
            f"Expected Claude model node, got: {model_names}"
        )

    def test_claude_model_has_metadata(self, doc: AiSbomDocument) -> None:
        claude = next(
            (
                n
                for n in doc.nodes
                if n.component_type == ComponentType.MODEL and "claude" in n.name.lower()
            ),
            None,
        )
        assert claude is not None
        extras = claude.metadata.extras
        assert extras.get("provider") == "anthropic"
        assert extras.get("model_family") == "claude"
        assert extras.get("model_card_url") is not None

    def test_detects_system_prompt(self, doc: AiSbomDocument) -> None:
        assert nodes(doc, ComponentType.PROMPT), (
            "Expected at least one PROMPT node for SYSTEM_PROMPT constant"
        )

    def test_agent_to_model_edges(self, doc: AiSbomDocument) -> None:
        uses_edges = [e for e in doc.edges if e.relationship_type.value == "USES"]
        assert uses_edges, "Expected AGENT--USES-->MODEL edges"

    def test_tool_calls_edges(self, doc: AiSbomDocument) -> None:
        calls_edges = [e for e in doc.edges if e.relationship_type.value == "CALLS"]
        assert calls_edges, "Expected AGENT--CALLS-->TOOL edges"

    def test_deterministic_extraction(self) -> None:
        doc1 = extract(FIXTURES / "langgraph_research_agent")
        doc2 = extract(FIXTURES / "langgraph_research_agent")
        assert len(doc1.nodes) == len(doc2.nodes)
        assert sorted(n.name for n in doc1.nodes) == sorted(n.name for n in doc2.nodes)


class TestOpenAIAgentsTriage:
    """agents.py: three Agent instances, @function_tool decorators, handoffs."""

    @pytest.fixture(scope="class")
    def doc(self) -> AiSbomDocument:
        return extract(FIXTURES / "openai_agents_triage")

    def test_detects_framework(self, doc: AiSbomDocument) -> None:
        assert "openai_agents" in adapters(doc)

    def test_detects_all_three_agents(self, doc: AiSbomDocument) -> None:
        agent_names = names(doc, ComponentType.AGENT)
        assert "triage_agent" in agent_names
        assert "billing_agent" in agent_names
        assert "technical_agent" in agent_names

    def test_detects_function_tools(self, doc: AiSbomDocument) -> None:
        tool_names = names(doc, ComponentType.TOOL)
        assert "lookup_account" in tool_names or "create_refund" in tool_names

    def test_detects_gpt_models(self, doc: AiSbomDocument) -> None:
        model_names = names(doc, ComponentType.MODEL)
        assert any("gpt" in n for n in model_names), f"Expected GPT model nodes, got: {model_names}"

    def test_detects_instructions_as_prompts(self, doc: AiSbomDocument) -> None:
        assert nodes(doc, ComponentType.PROMPT), "Expected PROMPT nodes from agent instructions"

    def test_model_has_openai_provider(self, doc: AiSbomDocument) -> None:
        gpt_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.MODEL and "gpt" in n.name.lower()
        ]
        assert gpt_nodes
        assert gpt_nodes[0].metadata.extras.get("provider") == "openai"

    def test_agent_uses_model_edge(self, doc: AiSbomDocument) -> None:
        uses = [e for e in doc.edges if e.relationship_type.value == "USES"]
        assert uses

    def test_evidence_quality(self, doc: AiSbomDocument) -> None:
        for node in doc.nodes:
            for ev in node.evidence:
                assert ev.location is not None
                assert ev.confidence > 0


class TestCrewAIBlogTeam:
    """crew.py: two Agents (researcher + writer), two Tasks, one Crew."""

    @pytest.fixture(scope="class")
    def doc(self) -> AiSbomDocument:
        return extract(FIXTURES / "crewai_blog_team")

    def test_detects_framework(self, doc: AiSbomDocument) -> None:
        assert "crewai" in adapters(doc)

    def test_detects_both_agents(self, doc: AiSbomDocument) -> None:
        assert len(names(doc, ComponentType.AGENT)) >= 2

    def test_detects_crew_orchestrator(self, doc: AiSbomDocument) -> None:
        # Crew() objects are the orchestration container, not individual agents.
        # The FRAMEWORK node for crewai should be present instead.
        assert "crewai" in adapters(doc), "Expected crewai framework to be detected"

    def test_detects_tasks_as_tools(self, doc: AiSbomDocument) -> None:
        assert nodes(doc, ComponentType.TOOL), "Expected Task nodes registered as TOOL components"

    def test_detects_both_models(self, doc: AiSbomDocument) -> None:
        model_names = names(doc, ComponentType.MODEL)
        has_claude = any("claude" in n for n in model_names)
        has_gpt = any("gpt" in n for n in model_names)
        assert has_claude or has_gpt, f"Expected AI models, got: {model_names}"

    def test_no_duplicate_models(self, doc: AiSbomDocument) -> None:
        model_names = [n.name.lower() for n in doc.nodes if n.component_type == ComponentType.MODEL]
        assert len(model_names) == len(set(model_names)), (
            f"Duplicate model nodes detected: {model_names}"
        )

    def test_backstory_as_prompt_or_metadata(self, doc: AiSbomDocument) -> None:
        crewai_agents = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.AGENT
            and n.metadata.extras.get("framework") == "crewai"
        ]
        assert any("has_backstory" in n.metadata.extras for n in crewai_agents), (
            f"Expected has_backstory key in crewai agent extras; "
            f"got: {[n.metadata.extras for n in crewai_agents]}"
        )


class TestLlamaIndexRag:
    """pipeline.py: VectorStoreIndex, ChromaVectorStore, OpenAI + Anthropic LLMs."""

    @pytest.fixture(scope="class")
    def doc(self) -> AiSbomDocument:
        return extract(FIXTURES / "llamaindex_rag")

    def test_detects_framework(self, doc: AiSbomDocument) -> None:
        assert "llamaindex" in adapters(doc)

    def test_detects_vector_datastore(self, doc: AiSbomDocument) -> None:
        assert names(doc, ComponentType.DATASTORE), (
            "Expected DATASTORE node for VectorStoreIndex / ChromaVectorStore"
        )

    def test_detects_openai_model(self, doc: AiSbomDocument) -> None:
        model_names = names(doc, ComponentType.MODEL)
        assert any("gpt" in n for n in model_names), f"Expected GPT-4o model, got: {model_names}"

    def test_detects_anthropic_model(self, doc: AiSbomDocument) -> None:
        model_names = names(doc, ComponentType.MODEL)
        assert any("claude" in n for n in model_names), f"Expected Claude model, got: {model_names}"

    def test_detects_query_engine_as_agent(self, doc: AiSbomDocument) -> None:
        assert names(doc, ComponentType.AGENT), "Expected AGENT node for RetrieverQueryEngine"

    def test_models_have_provider_metadata(self, doc: AiSbomDocument) -> None:
        enriched = [n for n in nodes(doc, ComponentType.MODEL) if n.metadata.extras.get("provider")]
        assert enriched, "At least one model should have a provider annotation"


# ═══════════════════════════════════════════════════════════════════════════
# Cross-fixture quality tests
# ═══════════════════════════════════════════════════════════════════════════


class TestQuality:
    """Cross-cutting correctness and deduplication assertions."""

    def test_no_duplicate_nodes_within_fixture(self) -> None:
        """Each fixture should produce unique (component_type, name) pairs."""
        for fixture in (
            FIXTURES / "langgraph_research_agent",
            FIXTURES / "openai_agents_triage",
            FIXTURES / "crewai_blog_team",
            FIXTURES / "llamaindex_rag",
        ):
            doc = extract(fixture)
            pairs = [(n.component_type, n.name.lower()) for n in doc.nodes]
            assert len(pairs) == len(set(pairs)), (
                f"{fixture.name}: duplicate nodes detected: "
                + str([p for p in pairs if pairs.count(p) > 1])
            )

    def test_all_nodes_have_positive_confidence(self) -> None:
        doc = extract(FIXTURES / "langgraph_research_agent")
        for node in doc.nodes:
            assert node.confidence > 0, f"Node {node.name!r} has zero confidence"

    def test_all_edges_reference_existing_nodes(self) -> None:
        doc = extract(FIXTURES / "openai_agents_triage")
        node_ids = {n.id for n in doc.nodes}
        for edge in doc.edges:
            assert edge.source in node_ids, "Edge source not in node set"
            assert edge.target in node_ids, "Edge target not in node set"

    def test_framework_nodes_deduplicate_across_imports(self, tmp_path: Path) -> None:
        """Same framework imported in two files → single FRAMEWORK node."""
        (tmp_path / "a.py").write_text("from langgraph import StateGraph\n")
        (tmp_path / "b.py").write_text("import langgraph\n")
        doc = AiSbomExtractor().extract_from_path(tmp_path, PY_ONLY)
        fw = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.FRAMEWORK
            and n.metadata.extras.get("adapter") == "langgraph"
        ]
        assert len(fw) == 1
        assert fw[0].metadata.extras["evidence_count"] >= 2

    def test_model_name_deduplicates_across_adapters(self, tmp_path: Path) -> None:
        """AST-detected model and regex-detected model for same name merge."""
        (tmp_path / "app.py").write_text(
            "from langchain_openai import ChatOpenAI\nllm = ChatOpenAI(model='gpt-4o')\n"
        )
        doc = AiSbomExtractor().extract_from_path(tmp_path, PY_ONLY)
        gpt_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.MODEL and "gpt" in n.name.lower()
        ]
        assert len(gpt_nodes) == 1, f"Expected single gpt-4o node, got {len(gpt_nodes)}"
        assert gpt_nodes[0].metadata.extras.get("provider") == "openai"

    def test_adapter_registry_order(self) -> None:
        adapters_list = default_framework_adapters()
        priorities = [a.priority for a in adapters_list]
        assert priorities == sorted(priorities)
        adapter_names = {a.name for a in adapters_list}
        assert {
            "langgraph",
            "openai_agents",
            "autogen",
            "semantic_kernel",
            "crewai",
            "llamaindex",
            "llm_clients",
        } <= adapter_names
        assert {
            "langgraph_ts",
            "openai_agents_ts",
            "google_adk_ts",
            "llm_clients_ts",
            "bedrock_agents_ts",
            "datastore_ts",
            "prompt_ts",
        } <= adapter_names
