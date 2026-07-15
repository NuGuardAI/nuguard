"""Regression tests: alignment.py's static findings must no longer carry an
inline templated `remediation` string — that text is now synthesized
per-finding by RemediationSynthesizer and backfilled after analysis, not
baked into check_alignment()'s finding-construction call sites."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from nuguard.behavior.alignment import check_alignment
from nuguard.behavior.analyzer import BehaviorAnalyzer
from nuguard.behavior.models import IntentProfile
from nuguard.config import BehaviorConfig
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


def _node(name: str, ctype: ComponentType, **meta_kwargs: object) -> Node:
    nid = uuid.uuid5(_NS, f"{ctype.value}/{name}")
    return Node(
        id=nid,
        name=name,
        component_type=ctype,
        confidence=1.0,
        metadata=NodeMetadata(**meta_kwargs),
    )


def _policy() -> MagicMock:
    policy = MagicMock()
    policy.restricted_topics = []
    policy.allowed_topics = []
    policy.restricted_actions = []
    policy.hitl_triggers = []
    policy.data_classification = []
    return policy


def _sbom(*nodes: Node) -> AiSbomDocument:
    return AiSbomDocument(target="./app", nodes=list(nodes), edges=[])


def test_check_alignment_finding_has_no_inline_remediation() -> None:
    """check_alignment() itself must not populate `remediation` — that's now
    the RemediationSynthesizer's job, run afterward by BehaviorAnalyzer."""
    ep = _node("/api/records", ComponentType.API_ENDPOINT, auth_required=False, returns_sensitive_data=True)
    sbom = _sbom(ep)
    findings = check_alignment(sbom, IntentProfile(app_purpose="test app"), _policy())

    ba9 = [f for f in findings if f.finding_id.startswith("BA-009")]
    assert ba9, "Expected BA-009 finding for unprotected sensitive endpoint"
    assert ba9[0].remediation is None


@pytest.mark.asyncio
async def test_analyze_backfills_remediation_onto_static_findings() -> None:
    """BehaviorAnalyzer.analyze() must backfill each static finding's
    `remediation` from the synthesized RemediationArtefact plan, even with no
    LLM configured (falls back to the synthesizer's deterministic template)."""
    ep = _node("/api/records", ComponentType.API_ENDPOINT, auth_required=False, returns_sensitive_data=True)
    sbom = _sbom(ep)
    config = BehaviorConfig(target="http://localhost:9999")

    analyzer = BehaviorAnalyzer(config=config, sbom=sbom, policy=_policy(), llm_client=None)
    result = await analyzer.analyze(mode="static")

    ba9 = [f for f in result.static_findings if str(f.get("finding_id", "")).startswith("BA-009")]
    assert ba9, "Expected BA-009 finding for unprotected sensitive endpoint"
    assert ba9[0]["remediation"], "remediation should be backfilled after analyze() completes"
