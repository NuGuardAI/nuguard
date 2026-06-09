from __future__ import annotations

from nuguard.models.policy import CognitivePolicy
from nuguard.policy.checker import check_policy_against_sbom
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, RateLimitDetail
from nuguard.sbom.types import ComponentType


def _doc_with(*nodes: Node) -> AiSbomDocument:
    return AiSbomDocument(target="test://policy-checker", nodes=list(nodes))


def test_rate_limit_check_accepts_schema_15_metadata() -> None:
    policy = CognitivePolicy(rate_limits={"requests_per_minute": 60})
    endpoint = Node(
        name="chat",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.95,
        metadata=NodeMetadata(
            endpoint="/chat",
            rate_limited=True,
            rate_limit_detail=RateLimitDetail(
                requests_per_minute=60,
                burst_size=10,
                enforcement_type="middleware",
            ),
        ),
    )

    result = check_policy_against_sbom(policy, _doc_with(endpoint))

    assert not [gap for gap in result.gaps if gap.check_id == "CHECK-004"]
    rate_limit_passes = [
        control for control in result.passed if control.check_id == "CHECK-004"
    ]
    assert len(rate_limit_passes) == 1
    assert any("rate_limited=True" in item for item in rate_limit_passes[0].evidence)
    assert any("rate_limit_detail" in item for item in rate_limit_passes[0].evidence)


def test_rate_limit_check_still_reports_missing_schema_15_metadata() -> None:
    policy = CognitivePolicy(rate_limits={"requests_per_minute": 60})
    endpoint = Node(
        name="chat",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.95,
        metadata=NodeMetadata(endpoint="/chat"),
    )

    result = check_policy_against_sbom(policy, _doc_with(endpoint))

    rate_limit_gaps = [gap for gap in result.gaps if gap.check_id == "CHECK-004"]
    assert len(rate_limit_gaps) == 1
    assert "rate_limited or rate_limit_detail" in rate_limit_gaps[0].message
    assert "legacy rate_limit" in rate_limit_gaps[0].searched[0]


def test_data_classification_check_accepts_schema_15_sensitive_field_metadata() -> None:
    policy = CognitivePolicy(data_classification=["PII fields: name, email"])
    datastore = Node(
        name="customer_db",
        component_type=ComponentType.DATASTORE,
        confidence=0.9,
        metadata=NodeMetadata(
            classified_tables=["customers"],
            classified_fields={"customers": ["name", "email"]},
            pii_fields=["name", "email"],
        ),
    )

    result = check_policy_against_sbom(policy, _doc_with(datastore))

    assert not [gap for gap in result.gaps if gap.check_id == "CHECK-003"]
    data_passes = [
        control for control in result.passed if control.check_id == "CHECK-003"
    ]
    assert len(data_passes) == 1
    assert any("classified_tables" in item for item in data_passes[0].evidence)
    assert any("classified_fields" in item for item in data_passes[0].evidence)
    assert any("pii_fields" in item for item in data_passes[0].evidence)
