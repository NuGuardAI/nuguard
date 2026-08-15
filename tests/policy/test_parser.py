"""Tests for nuguard.policy.parser basic parsing correctness."""

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


def test_parse_policy_allowed_topics() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert policy.allowed_topics == ["Finance questions", "Account balance"]


def test_parse_policy_restricted_topics() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert policy.restricted_topics == ["Politics"]


def test_parse_policy_restricted_actions() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert policy.restricted_actions == ["Wire transfers"]


def test_parse_policy_hitl_keyword_trigger() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert policy.hitl_triggers == ["Suspicious activity"]


def test_parse_policy_hitl_tool_condition() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert len(policy.hitl_tool_conditions) == 1
    assert policy.hitl_tool_conditions[0].tool_name == "payment_tool"
    assert policy.hitl_tool_conditions[0].condition == "amount exceeds $500"


def test_parse_policy_data_classification() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert policy.data_classification == ["PII fields"]


def test_parse_policy_rate_limits() -> None:
    policy = parse_policy(_POLICY_TEXT)
    assert policy.rate_limits == {"requests_per_minute": 60}


def test_parse_policy_empty_doc() -> None:
    policy = parse_policy("# Cognitive Policy\n")
    assert policy.allowed_topics == []
    assert policy.restricted_topics == []
    assert policy.restricted_actions == []
    assert policy.hitl_triggers == []
    assert policy.rate_limits == {}


def test_parse_policy_unrecognised_section_preserved_verbatim() -> None:
    text = """\
# Cognitive Policy

## Agent Scope Restrictions
- The agent must only act on behalf of the authenticated session user.
"""
    policy = parse_policy(text)
    assert policy.raw_sections["Agent Scope Restrictions"] == [
        "The agent must only act on behalf of the authenticated session user."
    ]
