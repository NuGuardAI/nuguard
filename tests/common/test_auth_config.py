"""Unit tests for nuguard.common.auth.AuthConfig."""
from __future__ import annotations

import base64

import pytest

from nuguard.common.auth import AuthConfig


class TestToHeaders:
    def test_bearer_to_headers(self) -> None:
        cfg = AuthConfig(type="bearer", header="Authorization: Bearer tok")
        assert cfg.to_headers() == {"Authorization": "Bearer tok"}

    def test_api_key_to_headers(self) -> None:
        cfg = AuthConfig(type="api_key", header="X-API-Key: secret123")
        assert cfg.to_headers() == {"X-API-Key": "secret123"}

    def test_basic_to_headers(self) -> None:
        cfg = AuthConfig(type="basic", username="alice", password="p@ss")
        expected = base64.b64encode(b"alice:p@ss").decode()
        assert cfg.to_headers() == {"Authorization": f"Basic {expected}"}

    def test_none_to_headers(self) -> None:
        cfg = AuthConfig(type="none")
        assert cfg.to_headers() == {}


class TestFromHeaderString:
    def test_bearer(self) -> None:
        cfg = AuthConfig.from_header_string("Authorization: Bearer mytoken")
        assert cfg.type == "bearer"
        assert cfg.header == "Authorization: Bearer mytoken"

    def test_api_key(self) -> None:
        cfg = AuthConfig.from_header_string("X-API-Key: secret")
        assert cfg.type == "api_key"
        assert cfg.header == "X-API-Key: secret"

    def test_empty_string_returns_none(self) -> None:
        cfg = AuthConfig.from_header_string("")
        assert cfg.type == "none"

    def test_whitespace_only_returns_none(self) -> None:
        cfg = AuthConfig.from_header_string("   ")
        assert cfg.type == "none"


class TestFromTenantToken:
    def test_bare_token_becomes_bearer(self) -> None:
        cfg = AuthConfig.from_tenant_token("tok_abc123")
        assert cfg.type == "bearer"
        assert cfg.header == "Authorization: Bearer tok_abc123"

    def test_header_string_preserved(self) -> None:
        cfg = AuthConfig.from_tenant_token("X-API-Key: sk-tenant-001")
        assert cfg.type == "api_key"
        assert cfg.header == "X-API-Key: sk-tenant-001"

    def test_empty_token_returns_none(self) -> None:
        cfg = AuthConfig.from_tenant_token("")
        assert cfg.type == "none"


class TestValidation:
    def test_bearer_missing_header_raises(self) -> None:
        with pytest.raises(ValueError, match="auth.type=bearer requires auth.header"):
            AuthConfig(type="bearer")

    def test_api_key_missing_header_raises(self) -> None:
        with pytest.raises(ValueError, match="auth.type=api_key requires auth.header"):
            AuthConfig(type="api_key")

    def test_basic_missing_password_raises(self) -> None:
        with pytest.raises(ValueError, match="auth.type=basic requires"):
            AuthConfig(type="basic", username="alice")

    def test_basic_missing_username_raises(self) -> None:
        with pytest.raises(ValueError, match="auth.type=basic requires"):
            AuthConfig(type="basic", password="pass")
