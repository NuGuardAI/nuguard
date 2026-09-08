from __future__ import annotations

from types import SimpleNamespace

from nuguard.common.soft_reject import SOFT_REJECT_FLAG
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.sbom.models import (
    AiSbomDocument,
    Edge,
    Node,
    NodeMetadata,
    ScanSummary,
)
from nuguard.sbom.types import (
    ComponentType,
    RelationshipType,
)


def _node(
    name: str,
    component_type: ComponentType,
    *,
    rejected: bool = False,
    pii_fields: list[str] | None = None,
    datastore_type: str | None = None,
) -> Node:
    return Node(
        name=name,
        component_type=component_type,
        confidence=0.9,
        metadata=NodeMetadata(
            pii_fields=pii_fields,
            datastore_type=datastore_type,
            extras=(
                {
                    SOFT_REJECT_FLAG: True,
                }
                if rejected
                else {}
            ),
        ),
    )


def _document() -> tuple[
    AiSbomDocument,
    Node,
    Node,
    Node,
    Node,
]:
    rejected_store = _node(
        "False Chroma Utility",
        ComponentType.DATASTORE,
        rejected=True,
        pii_fields=[
            "fabricated_customer_ssn",
        ],
        datastore_type="chroma",
    )
    active_store = _node(
        "Customer Postgres",
        ComponentType.DATASTORE,
        pii_fields=[
            "email",
        ],
        datastore_type="postgres",
    )
    active_agent = _node(
        "Support Agent",
        ComponentType.AGENT,
    )
    rejected_agent = _node(
        "Rejected Hallucinated Agent",
        ComponentType.AGENT,
        rejected=True,
    )

    document = AiSbomDocument(
        target="soft-reject-redteam-fixture",
        nodes=[
            rejected_store,
            active_store,
            active_agent,
            rejected_agent,
        ],
        edges=[
            Edge(
                source=active_agent.id,
                target=active_store.id,
                relationship_type=(RelationshipType.ACCESSES),
            ),
            Edge(
                source=active_agent.id,
                target=rejected_store.id,
                relationship_type=(RelationshipType.ACCESSES),
            ),
            Edge(
                source=rejected_agent.id,
                target=active_store.id,
                relationship_type=(RelationshipType.ACCESSES),
            ),
        ],
        summary=ScanSummary(
            node_counts={
                "AGENT": 2,
                "DATASTORE": 2,
            }
        ),
    )

    return (
        document,
        rejected_store,
        active_store,
        active_agent,
        rejected_agent,
    )


def test_generator_builds_non_mutating_effective_sbom_view() -> None:
    (
        original,
        rejected_store,
        active_store,
        active_agent,
        rejected_agent,
    ) = _document()

    generator = ScenarioGenerator(original)

    # The source document remains complete for recall and audit.
    assert len(original.nodes) == 4
    assert len(original.edges) == 3
    assert rejected_store in original.nodes
    assert rejected_agent in original.nodes

    effective_names = {node.name for node in generator._sbom.nodes}

    assert effective_names == {
        "Customer Postgres",
        "Support Agent",
    }

    assert str(rejected_store.id) not in generator._node_by_id
    assert str(rejected_agent.id) not in generator._node_by_id

    assert len(generator._sbom.edges) == 1

    remaining_edge = generator._sbom.edges[0]

    assert remaining_edge.source == active_agent.id
    assert remaining_edge.target == active_store.id

    assert generator._sbom.summary is not None
    assert generator._sbom.summary.node_counts == {
        "AGENT": 1,
        "DATASTORE": 1,
    }
    assert generator._sbom.summary.node_counts_soft_rejected == {
        "AGENT": 1,
        "DATASTORE": 1,
    }


def test_rejected_fields_and_agents_are_absent_from_generator_input() -> None:
    (
        original,
        rejected_store,
        active_store,
        _active_agent,
        rejected_agent,
    ) = _document()

    generator = ScenarioGenerator(original)

    serialized = generator._sbom.model_dump_json()

    assert "fabricated_customer_ssn" not in serialized
    assert rejected_store.name not in serialized
    assert rejected_agent.name not in serialized

    assert active_store.name in serialized
    assert "email" in serialized

    effective_datastores = [
        node for node in generator._sbom.nodes if node.component_type == ComponentType.DATASTORE
    ]

    # Reproduces the first-match path reported in #511:
    # the first available datastore is now the confirmed one.
    assert len(effective_datastores) == 1
    assert effective_datastores[0].metadata.pii_fields == ["email"]

    # Generator filtering must never mutate the audit SBOM.
    assert "fabricated_customer_ssn" in original.model_dump_json()


def test_generator_preserves_canary_tenant_initialization() -> None:
    (
        document,
        _rejected_store,
        _active_store,
        _active_agent,
        _rejected_agent,
    ) = _document()

    without_canary = ScenarioGenerator(document)

    assert without_canary._real_tenant_id == ""

    canary_config = SimpleNamespace(
        tenants=[
            SimpleNamespace(
                tenant_id="tenant-primary",
            ),
            SimpleNamespace(
                tenant_id="tenant-secondary",
            ),
        ]
    )

    with_canary = ScenarioGenerator(
        document,
        canary_config=canary_config,
    )

    assert with_canary._real_tenant_id == "tenant-secondary"
