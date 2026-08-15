"""Tests for nuguard.policy.parser line-number/provenance tracking."""

from __future__ import annotations

from nuguard.policy.parser import parse_policy

_POLICY_TEXT = """\
# Cognitive Policy

## Allowed Topics
- Finance questions
- Account balance

## Restricted Topics
- Politics

## Restricted Actions
- Wire transfers

## HITL Triggers
- Suspicious activity
- payment_tool: amount exceeds $500

## Data Classification
- PII fields

## Rate Limits
- requests_per_minute: 60
"""


def test_parse_policy_default_source_path() -> None:
    policy = parse_policy(_POLICY_TEXT)
    loc = policy.item_evidence["allowed_topics:Finance questions"]
    assert loc.path == "cognitive_policy.md"


def test_parse_policy_custom_source_path() -> None:
    policy = parse_policy(_POLICY_TEXT, source_path="my-policy.md")
    for loc in policy.item_evidence.values():
        assert loc.path == "my-policy.md"


def test_parse_policy_records_correct_line_numbers() -> None:
    policy = parse_policy(_POLICY_TEXT, source_path="p.md")
    lines = _POLICY_TEXT.splitlines()

    assert lines[3] == "- Finance questions"
    loc = policy.item_evidence["allowed_topics:Finance questions"]
    assert loc.line == 4  # 1-based

    assert lines[4] == "- Account balance"
    loc2 = policy.item_evidence["allowed_topics:Account balance"]
    assert loc2.line == 5


def test_parse_policy_restricted_topics_evidence() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert "restricted_topics:Politics" in policy.item_evidence


def test_parse_policy_restricted_actions_evidence() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert "restricted_actions:Wire transfers" in policy.item_evidence


def test_parse_policy_hitl_keyword_trigger_evidence() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert "hitl_triggers:Suspicious activity" in policy.item_evidence


def test_parse_policy_hitl_tool_condition_evidence() -> None:
    policy = parse_policy(_POLICY_TEXT)
    key = "hitl_tool_conditions:payment_tool: amount exceeds $500"
    assert key in policy.item_evidence
    assert len(policy.hitl_tool_conditions) == 1
    assert policy.hitl_tool_conditions[0].tool_name == "payment_tool"


def test_parse_policy_data_classification_evidence() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert "data_classification:PII fields" in policy.item_evidence


def test_parse_policy_rate_limits_evidence() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert "rate_limits:requests_per_minute: 60" in policy.item_evidence
    assert policy.rate_limits["requests_per_minute"] == 60


def test_parse_policy_empty_doc_has_no_evidence() -> None:
    policy = parse_policy("# Cognitive Policy\n")
    assert policy.item_evidence == {}
