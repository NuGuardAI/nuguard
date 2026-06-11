"""Tests for nuguard.redteam.target.discovery (pre-scan discovery)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nuguard.redteam.target.discovery import (
    DiscoveredProfile,
    _domain_messages,
    _domain_task_messages,
    _is_refusal,
    _AIRLINE_MESSAGES,
    _AIRLINE_TASK_MESSAGES,
    _BANKING_MESSAGES,
    _BANKING_TASK_MESSAGES,
    _HEALTHCARE_MESSAGES,
    _HEALTHCARE_TASK_MESSAGES,
    _GENERIC_MESSAGES,
    _GENERIC_TASK_MESSAGES,
    _CAPABILITY_PROBE,
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
    """Discovery stops after the first turn that yields both a name AND IDs."""
    responses = [
        # Greeting pattern → name extracted; labelled PNR → ID extracted
        "Hello John Doe, your booking reference is PNR12345.",
        "This second response should never be sent.",
    ]
    client = _make_client(responses)
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="travel", max_turns=3)

    # Only 1 turn should have been sent — we have both name and ID
    assert profile.turns_sent == 1
    assert client.send.call_count == 1
    assert profile.customer_name == "John Doe"
    assert "PNR12345" in profile.ids


@pytest.mark.asyncio
async def test_discovery_continues_for_name_when_only_ids_found():
    """When turn 1 yields IDs but no name, discovery continues to turn 2."""
    responses = [
        "Your bookings: PNR12345 (JFK→LAX), BB5678 (LAX→ORD).",  # IDs but no name
        "Hello John Doe, here are your bookings as confirmed.",   # name extracted
        "This third response should never be sent.",
    ]
    client = _make_client(responses)
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="travel", max_turns=3)

    assert profile.turns_sent == 2
    assert profile.customer_name == "John Doe"
    assert "PNR12345" in profile.ids


@pytest.mark.asyncio
async def test_discovery_respects_max_turns():
    """Discovery never sends more than max_turns data turns + 1 capability probe."""
    responses = ["No useful data here."] * 5
    client = _make_client(responses)
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="", max_turns=2)

    # max_turns=2 data turns + at most 1 capability probe = 3 total
    assert client.send.call_count <= 3
    assert profile.turns_sent <= 3


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


# ---------------------------------------------------------------------------
# _is_refusal — refusal detection helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("I'm sorry, I can't retrieve that information.", True),
    ("I apologize, but I cannot access your booking history.", True),
    ("Sorry, I can only answer questions related to airline travel.", True),
    ("I'm unable to list your account data.", True),
    ("I do not have access to your booking records.", True),
    ("Cannot retrieve the requested information.", True),
    ("Here are your upcoming bookings: AA1234, BB5678.", False),
    ("Hello Alice, your booking reference is K7Q4MN.", False),
    ("Your account number is 9876543210.", False),
    ("", False),
])
def test_is_refusal(text: str, expected: bool):
    assert _is_refusal(text) is expected


# ---------------------------------------------------------------------------
# _domain_task_messages — task-framed opener selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("use_case,expected", [
    ("airline booking assistant", _AIRLINE_TASK_MESSAGES),
    ("flight reservation manager", _AIRLINE_TASK_MESSAGES),
    ("bank account helper", _BANKING_TASK_MESSAGES),
    ("financial transaction tracker", _BANKING_TASK_MESSAGES),
    ("patient appointment manager", _HEALTHCARE_TASK_MESSAGES),
    ("hospital clinic navigator", _HEALTHCARE_TASK_MESSAGES),
    ("general assistant", _GENERIC_TASK_MESSAGES),
    ("", _GENERIC_TASK_MESSAGES),
])
def test_domain_task_messages_selection(use_case: str, expected: list[str]):
    assert _domain_task_messages(use_case) is expected


# ---------------------------------------------------------------------------
# Adaptive tactic switching: refusal → task-framing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discovery_switches_to_task_on_refusal():
    """After a refusal on turn 1, subsequent turns use task-framed messages."""
    refusal = "I'm sorry, I cannot retrieve your booking history."
    task_response = "Your booking reference is ZR7K91. Passenger: Jane Doe."

    client = _make_client([refusal, task_response])
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="airline", max_turns=3)

    assert profile.turns_sent >= 2
    # Task-framed turn should have extracted the ID
    assert "ZR7K91" in profile.ids
    # The second turn message should be a task-framed opener (not a primary one)
    second_call_msg = client.send.call_args_list[1][0][0]
    assert second_call_msg in _AIRLINE_TASK_MESSAGES


@pytest.mark.asyncio
async def test_discovery_does_not_switch_when_turn1_succeeds():
    """No tactic switch when turn 1 returns extractable data."""
    success = "Hello John Doe, your booking PNR12345 is confirmed."

    client = _make_client([success])
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="travel", max_turns=3)

    assert profile.turns_sent == 1  # stopped early
    # First call should be the primary opener
    first_call_msg = client.send.call_args_list[0][0][0]
    assert first_call_msg in _AIRLINE_MESSAGES


@pytest.mark.asyncio
async def test_discovery_switches_only_once():
    """The tactic switch is idempotent — second refusal does not re-switch."""
    refusal1 = "I cannot retrieve your account data."
    refusal2 = "Sorry, I can only answer airline questions."
    data = "Your booking reference K7Q4MN is on file for Alice Johnson."

    client = _make_client([refusal1, refusal2, data])
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="airline", max_turns=3)

    assert "K7Q4MN" in profile.ids
    # Turn 2 should be task-framed (switched after turn 1 refusal)
    second_msg = client.send.call_args_list[1][0][0]
    assert second_msg in _AIRLINE_TASK_MESSAGES
    # Turn 3 should also be task-framed (no re-switch back to primary)
    third_msg = client.send.call_args_list[2][0][0]
    assert third_msg in _AIRLINE_TASK_MESSAGES


# ---------------------------------------------------------------------------
# Capability probe — sent when all data turns fail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_capability_probe_sent_when_all_turns_empty():
    """When all turns return no extractable data, a capability probe is sent."""
    refusals = [
        "I'm sorry, I cannot retrieve that.",
        "Sorry, I can only answer airline questions.",
        "I cannot list your booking details.",
    ]
    cap_hint = "I can help you with flight check-in, seat selection, and cancellations."

    # 3 refusal turns + 1 capability probe turn
    client = _make_client(refusals + [cap_hint])
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="airline", max_turns=3)

    assert profile.is_empty
    assert profile.capability_hint == cap_hint
    assert client.send.call_count == 4  # 3 turns + 1 probe
    # The last call should be the capability probe
    last_msg = client.send.call_args_list[3][0][0]
    assert last_msg == _CAPABILITY_PROBE


@pytest.mark.asyncio
async def test_capability_probe_not_sent_when_data_extracted():
    """The capability probe is NOT sent when data is successfully extracted."""
    data_response = "Hello Alice, your booking K7Q4MN is on flight BA205."
    client = _make_client([data_response])
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="airline", max_turns=3)

    assert not profile.is_empty
    assert profile.capability_hint == ""
    assert client.send.call_count == 1  # stopped early, no probe


@pytest.mark.asyncio
async def test_capability_hint_not_used_for_id_extraction():
    """capability_hint text is stored but does not pollute the ids list."""
    refusal = "I'm sorry, I cannot retrieve that."
    # The hint contains strings that look like IDs — must NOT be extracted
    cap_hint = "I can look up bookings like PNR12345 or reference codes like AB1234."

    client = _make_client([refusal, cap_hint])
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="airline", max_turns=1)

    assert profile.is_empty
    assert profile.capability_hint == cap_hint
    # IDs mentioned in capability hint must not end up in profile.ids
    assert "PNR12345" not in profile.ids
    assert "AB1234" not in profile.ids


@pytest.mark.asyncio
async def test_capability_probe_gracefully_handles_transport_error():
    """A transport error on the capability probe does not raise; profile stays empty."""
    refusal = "I'm sorry, I can't retrieve that."
    client = MagicMock()
    # First call returns refusal; second call (probe) raises
    client.send = AsyncMock(side_effect=[(refusal, {}), RuntimeError("connection reset")])
    session = _make_session()

    profile = await run_discovery_conversation(client, session, use_case="airline", max_turns=1)

    assert profile.is_empty
    assert profile.capability_hint == ""
