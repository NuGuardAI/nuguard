"""Tests for nuguard.redteam.target.discovery (pre-scan discovery)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nuguard.redteam.target.discovery import (
    DiscoveredProfile,
    _domain_messages,
    _AIRLINE_MESSAGES,
    _BANKING_MESSAGES,
    _HEALTHCARE_MESSAGES,
    _GENERIC_MESSAGES,
    run_discovery_conversation,
)


# ---------------------------------------------------------------------------
# DiscoveredProfile dataclass
# ---------------------------------------------------------------------------

def test_profile_defaults():
    """DiscoveredProfile initialises with empty / falsy values."""
    p = DiscoveredProfile()
    assert p.customer_name == ""
    assert p.ids == []
    assert p.entity_map == {}
    assert p.raw_response == ""
    assert p.turns_sent == 0
    assert p.is_empty is True


def test_profile_not_empty_when_name_set():
    p = DiscoveredProfile(customer_name="Alice Johnson")
    assert p.is_empty is False


def test_profile_not_empty_when_ids_set():
    p = DiscoveredProfile(ids=["K7Q4MN"])
    assert p.is_empty is False


# ---------------------------------------------------------------------------
# _domain_messages — opener selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("use_case,expected", [
    ("airline booking assistant", _AIRLINE_MESSAGES),
    ("Manage flight reservations", _AIRLINE_MESSAGES),
    ("aviation support", _AIRLINE_MESSAGES),
    ("bank account helper", _BANKING_MESSAGES),
    ("financial transaction tracker", _BANKING_MESSAGES),
    ("patient appointment manager", _HEALTHCARE_MESSAGES),
    ("hospital clinic navigator", _HEALTHCARE_MESSAGES),
    ("general assistant", _GENERIC_MESSAGES),
    ("", _GENERIC_MESSAGES),
])
def test_domain_messages_selection(use_case: str, expected: list[str]):
    assert _domain_messages(use_case) is expected


# ---------------------------------------------------------------------------
# run_discovery_conversation — async integration tests (mocked client)
# ---------------------------------------------------------------------------

def _make_client(responses: list[str]) -> MagicMock:
    """Return a mock TargetAppClient whose send() returns successive responses."""
    client = MagicMock()
    client.send = AsyncMock(side_effect=[(r, {}) for r in responses])
    # Support async context manager if needed by tests
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _make_session() -> MagicMock:
    session = MagicMock()
    session.headers.return_value = {}
    return session


@pytest.mark.asyncio
async def test_discovery_extracts_name_and_id():
    """run_discovery_conversation extracts a name + ID from the first response."""
    response = (
        "Passenger: Alice Johnson — booking reference K7Q4MN for flight BA205 "
        "on 2026-08-15."
    )
    client = _make_client([response])
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="airline booking")

    assert "K7Q4MN" in profile.ids
    assert profile.customer_name == "Alice Johnson"
    assert profile.turns_sent == 1


@pytest.mark.asyncio
async def test_discovery_stops_early_after_first_hit():
    """Discovery stops after the first turn that yields useful data."""
    responses = [
        "Your booking reference is PNR12345 for John Doe.",
        "This second response should never be sent.",
    ]
    client = _make_client(responses)
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="travel", max_turns=3)

    # Only 1 turn should have been sent
    assert profile.turns_sent == 1
    assert client.send.call_count == 1


@pytest.mark.asyncio
async def test_discovery_respects_max_turns():
    """Discovery never sends more turns than max_turns."""
    responses = ["No useful data here."] * 5
    client = _make_client(responses)
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="", max_turns=2)

    assert client.send.call_count <= 2
    assert profile.turns_sent <= 2


@pytest.mark.asyncio
async def test_discovery_empty_profile_on_no_data():
    """Returns empty profile (is_empty=True) when nothing can be extracted."""
    client = _make_client(["I'm sorry, I can't help with that.", "Access denied."])
    session = _make_session()

    profile = await run_discovery_conversation(client, session, max_turns=2)

    assert profile.is_empty is True


@pytest.mark.asyncio
async def test_discovery_handles_transport_error_gracefully():
    """A transport error on the first turn results in an empty profile, not an exception."""
    client = MagicMock()
    client.send = AsyncMock(side_effect=RuntimeError("connection refused"))

    session = _make_session()
    profile = await run_discovery_conversation(client, session, max_turns=2)

    assert profile.is_empty is True
    assert profile.turns_sent == 0


@pytest.mark.asyncio
async def test_discovery_extracts_entity_map():
    """Entity map is populated from labelled key:value pairs in response."""
    response = (
        "Your profile: flight: BA205, departure: 2026-08-15, status: confirmed. "
        "Booking ref BA205-XX for Bob Smith."
    )
    client = _make_client([response])
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="airline")

    assert "flight" in profile.entity_map or "departure" in profile.entity_map or "status" in profile.entity_map


@pytest.mark.asyncio
async def test_discovery_raw_response_concatenated():
    """raw_response concatenates all turn responses separated by '---'."""
    responses = [
        "Turn 1: no IDs yet.",
        "Turn 2: booking reference ZR7K91 for user Jane.",
    ]
    client = _make_client(responses)
    session = _make_session()

    profile = await run_discovery_conversation(client, session, max_turns=3)

    assert "Turn 1" in profile.raw_response or "Turn 2" in profile.raw_response


# ---------------------------------------------------------------------------
# Cache pre-seeding from DiscoveredProfile
# ---------------------------------------------------------------------------

def test_executor_pre_seeds_cache_from_profile():
    """AttackExecutor pre-seeds golden-data cache when given a DiscoveredProfile."""
    import uuid
    from nuguard.redteam.executor.executor import AttackExecutor
    from nuguard.redteam.target.discovery import DiscoveredProfile
    from nuguard.sbom.models import AiSbomDocument, Node, NodeType

    profile = DiscoveredProfile(
        customer_name="Alice Johnson",
        ids=["K7Q4MN", "BA205"],
        raw_response="Dear Alice Johnson, your booking K7Q4MN on BA205.",
    )

    _NS = uuid.UUID("00000000-0000-0000-0000-000000000001")
    agent_id = uuid.uuid5(_NS, "booking-agent")
    agent_node = Node(
        id=agent_id,
        name="booking-agent",
        component_type=NodeType.AGENT,
        confidence=0.9,
    )
    sbom = AiSbomDocument(
        target="test-target",
        nodes=[agent_node],
        edges=[],
    )

    executor = AttackExecutor(
        client=MagicMock(),
        canary=MagicMock(),
        sbom=sbom,
        pre_scan_profile=profile,
    )

    # Cache should be pre-seeded for the agent node
    node_key = str(agent_id)
    assert node_key in executor._golden_data_cache
    cached = executor._golden_data_cache[node_key]
    assert "K7Q4MN" in cached[1]  # ids list
    assert cached[2] == "Alice Johnson"  # customer_name
