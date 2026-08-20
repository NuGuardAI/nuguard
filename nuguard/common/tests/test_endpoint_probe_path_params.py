"""Unresolved path-parameter placeholders (`:id`, `{id}`, `<id>`) must not be
treated as safely-hittable chat endpoints — sent literally, they always 404
since no real resource ID exists to substitute.

Regression coverage for the Studyield redteam failure: NestJS's
`POST /chat/conversations/:id/messages` was the only real chat surface, but
`discover_chat_candidates_from_sbom` previously ranked it identically to a
parameter-free route, and `probe_chat_endpoints`'s SBOM-path helper didn't
skip `:id`-style params at all (only FastAPI's `{id}` was excluded).
"""

from __future__ import annotations

import uuid

from nuguard.common.endpoint_probe import (
    _sbom_post_paths,
    discover_chat_candidates_from_sbom,
)
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

_NS = uuid.NAMESPACE_URL


def _endpoint_node(path: str, chat_key: str = "message", confidence: float = 0.9) -> Node:
    return Node(
        id=uuid.uuid5(_NS, f"API_ENDPOINT/{path}"),
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=confidence,
        metadata=NodeMetadata(
            endpoint=path,
            method="POST",
            chat_payload_key=chat_key,
            chat_payload_list=False,
        ),
    )


class TestSbomPostPathsSkipsPathParams:
    def test_colon_style_param_skipped(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[_endpoint_node("/chat/conversations/:id/messages")],
        )
        assert _sbom_post_paths(sbom) == []

    def test_brace_style_param_still_skipped(self) -> None:
        sbom = AiSbomDocument(target="./app", nodes=[_endpoint_node("/user/{id}/chat")])
        assert _sbom_post_paths(sbom) == []

    def test_parameter_free_path_included(self) -> None:
        sbom = AiSbomDocument(target="./app", nodes=[_endpoint_node("/chat")])
        assert "/chat" in _sbom_post_paths(sbom)


class TestDiscoverChatCandidatesPenalizesPathParams:
    def test_parameter_free_candidate_outranks_path_param_candidate(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[
                _endpoint_node("/chat/conversations/:id/messages"),
                _endpoint_node("/chat"),
            ],
        )
        candidates = discover_chat_candidates_from_sbom(sbom)
        paths = [c[0] for c in candidates]
        assert paths[0] == "/chat"

    def test_path_param_candidate_still_returned_when_it_is_the_only_option(self) -> None:
        sbom = AiSbomDocument(
            target="./app",
            nodes=[_endpoint_node("/chat/conversations/:id/messages")],
        )
        candidates = discover_chat_candidates_from_sbom(sbom)
        assert candidates
        assert candidates[0][0] == "/chat/conversations/:id/messages"
