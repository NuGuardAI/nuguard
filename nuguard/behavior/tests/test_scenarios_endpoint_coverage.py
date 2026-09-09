"""Unit tests for _endpoint_coverage_scenarios' liveness-aware skip (Phase 1)."""
from __future__ import annotations

import uuid

from nuguard.behavior.models import IntentProfile
from nuguard.behavior.scenarios import _endpoint_coverage_scenarios
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


def _endpoint_node(path: str, *, operational: bool | None = None) -> Node:
    return Node(
        id=uuid.uuid5(_NS, f"API_ENDPOINT/{path}"),
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(
            endpoint=path,
            method="POST",
            accepts_user_input=True,
            chat_payload_key="message",
            operational=operational,
        ),
    )


def test_non_operational_endpoint_skipped_with_note() -> None:
    node = _endpoint_node("/api/chat/support", operational=False)
    sbom = AiSbomDocument(target="./app", nodes=[node])
    intent = IntentProfile(app_purpose="customer support")

    scenarios = _endpoint_coverage_scenarios(sbom, intent)

    assert scenarios == []


def test_operational_unknown_endpoint_unaffected() -> None:
    node = _endpoint_node("/api/chat/support", operational=None)
    sbom = AiSbomDocument(target="./app", nodes=[node])
    intent = IntentProfile(app_purpose="customer support")

    scenarios = _endpoint_coverage_scenarios(sbom, intent)

    assert len(scenarios) == 1


def test_operational_true_endpoint_unaffected() -> None:
    node = _endpoint_node("/api/chat/support", operational=True)
    sbom = AiSbomDocument(target="./app", nodes=[node])
    intent = IntentProfile(app_purpose="customer support")

    scenarios = _endpoint_coverage_scenarios(sbom, intent)

    assert len(scenarios) == 1
