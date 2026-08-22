"""Unit tests for AgentOrchestratorTSAdapter (docs/sbom-fix2.md #6).

Covers the hand-rolled multi-agent orchestration heuristic: a base class
whose >=2 subclasses are instantiated and invoked in sequence from one
orchestrating class emits a real AGENT node — with evidence at both the
sequencing call site and the base class's own definition site — instead
of leaving only the generic empty-evidence fallback placeholder.
"""

from __future__ import annotations

from nuguard.sbom.adapters.typescript.agent_orchestrator import (
    AgentOrchestratorTSAdapter,
    collect_class_hierarchy,
)
from nuguard.sbom.types import ComponentType

_BASE_AGENT_SRC = """
export abstract class BaseAgent {
  abstract run(input: string): Promise<string>;
}
"""

_SUBCLASSES_SRC = """
export class AnalysisAgent extends BaseAgent {
  async run(input: string) { return this.model.call(input); }
}
export class SolverAgent extends BaseAgent {
  async run(input: string) { return this.model.call(input); }
}
"""

_ORCHESTRATOR_SRC = """
export class ProblemSolverService {
  async solve(problem: string) {
    const analysis = await new AnalysisAgent().run(problem);
    const solution = await new SolverAgent().run(analysis);
    return solution;
  }
}
"""


def _build_adapter() -> AgentOrchestratorTSAdapter:
    adapter = AgentOrchestratorTSAdapter()
    bases: dict[str, str] = {}
    locations: dict[str, tuple[str, int]] = {}
    for src, path in (
        (_BASE_AGENT_SRC, "base.agent.ts"),
        (_SUBCLASSES_SRC, "agents.ts"),
        (_ORCHESTRATOR_SRC, "problem-solver.service.ts"),
    ):
        b, loc = collect_class_hierarchy(src, path)
        bases.update(b)
        locations.update(loc)
    adapter.set_global_class_hierarchy(bases, locations)
    return adapter


class TestSequentialOrchestrationDetected:
    def test_real_agent_node_emitted_with_two_evidence_locations(self) -> None:
        adapter = _build_adapter()
        dets = adapter.extract(
            _ORCHESTRATOR_SRC, "problem-solver.service.ts", None
        )
        agent_dets = [d for d in dets if d.component_type == ComponentType.AGENT]

        assert len(agent_dets) == 2, agent_dets
        assert all(d.canonical_name == agent_dets[0].canonical_name for d in agent_dets)
        assert all(d.display_name == "ProblemSolverService" for d in agent_dets)

        files = {d.file_path for d in agent_dets}
        assert files == {"problem-solver.service.ts", "base.agent.ts"}
        assert all(d.snippet for d in agent_dets), "evidence must not be empty"

    def test_metadata_records_base_class_and_subclasses(self) -> None:
        adapter = _build_adapter()
        dets = adapter.extract(
            _ORCHESTRATOR_SRC, "problem-solver.service.ts", None
        )
        agent_det = next(d for d in dets if d.component_type == ComponentType.AGENT)
        assert agent_det.metadata["base_class"] == "BaseAgent"
        assert set(agent_det.metadata["subclasses"]) == {"AnalysisAgent", "SolverAgent"}


_DI_ORCHESTRATOR_SRC = """
@Injectable()
export class ProblemSolverService {
  constructor(
    private readonly db: DatabaseService,
    private readonly aiService: AiService,
    private readonly analysisAgent: AnalysisAgent,
    private readonly solverAgent: SolverAgent,
  ) {}

  async solve(problem: string) {
    const analysisResult = await this.analysisAgent.execute(context);
    const solverResult = await this.solverAgent.execute(context);
    return solverResult;
  }
}
"""


class TestConstructorInjectedOrchestrationDetected:
    """The real-world pattern (Studyield's ProblemSolverService): agent
    subclasses are constructor-injected (NestJS DI), not `new`-instantiated,
    and invoked later via `this.<propName>.execute(...)`."""

    def test_di_injected_agents_detected_with_invocation_site_evidence(self) -> None:
        adapter = _build_adapter()
        dets = adapter.extract(
            _DI_ORCHESTRATOR_SRC, "problem-solver.service.ts", None
        )
        agent_dets = [d for d in dets if d.component_type == ComponentType.AGENT]

        assert len(agent_dets) == 2, agent_dets
        assert all(d.display_name == "ProblemSolverService" for d in agent_dets)
        primary = next(d for d in agent_dets if d.file_path == "problem-solver.service.ts")
        assert "this.analysisAgent.execute(" in primary.snippet or "this.solverAgent.execute(" in primary.snippet

    def test_di_injected_but_never_invoked_not_flagged(self) -> None:
        adapter = _build_adapter()
        code = (
            "@Injectable()\n"
            "export class ProblemSolverService {\n"
            "  constructor(\n"
            "    private readonly analysisAgent: AnalysisAgent,\n"
            "    private readonly solverAgent: SolverAgent,\n"
            "  ) {}\n"
            "}\n"
        )
        dets = adapter.extract(code, "problem-solver.service.ts", None)
        assert not any(d.component_type == ComponentType.AGENT for d in dets)


class TestNegativeCases:
    def test_no_class_hierarchy_index_is_a_no_op(self) -> None:
        adapter = AgentOrchestratorTSAdapter()  # never wired
        dets = adapter.extract(_ORCHESTRATOR_SRC, "problem-solver.service.ts", None)
        assert dets == []

    def test_single_subclass_instantiation_not_flagged(self) -> None:
        """Needs >=2 distinct sequenced subclasses, not just one."""
        adapter = _build_adapter()
        code = (
            "export class SingleAgentRunner {\n"
            "  async run(input: string) {\n"
            "    return new AnalysisAgent().run(input);\n"
            "  }\n"
            "}\n"
        )
        dets = adapter.extract(code, "single.service.ts", None)
        assert not any(d.component_type == ComponentType.AGENT for d in dets)

    def test_non_agent_named_base_class_not_flagged(self) -> None:
        """The base-class naming gate (must contain 'agent') keeps ordinary
        OOP hierarchies (e.g. a Strategy pattern) from false-positiving."""
        adapter = AgentOrchestratorTSAdapter()
        bases: dict[str, str] = {}
        locations: dict[str, tuple[str, int]] = {}
        strategies_src = (
            "export abstract class BaseStrategy {}\n"
            "export class DiscountStrategy extends BaseStrategy {}\n"
            "export class TaxStrategy extends BaseStrategy {}\n"
        )
        b, loc = collect_class_hierarchy(strategies_src, "strategies.ts")
        bases.update(b)
        locations.update(loc)
        adapter.set_global_class_hierarchy(bases, locations)

        code = (
            "export class PricingService {\n"
            "  compute(input: number) {\n"
            "    const a = new DiscountStrategy().apply(input);\n"
            "    const b = new TaxStrategy().apply(a);\n"
            "    return b;\n"
            "  }\n"
            "}\n"
        )
        dets = adapter.extract(code, "pricing.service.ts", None)
        assert not any(d.component_type == ComponentType.AGENT for d in dets)

    def test_no_call_after_instantiation_not_flagged(self) -> None:
        adapter = _build_adapter()
        code = (
            "export class Holder {\n"
            "  setup() {\n"
            "    const a = new AnalysisAgent();\n"
            "    const s = new SolverAgent();\n"
            "  }\n"
            "}\n"
        )
        dets = adapter.extract(code, "holder.service.ts", None)
        assert not any(d.component_type == ComponentType.AGENT for d in dets)


class TestCollectClassHierarchy:
    def test_extends_relationship_and_location_recorded(self) -> None:
        bases, locations = collect_class_hierarchy(_SUBCLASSES_SRC, "agents.ts")
        assert bases["AnalysisAgent"] == "BaseAgent"
        assert bases["SolverAgent"] == "BaseAgent"
        assert locations["AnalysisAgent"][0] == "agents.ts"

    def test_base_class_without_extends_has_no_bases_entry(self) -> None:
        bases, locations = collect_class_hierarchy(_BASE_AGENT_SRC, "base.agent.ts")
        assert "BaseAgent" not in bases
        assert locations["BaseAgent"] == ("base.agent.ts", 2)
