"""Regression test for issue #511: ScenarioGenerator must exclude nodes the
SBOM's own LLM verification pass soft-rejected (llm_soft_rejected=True), the
same way analysis/policy/behavior already do. Before this fix, ~40 call
sites in generator.py read ``self._sbom.nodes``/``self._node_by_id`` directly
with no such filter, so confirmed false positives (e.g. a mock model string
in a test fixture) could still drive attack-scenario synthesis.
"""
from __future__ import annotations

from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.sbom.models import AiSbomDocument, Node
from nuguard.sbom.types import ComponentType


def test_soft_rejected_node_excluded_from_generator_sbom():
    real_agent = Node(name="real-agent", component_type=ComponentType.AGENT, confidence=0.9)
    fake_model = Node(name="mock-model-fixture", component_type=ComponentType.MODEL, confidence=0.9)
    fake_model.metadata.extras["llm_soft_rejected"] = True
    sbom = AiSbomDocument(target="unit-test", nodes=[real_agent, fake_model])

    gen = ScenarioGenerator(sbom)

    assert str(real_agent.id) in gen._node_by_id
    assert str(fake_model.id) not in gen._node_by_id
    assert all(n.id != fake_model.id for n in gen._sbom.nodes)


def test_no_soft_rejected_nodes_leaves_sbom_untouched():
    agent = Node(name="agent", component_type=ComponentType.AGENT, confidence=0.9)
    sbom = AiSbomDocument(target="unit-test", nodes=[agent])

    gen = ScenarioGenerator(sbom)

    assert gen._sbom is sbom
    assert len(gen._sbom.nodes) == 1
