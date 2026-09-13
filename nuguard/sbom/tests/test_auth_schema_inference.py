"""Unit tests for nuguard/sbom/core/auth_schema_inference.py — the LLM
fallback pass for login-endpoint token-key resolution. Fallback-only: must
never fire (or overwrite) when static DTO extraction already resolved the
key, must respect its budget, and must stamp provenance when it succeeds.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nuguard.sbom.core.auth_schema_inference import infer_login_token_key
from nuguard.sbom.core.gap_fill.budget import GapFillBudget
from nuguard.sbom.models import AiSbomDocument, Evidence, Node, NodeMetadata, SourceLocation
from nuguard.sbom.types import ComponentType


def _login_node(**metadata_kwargs: Any) -> Node:
    defaults: dict[str, Any] = {
        "endpoint": "/api/v1/auth/login",
        "method": "POST",
        "request_body_schema": {"email": "string", "password": "string"},
    }
    defaults.update(metadata_kwargs)
    return Node(
        name="login",
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.85,
        metadata=NodeMetadata(**defaults),
        evidence=[
            Evidence(
                kind="regex",
                confidence=0.85,
                detail="@Post('login')",
                location=SourceLocation(path="auth.controller.ts", line=10),
            )
        ],
    )


async def _unexpected_llm_call(system: str, user: str) -> tuple[str, int]:
    raise AssertionError("LLM should not be called")


def _mock_llm_call(response_json: dict):
    async def _call(system: str, user: str) -> tuple[str, int]:
        return json.dumps(response_json), 100

    return _call


@pytest.mark.asyncio
class TestInferLoginTokenKey:
    async def test_skipped_when_static_extraction_already_resolved(self) -> None:
        node = _login_node(response_schema={"accessToken": "string"})
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        stats = await infer_login_token_key(doc, {}, _unexpected_llm_call)
        assert stats.attempted == 0
        assert node.metadata.login_token_response_key is None

    async def test_fires_and_stamps_provenance_when_static_extraction_failed(self) -> None:
        node = _login_node()
        file_contents = {
            "auth.controller.ts": "\n" * 9 + "async login(dto) { return this.auth.login(dto); }\n",
        }
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        llm_call = _mock_llm_call(
            {
                "login_token_response_key": "tokens.accessToken",
                "confidence": 0.9,
                "reasoning": "response nests token under tokens",
            }
        )
        stats = await infer_login_token_key(doc, file_contents, llm_call)
        assert stats.attempted == 1
        assert stats.resolved == 1
        assert node.metadata.login_token_response_key == "tokens.accessToken"
        assert node.metadata.extras["login_token_response_key_source"] == "llm_frontend_inferred"
        assert node.metadata.extras["login_token_response_key_confidence"] == 0.9

    async def test_no_login_endpoint_is_a_noop(self) -> None:
        node = Node(
            name="widgets",
            component_type=ComponentType.API_ENDPOINT,
            confidence=0.9,
            metadata=NodeMetadata(endpoint="/api/v1/widgets", method="GET"),
        )
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        stats = await infer_login_token_key(doc, {}, _unexpected_llm_call)
        assert stats.attempted == 0

    async def test_respects_exhausted_budget(self) -> None:
        node = _login_node()
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        exhausted_budget = GapFillBudget(max_calls=0, max_cost_usd=0.0)
        stats = await infer_login_token_key(
            doc, {}, _unexpected_llm_call, budget=exhausted_budget
        )
        assert stats.attempted == 0
        assert node.metadata.login_token_response_key is None

    async def test_disabled_is_a_noop(self) -> None:
        node = _login_node()
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        stats = await infer_login_token_key(doc, {}, _unexpected_llm_call, enabled=False)
        assert stats.attempted == 0

    async def test_unparseable_llm_response_leaves_field_unset(self) -> None:
        node = _login_node()
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])

        async def _garbage_call(system: str, user: str) -> tuple[str, int]:
            return "not json at all", 10

        stats = await infer_login_token_key(doc, {}, _garbage_call)
        assert stats.attempted == 1
        assert stats.resolved == 0
        assert node.metadata.login_token_response_key is None
