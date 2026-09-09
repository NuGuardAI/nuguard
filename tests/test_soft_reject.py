from __future__ import annotations

from nuguard.analysis.plugins import nga_rules
from nuguard.common.soft_reject import (
    SOFT_REJECT_FLAG,
    is_soft_rejected,
    iter_effective_nodes,
    partition_node_counts,
)
from nuguard.policy import checker
from nuguard.sbom.models import Node, NodeMetadata
from nuguard.sbom.types import ComponentType


def _node(
    name: str,
    component_type: ComponentType,
    *,
    rejected: bool = False,
) -> Node:
    return Node(
        name=name,
        component_type=component_type,
        confidence=0.9,
        metadata=NodeMetadata(
            extras=(
                {
                    SOFT_REJECT_FLAG: True,
                }
                if rejected
                else {}
            )
        ),
    )


def test_soft_rejection_contract_supports_models_and_mappings() -> None:
    active = _node(
        "active-agent",
        ComponentType.AGENT,
    )
    rejected = _node(
        "rejected-agent",
        ComponentType.AGENT,
        rejected=True,
    )

    assert is_soft_rejected(active) is False
    assert is_soft_rejected(rejected) is True

    assert (
        is_soft_rejected(
            {
                "metadata": {
                    "extras": {
                        SOFT_REJECT_FLAG: True,
                    }
                }
            }
        )
        is True
    )

    # Require the canonical JSON boolean, not an arbitrary truthy string.
    assert (
        is_soft_rejected(
            {
                "metadata": {
                    "extras": {
                        SOFT_REJECT_FLAG: "true",
                    }
                }
            }
        )
        is False
    )


def test_effective_iterator_and_count_partition_preserve_audit_nodes() -> None:
    active_agent = _node(
        "active-agent",
        ComponentType.AGENT,
    )
    rejected_agent = _node(
        "rejected-agent",
        ComponentType.AGENT,
        rejected=True,
    )
    active_store = _node(
        "active-store",
        ComponentType.DATASTORE,
    )
    rejected_model = _node(
        "rejected-model",
        ComponentType.MODEL,
        rejected=True,
    )

    nodes = [
        rejected_agent,
        active_agent,
        rejected_model,
        active_store,
    ]

    assert list(iter_effective_nodes(nodes)) == [
        active_agent,
        active_store,
    ]

    partition = partition_node_counts(nodes)

    assert partition.effective == {
        "AGENT": 1,
        "DATASTORE": 1,
    }
    assert partition.soft_rejected == {
        "AGENT": 1,
        "MODEL": 1,
    }

    assert len(nodes) == 4
    assert rejected_agent in nodes
    assert rejected_model in nodes


def test_analysis_and_policy_use_the_shared_contract() -> None:
    assert nga_rules._is_soft_rejected is is_soft_rejected
    assert checker._is_soft_rejected is is_soft_rejected
