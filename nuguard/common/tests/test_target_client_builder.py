"""Unit tests for nuguard/common/target_client_builder.py's login-endpoint
and token-key auto-detection.

Regression coverage for a bug where `_discover_login_endpoint` read a
nonexistent `NodeMetadata.response_body_schema` attribute (dead code — always
`None`) instead of the real `response_schema` field, meaning token-key
auto-detection could never work regardless of what any adapter extracted.
"""

from __future__ import annotations

import uuid
from typing import Any

from nuguard.common.auth import AuthConfig, LoginFlowConfig
from nuguard.common.target_client_builder import (
    _discover_login_endpoint,
    resolve_auth_config_with_sbom_fallback,
)
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType


def _login_node(**metadata_kwargs: Any) -> Node:
    defaults: dict[str, Any] = {
        "endpoint": "/api/v1/auth/login",
        "method": "POST",
        "request_body_schema": {"email": "string", "password": "string"},
    }
    defaults.update(metadata_kwargs)
    return Node(
        id=uuid.uuid4(),
        component_type=ComponentType.API_ENDPOINT,
        name="login",
        confidence=0.85,
        metadata=NodeMetadata(**defaults),
    )


class TestDiscoverLoginEndpointTokenKey:
    def test_reads_response_schema_not_dead_response_body_schema_attr(self) -> None:
        node = _login_node(response_schema={"accessToken": "string"})
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        result = _discover_login_endpoint(doc)
        assert result is not None
        _, _, _, token_key = result
        assert token_key == "accessToken"

    def test_resolves_dotted_nested_token_key(self) -> None:
        node = _login_node(
            response_schema={
                "tokens.accessToken": "string",
                "tokens.refreshToken": "string",
            }
        )
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        result = _discover_login_endpoint(doc)
        assert result is not None
        assert result[3] == "tokens.accessToken"

    def test_prefers_shallow_flat_key_over_deeper_nested(self) -> None:
        node = _login_node(
            response_schema={"token": "string", "wrapper.token": "string"}
        )
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        result = _discover_login_endpoint(doc)
        assert result is not None
        assert result[3] == "token"

    def test_prefers_precomputed_login_token_response_key_field(self) -> None:
        node = _login_node(
            login_token_response_key="tokens.accessToken",
            response_schema={"accessToken": "string"},
        )
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        result = _discover_login_endpoint(doc)
        assert result is not None
        assert result[3] == "tokens.accessToken"

    def test_no_response_schema_leaves_token_key_none(self) -> None:
        node = _login_node()
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        result = _discover_login_endpoint(doc)
        assert result is not None
        assert result[3] is None


class TestResolveAuthConfigWithSbomFallbackEndToEnd:
    def test_basic_auth_upgraded_with_dotted_token_key(self) -> None:
        node = _login_node(
            response_schema={
                "tokens.accessToken": "string",
                "tokens.refreshToken": "string",
            }
        )
        doc = AiSbomDocument(target="t", nodes=[node], edges=[])
        basic = AuthConfig(type="basic", username="alice", password="s3cret")
        upgraded, note = resolve_auth_config_with_sbom_fallback(basic, doc)
        assert upgraded.type == "login_flow"
        assert note is not None
        assert isinstance(upgraded.login_flow, LoginFlowConfig)
        assert upgraded.login_flow.token_response_key == "tokens.accessToken"
