from __future__ import annotations

from nuguard.models.policy import CognitivePolicy
from nuguard.policy.checker import check_policy_against_sbom
from nuguard.sbom.models import (
    AiSbomDocument,
    Evidence,
    Node,
    NodeMetadata,
    RateLimitDetail,
    SourceLocation,
)
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


def test_rate_limit_check_ignores_soft_rejected_api_endpoint() -> None:
    policy = CognitivePolicy(rate_limits={"requests_per_minute": 60})
    soft_rejected = Node(
        name="Generic",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.44,
        metadata=NodeMetadata(
            endpoint="/login",
            method="POST",
            extras={"llm_soft_rejected": True},
        ),
    )
    login = Node(
        name="Login",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.95,
        metadata=NodeMetadata(endpoint="/login", method="POST"),
    )

    result = check_policy_against_sbom(policy, _doc_with(soft_rejected, login))

    rate_limit_gaps = [gap for gap in result.gaps if gap.check_id == "CHECK-004"]
    assert len(rate_limit_gaps) == 1
    assert rate_limit_gaps[0].sbom_component == "Login"
    assert rate_limit_gaps[0].severity == "medium"
    assert "Generic" not in rate_limit_gaps[0].message


def test_rate_limit_check_ignores_route_less_generic_endpoint_candidate() -> None:
    policy = CognitivePolicy(rate_limits={"requests_per_minute": 60})
    generic = Node(
        name="Generic",
        component_type=ComponentType.API_ENDPOINT,
        confidence=1.0,
        metadata=NodeMetadata(
            descriptive_name="Generic API Endpoint",
            extras={"canonical_name": "api_endpoint_generic"},
        ),
    )
    login = Node(
        name="Login",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.95,
        metadata=NodeMetadata(endpoint="/login", method="POST"),
    )

    result = check_policy_against_sbom(policy, _doc_with(generic, login))

    rate_limit_gaps = [gap for gap in result.gaps if gap.check_id == "CHECK-004"]
    assert len(rate_limit_gaps) == 1
    assert rate_limit_gaps[0].sbom_component == "Login"


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


def test_restricted_action_check_uses_tool_description_terms() -> None:
    policy = CognitivePolicy(
        restricted_actions=[
            "access booking records for any user other than the authenticated user"
        ]
    )
    tool = Node(
        name="Lookup Reservation",
        component_type=ComponentType.TOOL,
        confidence=0.9,
        metadata=NodeMetadata(
            description="Retrieves reservation details from the booking system.",
        ),
        evidence=[
            Evidence(
                kind="ast_call",
                confidence=0.9,
                detail="openai_agents: @function_tool",
                location=SourceLocation(path="main.py", line=87),
            )
        ],
    )

    result = check_policy_against_sbom(policy, _doc_with(tool))

    restricted_gaps = [gap for gap in result.gaps if gap.check_id == "CHECK-002"]
    restricted_passes = [
        control for control in result.passed if control.check_id == "CHECK-002"
    ]
    assert not restricted_gaps
    assert len(restricted_passes) == 1
    assert "Lookup Reservation" in restricted_passes[0].evidence[0]
    assert "matched terms" in restricted_passes[0].evidence[0]


def test_restricted_action_check_does_not_match_single_broad_term() -> None:
    policy = CognitivePolicy(
        restricted_actions=["create or execute code, scripts, or database queries"]
    )
    tool = Node(
        name="Lookup Reservation",
        component_type=ComponentType.TOOL,
        confidence=0.9,
        metadata=NodeMetadata(
            description="Retrieves reservation details from the database.",
        ),
    )

    result = check_policy_against_sbom(policy, _doc_with(tool))

    restricted_gaps = [gap for gap in result.gaps if gap.check_id == "CHECK-002"]
    restricted_passes = [
        control for control in result.passed if control.check_id == "CHECK-002"
    ]
    assert len(restricted_gaps) == 1
    assert not restricted_passes
