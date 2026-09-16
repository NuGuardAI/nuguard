"""Tests for BehaviorAnalyzer._confirmed_endpoint_from_sbom.

Regression coverage for reusing a runtime-probe-confirmed chat endpoint from
the enriched SBOM instead of re-probing it live on every behavior run —
mirrors RedteamOrchestrator._chat_endpoint_confirmed.
"""
from __future__ import annotations

import uuid

from nuguard.behavior.analyzer import BehaviorAnalyzer
from nuguard.config import BehaviorConfig
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


def _endpoint_node(path: str, **meta_kwargs: object) -> Node:
    nid = uuid.uuid5(_NS, f"API_ENDPOINT/{path}")
    return Node(
        id=nid,
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(endpoint=path, method="POST", **meta_kwargs),
    )


def _analyzer(sbom: AiSbomDocument | None) -> BehaviorAnalyzer:
    config = BehaviorConfig(target="http://localhost:9999")
    return BehaviorAnalyzer(config=config, sbom=sbom, llm_client=None)


def test_confirmed_when_node_has_runtime_probe_source_and_payload_key() -> None:
    node = _endpoint_node(
        "/api/chat",
        chat_payload_key="prompt",
        chat_payload_list=False,
        response_text_key="answer",
        extras={"source": "runtime_probe"},
    )
    analyzer = _analyzer(AiSbomDocument(target="./app", nodes=[node]))

    result = analyzer._confirmed_endpoint_from_sbom("/api/chat")

    assert result == ("/api/chat", "prompt", False, "answer")


def test_not_confirmed_when_source_is_auto_enrichment() -> None:
    node = _endpoint_node(
        "/api/chat",
        chat_payload_key="prompt",
        extras={"source": "auto_enrichment"},
    )
    analyzer = _analyzer(AiSbomDocument(target="./app", nodes=[node]))

    assert analyzer._confirmed_endpoint_from_sbom("/api/chat") is None


def test_not_confirmed_when_no_chat_payload_key() -> None:
    node = _endpoint_node("/api/chat", extras={"source": "runtime_probe"})
    analyzer = _analyzer(AiSbomDocument(target="./app", nodes=[node]))

    assert analyzer._confirmed_endpoint_from_sbom("/api/chat") is None


def test_not_confirmed_when_path_does_not_match() -> None:
    node = _endpoint_node(
        "/api/chat",
        chat_payload_key="prompt",
        extras={"source": "runtime_probe"},
    )
    analyzer = _analyzer(AiSbomDocument(target="./app", nodes=[node]))

    assert analyzer._confirmed_endpoint_from_sbom("/api/other") is None


def test_not_confirmed_when_no_sbom() -> None:
    analyzer = _analyzer(None)

    assert analyzer._confirmed_endpoint_from_sbom("/api/chat") is None
