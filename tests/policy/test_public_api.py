from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from nuguard.models.policy import CognitivePolicy
from nuguard.policy import (
    CognitivePolicyParseRequest,
    CognitivePolicyParseResult,
    parse_cognitive_policy,
)
from nuguard.policy.public_api import normalize_cognitive_policy


def test_parse_cognitive_policy_returns_validated_public_result() -> None:
    result = parse_cognitive_policy(
        CognitivePolicyParseRequest(markdown="## Allowed Topics\n- Customer support\n")
    )

    assert result.success is True
    assert result.policy is not None
    assert result.policy.allowed_topics == ["Customer support"]
    assert CognitivePolicyParseResult.model_validate_json(result.model_dump_json()) == result


def test_parse_cognitive_policy_returns_sanitized_failure() -> None:
    """Validation failures return stable diagnostics without source disclosure."""
    secret = "private-policy-content"
    validation_error = ValidationError.from_exception_data("CognitivePolicy", [])

    with patch("nuguard.policy.public_api.parse_policy", side_effect=validation_error):
        result = parse_cognitive_policy(CognitivePolicyParseRequest(markdown=secret))

    assert result.success is False
    assert result.errors[0].code == "policy_validation_failed"
    assert secret not in result.model_dump_json()


def test_parse_cognitive_policy_sanitizes_unexpected_parser_failure() -> None:
    """Unexpected parser errors do not disclose policy content or exception text."""
    secret = "private-policy-content"

    with patch(
        "nuguard.policy.public_api.parse_policy",
        side_effect=RuntimeError(f"parser failed for {secret}"),
    ):
        result = parse_cognitive_policy(CognitivePolicyParseRequest(markdown=secret))

    assert result.success is False
    assert result.errors[0].code == "policy_parse_failed"
    assert secret not in result.model_dump_json()


def test_normalize_cognitive_policy_accepts_model_and_parse_result() -> None:
    """Successful public parse results normalize to their policy model."""
    policy = CognitivePolicy(allowed_topics=["Support"])
    parse_result = CognitivePolicyParseResult(success=True, policy=policy)

    assert normalize_cognitive_policy(policy) is policy
    assert normalize_cognitive_policy(parse_result) is policy


def test_normalize_cognitive_policy_rejects_failed_parse_result() -> None:
    """Failed parse results cannot silently disable policy enforcement."""
    parse_result = CognitivePolicyParseResult(success=False)

    with pytest.raises(ValueError, match="did not produce a valid policy"):
        normalize_cognitive_policy(parse_result)