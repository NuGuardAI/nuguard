"""Tests for Phase 0 security invariant derivation (docs/claude-redteam-3.md §3)."""
from __future__ import annotations

from nuguard.models.policy import CognitivePolicy, HitlToolCondition
from nuguard.redteam.invariants import derive_security_invariants


def test_derive_from_empty_policy_yields_only_universal_invariants():
    invariants = derive_security_invariants(None)
    assert invariants
    assert all(i.source == "owasp-genai-principle" for i in invariants)


def test_derive_includes_one_invariant_per_restricted_topic():
    policy = CognitivePolicy(restricted_topics=["medical advice", "legal advice"])
    invariants = derive_security_invariants(policy)
    topic_invariants = [i for i in invariants if i.source == "policy:restricted_topics"]
    assert len(topic_invariants) == 2
    assert any("medical advice" in i.statement for i in topic_invariants)


def test_derive_includes_one_invariant_per_restricted_action():
    policy = CognitivePolicy(restricted_actions=["transfer funds"])
    invariants = derive_security_invariants(policy)
    action_invariants = [i for i in invariants if i.source == "policy:restricted_actions"]
    assert len(action_invariants) == 1
    assert "transfer funds" in action_invariants[0].statement


def test_derive_includes_hitl_tool_conditions():
    policy = CognitivePolicy(
        hitl_tool_conditions=[HitlToolCondition(tool_name="payment_tool", condition="amount exceeds $500")]
    )
    invariants = derive_security_invariants(policy)
    hitl_invariants = [i for i in invariants if i.source == "policy:hitl_tool_conditions"]
    assert len(hitl_invariants) == 1
    assert "payment_tool" in hitl_invariants[0].statement
    assert "amount exceeds $500" in hitl_invariants[0].statement


def test_derive_ids_are_unique_and_sequential():
    policy = CognitivePolicy(restricted_topics=["a"], restricted_actions=["b"])
    invariants = derive_security_invariants(policy)
    ids = [i.id for i in invariants]
    assert len(ids) == len(set(ids))
    assert ids[0] == "INV-01"
