"""Tests for the LLM gap-fill category gating in nuguard.sbom.core.gap_fill.

Focused on _identify_absent_categories, since that's the piece extended in
this change (API_ENDPOINT added as a fallback-only category; AGENT relaxed
from a blanket exclusion to a narrower "hand-rolled orchestration only"
rule) — the LLM call itself is exercised indirectly via existing
integration coverage and is not re-tested here.
"""

from __future__ import annotations

from nuguard.sbom.core.gap_fill import GapFillBudget, GateReason, _identify_absent_categories
from nuguard.sbom.core.gap_fill.dedup import DedupContext
from nuguard.sbom.core.gap_fill.gating import (
    has_endpoint_registration_loop,
    identify_gated_categories,
    prompt_excluded_file_probe,
    tool_framework_diversity_probe,
)
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType


def _node(component_type: ComponentType, name: str = "x", confidence: float = 0.9, **extras) -> Node:
    return Node(
        name=name,
        component_type=component_type,
        confidence=confidence,
        metadata=NodeMetadata(extras=extras),
    )


def test_api_endpoint_gap_filled_when_absent() -> None:
    """A doc with zero API_ENDPOINT nodes should gap-fill that category —
    this is the fallback net for web frameworks with no AST adapter yet
    (e.g. NestJS)."""
    doc = AiSbomDocument(target=".", nodes=[_node(ComponentType.MODEL)])
    assert ComponentType.API_ENDPOINT in _identify_absent_categories(doc)


def test_api_endpoint_skipped_when_already_found() -> None:
    """Once the deterministic pass finds >=1 endpoint (a real adapter
    exists), gap-fill must not re-run for this category."""
    doc = AiSbomDocument(
        target=".",
        nodes=[_node(ComponentType.API_ENDPOINT, name="GET /health", confidence=0.9)],
    )
    assert ComponentType.API_ENDPOINT not in _identify_absent_categories(doc)


def test_agent_gap_filled_when_hand_rolled_no_framework() -> None:
    """Zero AGENT nodes and zero recognized AI-framework nodes means the
    orchestration (if any) is genuinely hand-rolled — this is exactly the
    case a framework-based adapter could never have caught, so gap-fill
    should be allowed to look for it."""
    doc = AiSbomDocument(target=".", nodes=[_node(ComponentType.MODEL)])
    assert ComponentType.AGENT in _identify_absent_categories(doc)


def test_agent_skipped_when_framework_node_present() -> None:
    """A recognized AI-framework node (e.g. langgraph) means deterministic
    AGENT detection already has ~97% recall — gap-fill must not re-trigger
    just because that AGENT node's own confidence is below the 0.65 bar."""
    doc = AiSbomDocument(
        target=".",
        nodes=[
            _node(ComponentType.FRAMEWORK, name="LangGraph", confidence=0.9, adapter="langgraph"),
        ],
    )
    assert ComponentType.AGENT not in _identify_absent_categories(doc)


def test_agent_skipped_when_agent_node_already_present() -> None:
    doc = AiSbomDocument(
        target=".",
        nodes=[_node(ComponentType.AGENT, name="Orchestrator", confidence=0.9)],
    )
    assert ComponentType.AGENT not in _identify_absent_categories(doc)


def test_agent_gap_fill_checks_framework_metadata_not_just_adapter() -> None:
    """The framework marker check must look at metadata.framework and
    canonical_name too, not just the adapter extras key, since different
    adapters populate different subsets of these fields."""
    doc = AiSbomDocument(
        target=".",
        nodes=[
            Node(
                name="crewai runtime",
                component_type=ComponentType.FRAMEWORK,
                confidence=0.9,
                metadata=NodeMetadata(framework="crewai"),
            )
        ],
    )
    assert ComponentType.AGENT not in _identify_absent_categories(doc)


def test_guardrail_and_privilege_never_gap_filled() -> None:
    doc = AiSbomDocument(target=".", nodes=[])
    absent = _identify_absent_categories(doc)
    assert ComponentType.GUARDRAIL not in absent
    assert ComponentType.PRIVILEGE not in absent


# ---------------------------------------------------------------------------
# PRIVILEGE / GUARDRAIL opt-in gating
# ---------------------------------------------------------------------------


def test_privilege_skipped_by_default_even_when_absent() -> None:
    doc = AiSbomDocument(target=".", nodes=[])
    decisions = identify_gated_categories(doc, {})
    assert decisions[ComponentType.PRIVILEGE] == GateReason.SKIPPED
    assert decisions[ComponentType.GUARDRAIL] == GateReason.SKIPPED


def test_privilege_absent_when_enabled_and_zero_nodes() -> None:
    doc = AiSbomDocument(target=".", nodes=[])
    decisions = identify_gated_categories(doc, {}, enable_privilege=True)
    assert decisions[ComponentType.PRIVILEGE] == GateReason.ABSENT
    # GUARDRAIL stays opted out independently
    assert decisions[ComponentType.GUARDRAIL] == GateReason.SKIPPED


def test_guardrail_absent_when_enabled_and_zero_nodes() -> None:
    doc = AiSbomDocument(target=".", nodes=[])
    decisions = identify_gated_categories(doc, {}, enable_guardrail=True)
    assert decisions[ComponentType.GUARDRAIL] == GateReason.ABSENT


def test_privilege_skipped_when_enabled_but_nodes_already_present() -> None:
    """Opted-in PRIVILEGE is ABSENT-only — never probed like TOOL/PROMPT/API_ENDPOINT."""
    doc = AiSbomDocument(target=".", nodes=[_node(ComponentType.PRIVILEGE, name="privilege:admin")])
    decisions = identify_gated_categories(doc, {}, enable_privilege=True)
    assert decisions[ComponentType.PRIVILEGE] == GateReason.SKIPPED


# ---------------------------------------------------------------------------
# Probe-signal functions
# ---------------------------------------------------------------------------


def test_endpoint_registration_loop_detected() -> None:
    content = (
        "const routeTable = [{method: 'get', path: '/a', handler: a}];\n"
        "for (const r of routeTable) {\n"
        "  app.get(r.path, r.handler);\n"
        "}\n"
    )
    assert has_endpoint_registration_loop({"app.ts": content}) is True


def test_endpoint_registration_loop_not_detected_for_plain_routes() -> None:
    content = "app.get('/a', handlerA);\napp.post('/b', handlerB);\n"
    assert has_endpoint_registration_loop({"app.ts": content}) is False


def test_api_endpoint_probed_when_registration_loop_present() -> None:
    doc = AiSbomDocument(
        target=".",
        nodes=[_node(ComponentType.API_ENDPOINT, name="GET /health", confidence=0.9)],
    )
    file_contents = {
        "app.ts": (
            "for (const r of routeTable) {\n"
            "  router.post(r.path, r.handler);\n"
            "}\n"
        )
    }
    decisions = identify_gated_categories(doc, file_contents)
    assert decisions[ComponentType.API_ENDPOINT] == GateReason.PROBE


def test_api_endpoint_skipped_when_present_and_no_loop_signal() -> None:
    doc = AiSbomDocument(
        target=".",
        nodes=[_node(ComponentType.API_ENDPOINT, name="GET /health", confidence=0.9)],
    )
    file_contents = {"app.ts": "app.get('/health', h);\n"}
    decisions = identify_gated_categories(doc, file_contents)
    assert decisions[ComponentType.API_ENDPOINT] == GateReason.SKIPPED


def test_tool_framework_diversity_probe_fires_with_many_frameworks_few_tools() -> None:
    doc = AiSbomDocument(
        target=".",
        nodes=[
            _node(ComponentType.FRAMEWORK, name="LangGraph"),
            _node(ComponentType.MODEL, name="gpt-4o"),
            _node(ComponentType.TOOL, name="search"),
        ],
    )
    assert tool_framework_diversity_probe(doc) is True


def test_tool_framework_diversity_probe_does_not_fire_with_balanced_tools() -> None:
    doc = AiSbomDocument(
        target=".",
        nodes=[
            _node(ComponentType.FRAMEWORK, name="LangGraph"),
            _node(ComponentType.MODEL, name="gpt-4o"),
            _node(ComponentType.TOOL, name="search"),
            _node(ComponentType.TOOL, name="calculator"),
        ],
    )
    assert tool_framework_diversity_probe(doc) is False


def test_prompt_excluded_file_probe_finds_excluded_prompt_files() -> None:
    doc = AiSbomDocument(target=".", nodes=[])
    file_contents = {
        "src/prompts.py": "SYSTEM = build_prompt()",
        "src/handler.py": "def handle(): pass",
    }
    candidates = prompt_excluded_file_probe(doc, file_contents)
    assert "src/prompts.py" in candidates
    assert "src/handler.py" not in candidates


def test_prompt_probe_gates_probe_reason_when_files_present() -> None:
    doc = AiSbomDocument(target=".", nodes=[])
    file_contents = {"src/prompt_templates.py": "TEMPLATE = build()"}
    decisions = identify_gated_categories(doc, file_contents)
    assert decisions[ComponentType.PROMPT] == GateReason.ABSENT  # zero nodes => ABSENT, not PROBE


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def test_dedup_exact_match_blocks_duplicate() -> None:
    doc = AiSbomDocument(target=".", nodes=[_node(ComponentType.TOOL, name="Redis", canonical_name="redis")])
    ctx = DedupContext(doc)
    assert ctx.check_and_register("redis", ComponentType.TOOL, fuzzy=False) is False


def test_dedup_fuzzy_endpoint_normalizes_path_params() -> None:
    doc = AiSbomDocument(
        target=".",
        nodes=[_node(ComponentType.API_ENDPOINT, name="GET /users/:id", canonical_name="get /users/:id")],
    )
    ctx = DedupContext(doc)
    is_new = ctx.check_and_register(
        "GET /users/{id}", ComponentType.API_ENDPOINT, fuzzy=True
    )
    assert is_new is False


def test_dedup_fuzzy_evidence_file_overlap_blocks_duplicate() -> None:
    doc = AiSbomDocument(
        target=".",
        nodes=[
            Node(
                name="System Prompt",
                component_type=ComponentType.PROMPT,
                confidence=0.85,
                metadata=NodeMetadata(
                    extras={"canonical_name": "system prompt", "evidence_files": ["src/prompts.py"]}
                ),
            )
        ],
    )
    ctx = DedupContext(doc)
    is_new = ctx.check_and_register(
        "Assistant Prompt",
        ComponentType.PROMPT,
        fuzzy=True,
        evidence_files=["src/prompts.py"],
    )
    assert is_new is False


def test_dedup_absent_path_has_no_fuzzy_collisions() -> None:
    """On the ABSENT path (fuzzy=False), a differently-named node is never
    treated as a duplicate — only exact canonical_name matches are checked."""
    doc = AiSbomDocument(
        target=".",
        nodes=[_node(ComponentType.TOOL, name="Redis Cache", canonical_name="redis cache")],
    )
    ctx = DedupContext(doc)
    assert ctx.check_and_register("redis-cache", ComponentType.TOOL, fuzzy=False) is True


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_budget_can_afford_within_call_cap() -> None:
    budget = GapFillBudget(max_calls=2, max_cost_usd=None)
    assert budget.can_afford(1) is True
    budget.record(1)
    assert budget.can_afford(1) is True
    budget.record(1)
    assert budget.can_afford(1) is False


def test_budget_exhausted_by_cost_cap() -> None:
    budget = GapFillBudget(max_calls=1000, max_cost_usd=0.001)
    assert budget.can_afford(1) is False
    assert budget.exhausted() is True


def test_budget_mark_exhausted_is_sticky() -> None:
    budget = GapFillBudget(max_calls=100, max_cost_usd=100.0)
    assert budget.exhausted() is False
    budget.mark_exhausted("test")
    assert budget.exhausted() is True
    assert budget.to_dict()["exhausted_at"] == "test"
