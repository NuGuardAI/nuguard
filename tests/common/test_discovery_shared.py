"""Tests for the shared discovery/golden-data routine used by both behavior and redteam.

Covers ``nuguard.common.discovery.run_discovery`` (live discovery + identity-candidate
retry) and ``profile_from_golden_data`` (config-supplied golden_data fallback) — the
single implementations that replaced three previously-duplicated copies.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nuguard.common.discovery import (
    DiscoveredProfile,
    DiscoveryOutcome,
    DiscoveryRequest,
    profile_from_golden_data,
    run_discovery,
)


def _make_client(responses: list[str], payload_extras: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.send = AsyncMock(side_effect=[(r, {}) for r in responses])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client._chat_payload_extras = payload_extras if payload_extras is not None else {}
    return client


def _make_session(session_id: str = "test-session") -> MagicMock:
    session = MagicMock()
    session.session_id = session_id
    session.target_url = "http://app.test"
    session.chain_id = "test-chain"
    return session


# ---------------------------------------------------------------------------
# DiscoveryRequest / DiscoveryOutcome — JSON-safety
# ---------------------------------------------------------------------------


def test_discovery_request_is_json_serializable():
    req = DiscoveryRequest(use_case="banking", max_turns=2, fallback_endpoints=[("/chat", "message", False, None)])
    payload = req.model_dump_json()
    restored = DiscoveryRequest.model_validate_json(payload)
    assert restored.use_case == "banking"
    assert restored.max_turns == 2
    assert restored.fallback_endpoints == [("/chat", "message", False, None)]


def test_discovery_outcome_is_json_serializable():
    profile = DiscoveredProfile(customer_name="Alice Johnson", ids=["ACCT-001"], source="live")
    outcome = DiscoveryOutcome(profile=profile, notes=["a note"])
    payload = outcome.model_dump_json()
    restored = DiscoveryOutcome.model_validate_json(payload)
    assert restored.profile.customer_name == "Alice Johnson"
    assert restored.profile.source == "live"
    assert restored.notes == ["a note"]


def test_discovered_profile_constructible_from_plain_dict():
    data = {"customer_name": "Bob Smith", "ids": ["ACCT-002"], "source": "config"}
    profile = DiscoveredProfile.model_validate(data)
    assert profile.customer_name == "Bob Smith"
    assert not profile.is_empty


# ---------------------------------------------------------------------------
# run_discovery — live conversation, no retry needed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_discovery_live_hit_sets_source_live():
    client = _make_client(["Account holder: Alice Johnson. Account ID: ACCT-001."])
    session = _make_session()

    outcome = await run_discovery(client, session, DiscoveryRequest(use_case="banking"))

    assert outcome.profile.customer_name == "Alice Johnson"
    assert "ACCT-001" in outcome.profile.ids
    assert outcome.profile.source == "live"
    assert outcome.notes == []


@pytest.mark.asyncio
async def test_run_discovery_all_empty_sets_source_none():
    client = _make_client(["Sorry, I can't help with that.", "I cannot access that.", "I don't have that info."])
    session = _make_session()

    outcome = await run_discovery(client, session, DiscoveryRequest(use_case="", max_turns=3))

    assert outcome.profile.is_empty
    assert outcome.profile.source == "none"


# ---------------------------------------------------------------------------
# run_discovery — identity-candidate retry (upgraded behavior for both callers)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_discovery_retries_identity_candidates_on_empty_profile():
    """First attempt (user_id=alice.johnson@bank.com) yields nothing; the second
    candidate (user_id=alice) should be retried and produce a profile."""
    client = _make_client(
        responses=[
            "Sorry, I don't have any data for that account.",  # initial main turn: empty
            "I still cannot access your account information.",  # initial capability probe: empty
            "Account holder: Alice Johnson. Account ID: ACCT-001.",  # retry main turn: hit
        ],
        payload_extras={
            "user_id": "alice.johnson@bank.com",
            "__user_id_candidates__": ["alice.johnson@bank.com", "alice.johnson", "alice"],
        },
    )
    session = _make_session()

    outcome = await run_discovery(client, session, DiscoveryRequest(use_case="banking", max_turns=1))

    assert outcome.profile.customer_name == "Alice Johnson"
    assert outcome.profile.source == "live"
    assert any("retrying" in n.lower() for n in outcome.notes)
    # The internal marker key must be stripped so it's never sent as a real payload field.
    assert "__user_id_candidates__" not in client._chat_payload_extras
    # The winning candidate should be left in place for subsequent requests.
    assert client._chat_payload_extras["user_id"] == "alice.johnson"


@pytest.mark.asyncio
async def test_run_discovery_marker_stripped_even_without_retry_need():
    client = _make_client(
        responses=["Account holder: Alice Johnson. Account ID: ACCT-001."],
        payload_extras={
            "user_id": "alice",
            "__user_id_candidates__": ["alice", "alice.johnson"],
        },
    )
    session = _make_session()

    await run_discovery(client, session, DiscoveryRequest())

    assert "__user_id_candidates__" not in client._chat_payload_extras


# ---------------------------------------------------------------------------
# run_discovery — transport failure is non-fatal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_discovery_handles_transport_error_gracefully():
    """run_discovery_conversation() already swallows per-turn transport errors
    internally and returns an empty profile rather than raising — verify that
    run_discovery() surfaces that as an empty, source='none' outcome with no
    crash, exercising the same path a real connection failure would take."""
    client = MagicMock()
    client.send = AsyncMock(side_effect=RuntimeError("connection refused"))
    session = _make_session()

    outcome = await run_discovery(client, session, DiscoveryRequest())

    assert outcome.profile.is_empty
    assert outcome.profile.source == "none"


@pytest.mark.asyncio
async def test_run_discovery_records_note_when_conversation_itself_raises(monkeypatch):
    """Defensive path: if run_discovery_conversation raises (rather than its usual
    graceful-empty-profile behavior), run_discovery() must still not crash and
    should record a diagnostic note."""
    import nuguard.common.discovery as discovery_module

    async def _boom(*args, **kwargs):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(discovery_module, "run_discovery_conversation", _boom)

    client = _make_client(responses=[])
    session = _make_session()

    outcome = await run_discovery(client, session, DiscoveryRequest())

    assert outcome.profile.is_empty
    assert outcome.profile.source == "none"
    assert any("failed" in n.lower() for n in outcome.notes)


# ---------------------------------------------------------------------------
# profile_from_golden_data — config fallback shared by behavior and redteam
# ---------------------------------------------------------------------------


def test_profile_from_golden_data_extracts_id_and_name():
    golden_data = {
        "Fintech App Assistant": {
            "account_id": "ACCT-001",
            "name": "Alice Johnson",
            "email": "alice.johnson@pinnaclebank.com",
        }
    }
    profile = profile_from_golden_data(golden_data)
    assert profile is not None
    assert profile.customer_name == "Alice Johnson"
    assert profile.ids == ["ACCT-001"]
    assert profile.source == "config"


def test_profile_from_golden_data_returns_none_when_no_usable_fields():
    golden_data = {"Some Agent": {"unrelated_field": "value"}}
    assert profile_from_golden_data(golden_data) is None


def test_profile_from_golden_data_empty_dict_returns_none():
    assert profile_from_golden_data({}) is None


def test_profile_from_golden_data_skips_non_dict_entries():
    golden_data = {"Agent A": "not a dict", "Agent B": {"account_id": "ACCT-002"}}
    profile = profile_from_golden_data(golden_data)
    assert profile is not None
    assert profile.ids == ["ACCT-002"]


def test_profile_from_golden_data_prefers_earlier_key_in_priority_order():
    # _GOLDEN_ID_KEYS checks "id" before "booking_ref", regardless of the
    # entry's own key order — deterministic priority, not insertion order.
    golden_data = {"Agent": {"booking_ref": "PNR123", "id": "ACCT-999"}}
    profile = profile_from_golden_data(golden_data)
    assert profile is not None
    assert profile.ids == ["ACCT-999"]
