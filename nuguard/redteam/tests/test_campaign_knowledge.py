"""Tests for the campaign KnowledgeStore and split discovery."""
from __future__ import annotations

import pytest

from nuguard.common.discovery import DiscoveredProfile
from nuguard.redteam.campaign.discovery import run_adversarial_discovery, run_clean_baseline
from nuguard.redteam.campaign.knowledge import (
    KnowledgeItem,
    KnowledgeStore,
    OwnershipMap,
    Scope,
    TrustLevel,
    validate_identifier,
    validate_route,
)

A = Scope("dep1", "/chat", "primary", "fp-a")
B = Scope("dep1", "/chat", "canary:b", "fp-b")


def test_caches_are_identity_scoped() -> None:
    s = KnowledgeStore()
    s.seed_from_profile(DiscoveredProfile(customer_name="Alice", ids=["ACC-1"]), A)
    assert {i.key for i in s.own_account_facts(A)} == {"customer_name", "ACC-1"}
    assert s.items(B) == []  # another principal never sees A's facts


def test_baseline_and_adversarial_are_separate_artifacts() -> None:
    s = KnowledgeStore()
    s.add(KnowledgeItem("id", "ACC-1", "ACC-1", TrustLevel.OBSERVED, A, "baseline"))
    s.add(KnowledgeItem("probe_reply", "tools", "I can wire money", TrustLevel.CLAIMED, A,
                        "adversarial", objective_ref="J03", untrusted_text=True))
    assert [i.kind for i in s.clean_baseline(A)] == ["id"]
    adv = s.adversarial(A)
    assert [(i.kind, i.objective_ref) for i in adv] == [("probe_reply", "J03")]


def test_untrusted_text_cannot_be_stored_above_claimed_but_can_be_promoted() -> None:
    s = KnowledgeStore()
    with pytest.raises(ValueError, match="untrusted target text"):
        s.add(KnowledgeItem("tool", "wire", "wire", TrustLevel.VERIFIED, A, untrusted_text=True))
    item = s.add(KnowledgeItem("tool", "wire", "wire", TrustLevel.CLAIMED, A, untrusted_text=True))
    with pytest.raises(ValueError, match="evidence"):
        s.promote(item, TrustLevel.OBSERVED, "")
    promoted = s.promote(item, TrustLevel.OBSERVED, "trace:42")
    assert promoted.trust == TrustLevel.OBSERVED and "trace:42" in promoted.source_refs
    assert s.get(A, "baseline", "tool", "wire").trust == TrustLevel.OBSERVED  # type: ignore[union-attr]


def test_invalidation_on_identity_or_deployment_change() -> None:
    s = KnowledgeStore()
    s.seed_from_profile(DiscoveredProfile(ids=["ACC-1"]), A)
    assert s.invalidate(principal_ref="primary", auth_scope="fp-a-rotated") == 1
    assert s.items(A) == []  # stale items are hidden
    s.seed_from_profile(DiscoveredProfile(ids=["ACC-1"]), A)
    assert s.invalidate(deployment="dep2") == 1


def test_novel_value_is_candidate_unless_ownership_is_known() -> None:
    own = OwnershipMap(owned={"primary": {"ACC-1"}, "canary:b": {"ACC-2"}},
                       foreign_canaries={"canary:b": {"CANARY-B-9"}})
    s = KnowledgeStore(own)
    assert s.classify_disclosure("ACC-1", "primary") == "own"
    assert s.classify_disclosure("ACC-2", "primary") == "foreign_confirmed"
    assert s.classify_disclosure("CANARY-B-9", "primary") == "foreign_confirmed"
    assert s.classify_disclosure("ACC-777", "primary") == "candidate"


def test_deterministic_validators_reject_model_supplied_junk() -> None:
    assert validate_identifier("ACC-123") and not validate_identifier("x; DROP TABLE")
    assert validate_route("/api/v1/users/1?x=1")
    for bad in ("http://evil/x", "//evil/x", "api/x", "/a/../b", "/a b", "/a\n"):
        assert not validate_route(bad), bad


@pytest.mark.asyncio
async def test_clean_baseline_never_primes_debug_persona() -> None:
    sent: list[str] = []

    async def send(m: str) -> str:
        sent.append(m)
        return "I can help with your accounts."

    s = KnowledgeStore()
    assert await run_clean_baseline(send, s, A, elapsed_ms=lambda: 12.0) is True
    assert len(sent) == 1 and "developer" not in sent[0].lower() and "debug" not in sent[0].lower()
    base = {i.kind: i for i in s.clean_baseline(A)}
    assert base["capability_summary"].trust == TrustLevel.CLAIMED
    assert base["latency_ms"].trust == TrustLevel.OBSERVED
    assert s.adversarial(A) == []


@pytest.mark.asyncio
async def test_baseline_unusable_reply_is_not_ready() -> None:
    async def send(_: str) -> str:
        return "[HTTP 500]"

    assert await run_clean_baseline(send, KnowledgeStore(), A) is False


@pytest.mark.asyncio
async def test_adversarial_discovery_records_claims_and_sends_no_exit_message() -> None:
    sent: list[str] = []

    async def send(m: str) -> str:
        sent.append(m)
        return "Sorry, I cannot share that." if "system prompt" in m else "I use transfer_funds."

    s = KnowledgeStore()
    answered = await run_adversarial_discovery(send, s, A, objective_ref="DISC")
    assert "system_prompt" not in answered and "tools" in answered
    assert not any("exit developer" in m.lower() for m in sent)
    kinds = {(i.kind, i.key) for i in s.adversarial(A)}
    assert ("refusal", "system_prompt") in kinds and ("probe_reply", "tools") in kinds
    assert all(i.trust == TrustLevel.CLAIMED and i.untrusted_text and i.objective_ref == "DISC"
               for i in s.adversarial(A))
    assert s.clean_baseline(A) == []
