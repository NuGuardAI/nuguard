"""Public Pydantic contracts for parsing cognitive-policy Markdown."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from nuguard.models.policy import CognitivePolicy
from nuguard.policy.parser import parse_policy


class CognitivePolicyParseRequest(BaseModel):
    """JSON-safe cognitive-policy Markdown input."""

    markdown: str


class CognitivePolicyParseDetail(BaseModel):
    """Stable sanitized parser diagnostic."""

    code: str
    message: str
    severity: Literal["error", "warning"]


class CognitivePolicyParseResult(BaseModel):
    """Validated policy and stable parser diagnostics."""

    success: bool
    policy: CognitivePolicy | None = None
    errors: list[CognitivePolicyParseDetail] = Field(default_factory=list)
    warnings: list[CognitivePolicyParseDetail] = Field(default_factory=list)


def parse_cognitive_policy(request: CognitivePolicyParseRequest) -> CognitivePolicyParseResult:
    """Parse Markdown without exposing source text through validation details."""
    try:
        policy = parse_policy(request.markdown)
    except ValidationError:
        detail = CognitivePolicyParseDetail(
            code="policy_validation_failed",
            message="The parsed cognitive policy is invalid",
            severity="error",
        )
        return CognitivePolicyParseResult(success=False, errors=[detail])
    except Exception:  # noqa: BLE001
        detail = CognitivePolicyParseDetail(
            code="policy_parse_failed",
            message="The cognitive policy could not be parsed",
            severity="error",
        )
        return CognitivePolicyParseResult(success=False, errors=[detail])
    return CognitivePolicyParseResult(success=True, policy=policy)


def normalize_cognitive_policy(value: Any) -> Any:
    """Unwrap a public parse result while preserving existing collaborators."""
    if isinstance(value, CognitivePolicyParseResult):
        if value.success and value.policy is not None:
            return value.policy
        raise ValueError("Cognitive policy parsing did not produce a valid policy")
    return value