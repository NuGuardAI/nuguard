"""
Smoke test: NuGuardAI/Healthcare-voice-agent

Clones the public repository and asserts that Xelo correctly extracts the
AI Bill of Materials for a real-world healthcare AI application:

Architecture under test
-----------------------
Backend (Python):
  - LangGraph StateGraph with 5 nodes (normalize, prognosis, specialist
    lookup, recommend, fetch doctors) — backend/langgraph_llm_agents.py
  - ChatOpenAI model="gpt-4" via langchain_openai
  - SystemMessage prompt constants

Frontend (JavaScript):
  - GoogleGenAI from @google/genai — src/gemini.js
  - gemini-2.0-flash model
  - System instruction / medical triage prompt

Run this test:
  pytest tests/smoke/ -v -m smoke

Skip in offline / CI environments:
  pytest tests/smoke/ -m "smoke and not network"
  or set AISBOM_SMOKE_SKIP=1
"""

from __future__ import annotations

import os

import pytest

from xelo.config import AiSbomConfig
from xelo.extractor import AiSbomExtractor
from xelo.models import AiSbomDocument
from xelo.types import ComponentType

# ---------------------------------------------------------------------------
# Markers / skip conditions
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.smoke

_SKIP_REASON = "Set AISBOM_SMOKE_SKIP=1 or ensure git is available to run network smoke tests"


def _should_skip() -> bool:
    if os.environ.get("AISBOM_SMOKE_SKIP", "").strip() == "1":
        return True
    import shutil

    return shutil.which("git") is None


skip_if_offline = pytest.mark.skipif(_should_skip(), reason=_SKIP_REASON)

# ---------------------------------------------------------------------------
# Repo + config
# ---------------------------------------------------------------------------

_REPO_URL = "https://github.com/NuGuardAI/Healthcare-voice-agent"
_REPO_REF = "main"

_CONFIG = AiSbomConfig(
    include_extensions={".py", ".js", ".jsx", ".ts", ".tsx"},
    max_files=500,
    enable_llm=False,
)


def _build_repo_url() -> str:
    """Prepend GitHub token for authenticated clone if available."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return f"https://x-access-token:{token}@github.com/NuGuardAI/Healthcare-voice-agent"
    return _REPO_URL


# ---------------------------------------------------------------------------
# Shared fixture: clone once per session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def doc() -> AiSbomDocument:
    if _should_skip():
        pytest.skip(_SKIP_REASON)
    url = _build_repo_url()
    return AiSbomExtractor().extract_from_repo(url, _REPO_REF, _CONFIG)


def _names(doc: AiSbomDocument, ctype: ComponentType) -> set[str]:
    return {n.name.lower() for n in doc.nodes if n.component_type == ctype}


def _adapters(doc: AiSbomDocument) -> set[str]:
    return {n.metadata.extras.get("adapter", "") for n in doc.nodes}


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------


class TestFrameworkDetection:
    """Xelo should detect both the Python LangGraph and Google GenAI (JS) frameworks."""

    @skip_if_offline
    def test_detects_langgraph_framework(self, doc: AiSbomDocument) -> None:
        assert "langgraph" in _adapters(doc), (
            "Expected LangGraph framework node from backend/langgraph_llm_agents.py"
        )

    @skip_if_offline
    def test_detects_llm_clients_ts_framework(self, doc: AiSbomDocument) -> None:
        assert "llm_clients_ts" in _adapters(doc), (
            "Expected llm_clients_ts framework node from src/gemini.js (@google/genai)"
        )


# ---------------------------------------------------------------------------
# Agent detection (LangGraph graph nodes)
# ---------------------------------------------------------------------------


class TestAgentDetection:
    """The five StateGraph nodes should be detected as AGENT components."""

    @skip_if_offline
    def test_detects_normalize_agent(self, doc: AiSbomDocument) -> None:
        agent_names = _names(doc, ComponentType.AGENT)
        assert "normalize_agent" in agent_names, (
            f"Expected 'normalize_agent' in agents; got: {agent_names}"
        )

    @skip_if_offline
    def test_detects_prognosis_agent(self, doc: AiSbomDocument) -> None:
        agent_names = _names(doc, ComponentType.AGENT)
        assert "prognosis_search_agent" in agent_names, (
            f"Expected 'prognosis_search_agent' in agents; got: {agent_names}"
        )

    @skip_if_offline
    def test_detects_specialist_lookup_agent(self, doc: AiSbomDocument) -> None:
        agent_names = _names(doc, ComponentType.AGENT)
        assert "specialist_lookup_agent" in agent_names, (
            f"Expected 'specialist_lookup_agent' in agents; got: {agent_names}"
        )

    @skip_if_offline
    def test_detects_recommend_specialists_agent(self, doc: AiSbomDocument) -> None:
        agent_names = _names(doc, ComponentType.AGENT)
        assert "recommend_specialists_agent" in agent_names, (
            f"Expected 'recommend_specialists_agent' in agents; got: {agent_names}"
        )

    @skip_if_offline
    def test_detects_fetch_doctor_details_agent(self, doc: AiSbomDocument) -> None:
        agent_names = _names(doc, ComponentType.AGENT)
        assert "fetch_doctor_details_agent" in agent_names, (
            f"Expected 'fetch_doctor_details_agent' in agents; got: {agent_names}"
        )

    @skip_if_offline
    def test_agent_count(self, doc: AiSbomDocument) -> None:
        agent_names = _names(doc, ComponentType.AGENT)
        # At minimum the 5 graph nodes (possibly + a StateGraph workflow node)
        assert len(agent_names) >= 5, (
            f"Expected at least 5 agent nodes; got {len(agent_names)}: {agent_names}"
        )


# ---------------------------------------------------------------------------
# Model detection
# ---------------------------------------------------------------------------


class TestModelDetection:
    """GPT-4 (backend) and Gemini 2.0 Flash (frontend) should both appear."""

    @skip_if_offline
    def test_detects_gpt4_model(self, doc: AiSbomDocument) -> None:
        model_names = _names(doc, ComponentType.MODEL)
        assert any("gpt-4" in name for name in model_names), (
            f"Expected GPT-4 model from ChatOpenAI(model='gpt-4'); got: {model_names}"
        )

    @skip_if_offline
    def test_gpt4_has_openai_provider(self, doc: AiSbomDocument) -> None:
        gpt4_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.MODEL and "gpt-4" in n.name.lower()
        ]
        assert gpt4_nodes, "GPT-4 node not found"
        assert gpt4_nodes[0].metadata.extras.get("provider") == "openai", (
            f"Expected provider='openai'; got: {gpt4_nodes[0].metadata.extras}"
        )

    @skip_if_offline
    def test_detects_gemini_model(self, doc: AiSbomDocument) -> None:
        model_names = _names(doc, ComponentType.MODEL)
        assert any("gemini" in name for name in model_names), (
            f"Expected Gemini model from src/gemini.js; got: {model_names}"
        )

    @skip_if_offline
    def test_gemini_model_has_google_provider(self, doc: AiSbomDocument) -> None:
        # The AST-detected node (llm_clients_ts) carries provider metadata.
        # The regex model_generic node for "gemini-2.0" (no provider) may also
        # exist; we assert that at least one Gemini node has provider=google.
        gemini_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.MODEL and "gemini" in n.name.lower()
        ]
        assert gemini_nodes, "No Gemini model node found"
        assert any(n.metadata.extras.get("provider") == "google" for n in gemini_nodes), (
            f"Expected at least one Gemini node with provider='google'; "
            f"got: {[n.metadata.extras for n in gemini_nodes]}"
        )

    @skip_if_offline
    def test_no_duplicate_models(self, doc: AiSbomDocument) -> None:
        model_names = [n.name.lower() for n in doc.nodes if n.component_type == ComponentType.MODEL]
        assert len(model_names) == len(set(model_names)), (
            f"Duplicate model nodes: {[n for n in model_names if model_names.count(n) > 1]}"
        )


# ---------------------------------------------------------------------------
# Prompt detection
# ---------------------------------------------------------------------------


class TestPromptDetection:
    """System prompts (SystemMessage calls in Python, system instruction in JS)
    should be captured as PROMPT components."""

    @skip_if_offline
    def test_detects_prompts(self, doc: AiSbomDocument) -> None:
        prompts = [n for n in doc.nodes if n.component_type == ComponentType.PROMPT]
        assert prompts, (
            "Expected at least one PROMPT node from SystemMessage constants or "
            "the gemini.js systemInstruction"
        )


# ---------------------------------------------------------------------------
# Edge / relationship detection
# ---------------------------------------------------------------------------


class TestRelationships:
    """Agents should have USES → MODEL edges (LangGraph fallback inference)."""

    @skip_if_offline
    def test_agent_uses_model_edges_exist(self, doc: AiSbomDocument) -> None:
        uses_edges = [e for e in doc.edges if e.relationship_type.value == "USES"]
        assert uses_edges, "Expected at least one AGENT--USES-->MODEL edge"

    @skip_if_offline
    def test_all_edge_nodes_exist(self, doc: AiSbomDocument) -> None:
        node_ids = {n.id for n in doc.nodes}
        for edge in doc.edges:
            assert edge.source in node_ids, f"Edge source {edge.source} not in nodes"
            assert edge.target in node_ids, f"Edge target {edge.target} not in nodes"


# ---------------------------------------------------------------------------
# Document quality
# ---------------------------------------------------------------------------


class TestDocumentQuality:
    """Basic quality checks on the extracted document."""

    @skip_if_offline
    def test_has_nodes(self, doc: AiSbomDocument) -> None:
        assert doc.nodes, "Extraction produced no nodes"

    @skip_if_offline
    def test_all_nodes_have_positive_confidence(self, doc: AiSbomDocument) -> None:
        for node in doc.nodes:
            assert node.confidence > 0, f"Node '{node.name}' has zero confidence"

    @skip_if_offline
    def test_all_evidence_has_location(self, doc: AiSbomDocument) -> None:
        for node in doc.nodes:
            for ev in node.evidence:
                assert ev.location is not None, "Evidence item missing location"
                assert ev.location.path, "Evidence location has empty path"

    @skip_if_offline
    def test_deterministic_extraction(self) -> None:
        """Two consecutive extractions must produce identical node sets."""
        url = _build_repo_url()
        cfg = _CONFIG
        doc1 = AiSbomExtractor().extract_from_repo(url, _REPO_REF, cfg)
        doc2 = AiSbomExtractor().extract_from_repo(url, _REPO_REF, cfg)
        assert sorted(n.name for n in doc1.nodes) == sorted(n.name for n in doc2.nodes), (
            "Extraction is non-deterministic: node names differ between two runs"
        )

    @skip_if_offline
    def test_json_serializable(self, doc: AiSbomDocument) -> None:
        from xelo.serializer import AiSbomSerializer

        json_str = AiSbomSerializer().to_json(doc)
        assert '"schema_version"' in json_str
        assert '"nodes"' in json_str

    @skip_if_offline
    def test_cyclonedx_output(self, doc: AiSbomDocument) -> None:
        from xelo.serializer import AiSbomSerializer

        cdx = AiSbomSerializer().to_cyclonedx(doc)
        assert cdx.get("bomFormat") == "CycloneDX"
        # CycloneDX components = AI nodes + package dep libraries
        assert len(cdx["components"]) >= len(doc.nodes)


# ---------------------------------------------------------------------------
# Snapshot — print summary when run with -s for manual inspection
# ---------------------------------------------------------------------------


@skip_if_offline
def test_print_summary(doc: AiSbomDocument) -> None:
    """Print a human-readable extraction summary (visible with pytest -s)."""
    print(f"\n{'=' * 60}")
    print("Healthcare Voice Agent — SBOM Extraction Summary")
    print(f"{'=' * 60}")
    print(f"Total nodes  : {len(doc.nodes)}")
    print(f"Total edges  : {len(doc.edges)}")
    print(f"Total evidence: {sum(len(n.evidence) for n in doc.nodes)}")
    print()
    by_type: dict[str, list[str]] = {}
    for node in sorted(doc.nodes, key=lambda n: (n.component_type.value, n.name)):
        nt = node.component_type.value
        by_type.setdefault(nt, []).append(
            f"  {node.name} "
            f"(conf={node.confidence:.2f}, "
            f"adapter={node.metadata.extras.get('adapter', '?')}, "
            f"provider={node.metadata.extras.get('provider', '')})"
        )
    for ctype, entries in sorted(by_type.items()):
        print(f"{ctype}:")
        for e in entries:
            print(e)
    print(f"{'=' * 60}")
