from __future__ import annotations

from nuguard.common.soft_reject import SOFT_REJECT_FLAG
from nuguard.sbom.core.application_summary import build_scan_summary
from nuguard.sbom.extractor.core import _refresh_summary_node_counts
from nuguard.sbom.extractor.postprocess import _make_scan_summary
from nuguard.sbom.models import (
    AiSbomDocument,
    Node,
    NodeMetadata,
    ScanSummary,
)
from nuguard.sbom.types import ComponentType


def _node(
    name: str,
    component_type: ComponentType,
    *,
    rejected: bool = False,
    framework: str | None = None,
    endpoint: str | None = None,
    data_classification: list[str] | None = None,
    classified_tables: list[str] | None = None,
) -> Node:
    return Node(
        name=name,
        component_type=component_type,
        confidence=0.9,
        metadata=NodeMetadata(
            framework=framework,
            endpoint=endpoint,
            data_classification=(data_classification or []),
            classified_tables=(classified_tables or []),
            extras=(
                {
                    SOFT_REJECT_FLAG: True,
                }
                if rejected
                else {}
            ),
        ),
    )


def _nodes() -> list[Node]:
    return [
        _node(
            "active-agent",
            ComponentType.AGENT,
        ),
        _node(
            "active-store",
            ComponentType.DATASTORE,
            data_classification=[
                "PII",
            ],
            classified_tables=[
                "customers",
            ],
        ),
        _node(
            "langgraph",
            ComponentType.FRAMEWORK,
            framework="langgraph",
        ),
        _node(
            "POST /chat",
            ComponentType.API_ENDPOINT,
            endpoint="/chat",
        ),
        _node(
            "false-chroma",
            ComponentType.DATASTORE,
            rejected=True,
            data_classification=[
                "PHI",
            ],
            classified_tables=[
                "fabricated_records",
            ],
        ),
        _node(
            "false-crewai",
            ComponentType.FRAMEWORK,
            rejected=True,
            framework="crewai",
        ),
        _node(
            "POST /fabricated",
            ComponentType.API_ENDPOINT,
            rejected=True,
            endpoint="/fabricated",
        ),
    ]


def test_deterministic_summary_uses_effective_nodes_and_reports_rejections() -> None:
    nodes = _nodes()

    summary = build_scan_summary(
        nodes,
        [],
    )

    assert summary["node_type_counts"] == {
        "AGENT": 1,
        "API_ENDPOINT": 1,
        "DATASTORE": 1,
        "FRAMEWORK": 1,
    }

    assert summary["node_type_counts_soft_rejected"] == {
        "API_ENDPOINT": 1,
        "DATASTORE": 1,
        "FRAMEWORK": 1,
    }

    assert "langgraph" in summary["frameworks"]
    assert "crewai" not in summary["frameworks"]

    assert "/chat" in summary["api_endpoints"]
    assert "/fabricated" not in summary["api_endpoints"]

    assert summary["data_classification"] == [
        "PII",
    ]

    assert "fabricated_records" not in summary["classified_tables"]

    # Summary construction must not remove retained audit nodes.
    assert len(nodes) == 7


def test_typed_summary_maps_both_count_views() -> None:
    summary = _make_scan_summary(
        build_scan_summary(
            _nodes(),
            [],
        )
    )

    assert summary.node_counts == {
        "AGENT": 1,
        "API_ENDPOINT": 1,
        "DATASTORE": 1,
        "FRAMEWORK": 1,
    }

    assert summary.node_counts_soft_rejected == {
        "API_ENDPOINT": 1,
        "DATASTORE": 1,
        "FRAMEWORK": 1,
    }


def test_final_refresh_corrects_stale_counts_without_removing_nodes() -> None:
    nodes = _nodes()

    document = AiSbomDocument(
        target="soft-reject-fixture",
        nodes=nodes,
        summary=ScanSummary(
            node_counts={
                "AGENT": 99,
                "DATASTORE": 99,
            },
            node_counts_soft_rejected={},
        ),
    )

    _refresh_summary_node_counts(document)

    assert len(document.nodes) == 7
    assert document.summary is not None

    assert document.summary.node_counts == {
        "AGENT": 1,
        "API_ENDPOINT": 1,
        "DATASTORE": 1,
        "FRAMEWORK": 1,
    }

    assert document.summary.node_counts_soft_rejected == {
        "API_ENDPOINT": 1,
        "DATASTORE": 1,
        "FRAMEWORK": 1,
    }


def test_older_summaries_default_rejected_counts_to_empty() -> None:
    summary = ScanSummary.model_validate(
        {
            "node_counts": {
                "AGENT": 1,
            }
        }
    )

    assert summary.node_counts_soft_rejected == {}
