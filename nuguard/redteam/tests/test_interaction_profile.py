from __future__ import annotations

import pytest

from nuguard.redteam.target.interaction_profile import (
    discover_interaction_profiles,
    select_interaction_profile,
)
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, ScanSummary
from nuguard.sbom.types import ComponentType


def _doc(nodes: list[Node]) -> AiSbomDocument:
    return AiSbomDocument(
        schema_version="1.3.0",
        generated_at="2026-01-01T00:00:00Z",
        generator="tests",
        target="test",
        nodes=nodes,
        edges=[],
        deps=[],
        summary=ScanSummary(),
    )


def _api_node(
    node_id: str,
    name: str,
    endpoint: str,
    method: str = "POST",
    chat_payload_key: str | None = None,
    request_body_schema: dict[str, str] | None = None,
    interaction_role_tags: list[str] | None = None,
    response_id_map: dict[str, str] | None = None,
) -> Node:
    return Node(
        id=f"00000000-0000-0000-0000-{int(node_id.split('-')[-1]):012d}",
        name=name,
        component_type=ComponentType.API_ENDPOINT,
        confidence=1.0,
        metadata=NodeMetadata(
            endpoint=endpoint,
            method=method,
            chat_payload_key=chat_payload_key,
            chat_payload_list=False,
            request_body_schema=request_body_schema or {},
            interaction_role_tags=interaction_role_tags,
            response_id_map=response_id_map,
        ),
    )


def test_discover_direct_chat_profile() -> None:
    sbom = _doc([
        _api_node("chat-1", "Chat", "/chat", chat_payload_key="message"),
    ])

    profiles = discover_interaction_profiles(sbom)

    assert len(profiles) == 1
    p = profiles[0]
    assert p.profile_id == "direct:00000000-0000-0000-0000-000000000001"
    assert p.chat_path == "/chat"
    assert p.chat_payload_key == "message"
    assert p.bootstrap_step is None


def test_discover_bootstrap_profile_from_id_field_match() -> None:
    sbom = _doc([
        _api_node("init-1", "Create Session", "/api/v1/new-chat"),
        _api_node(
            "chat-1",
            "Chat",
            "/api/v1/chat",
            chat_payload_key="message",
            request_body_schema={"session_id": "string", "message": "string"},
        ),
    ])

    profiles = discover_interaction_profiles(sbom)
    bootstrap = [p for p in profiles if p.profile_id.startswith("bootstrap:")]

    assert len(bootstrap) == 1
    p = bootstrap[0]
    assert p.chat_path == "/api/v1/chat"
    assert p.session_id_json_key == "session_id"
    assert p.session_header_name == "X-Session-Id"
    assert p.bootstrap_step is not None
    assert p.bootstrap_step.path == "/api/v1/new-chat"


def test_discover_modeled_auth_and_chat_flow() -> None:
    sbom = _doc([
        _api_node(
            "auth-1",
            "Auth Token",
            "/auth/token",
            interaction_role_tags=["auth_token"],
            response_id_map={"access_token": "access_token"},
        ),
        _api_node("chat-1", "Chat", "/chat", chat_payload_key="message"),
    ])

    profiles = discover_interaction_profiles(sbom)
    flow = [p for p in profiles if p.profile_id.startswith("flow:")]

    assert len(flow) == 1
    steps = list(flow[0].flow_steps)
    assert [s.role for s in steps] == ["auth_token", "chat"]
    assert steps[0].extractors[0].state_key == "access_token"


def test_select_profile_by_endpoint_id() -> None:
    sbom = _doc([
        _api_node("chat-1", "Primary Chat", "/chat", chat_payload_key="message"),
        _api_node("chat-2", "Alt Chat", "/assistant", chat_payload_key="input"),
    ])

    profiles = discover_interaction_profiles(sbom)
    selected = select_interaction_profile(
        profiles,
        endpoint_id="00000000-0000-0000-0000-000000000002",
    )

    assert selected is not None
    assert selected.endpoint_node_id == "00000000-0000-0000-0000-000000000002"
    assert selected.chat_path == "/assistant"


def test_select_profile_raises_on_ambiguous_candidates() -> None:
    sbom = _doc([
        _api_node("chat-1", "Primary Chat", "/chat", chat_payload_key="message"),
        _api_node("chat-2", "Alt Chat", "/assistant", chat_payload_key="input"),
    ])

    profiles = discover_interaction_profiles(sbom)

    with pytest.raises(ValueError, match="Multiple interaction profiles"):
        select_interaction_profile(profiles)
