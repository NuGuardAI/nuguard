from __future__ import annotations

from nuguard.common.auth import AuthConfig
from nuguard.common.auth_runtime import resolve_auth_runtime


def test_resolve_auth_runtime_prefers_authorization_override() -> None:
    auth = AuthConfig(type="none")
    resolved = resolve_auth_runtime(
        auth_config=auth,
        headers_override={
            "X-Request-Id": "abc-123",
            "Authorization": "Bearer token-1",
        },
    )

    assert resolved.auth_config.type == "bearer"
    assert resolved.initial_headers["Authorization"] == "Bearer token-1"


def test_resolve_auth_runtime_uses_first_override_when_no_authorization() -> None:
    resolved = resolve_auth_runtime(
        auth_config=AuthConfig(type="none"),
        headers_override={"X-API-Key": "secret-key"},
    )

    assert resolved.auth_config.type == "api_key"
    assert resolved.initial_headers == {"X-API-Key": "secret-key"}


def test_resolve_auth_runtime_falls_back_to_auth_config() -> None:
    auth = AuthConfig(type="bearer", header="Authorization: Bearer from-config")
    resolved = resolve_auth_runtime(auth_config=auth)

    assert resolved.auth_config == auth
    assert resolved.initial_headers == {"Authorization": "Bearer from-config"}


def test_resolve_auth_runtime_defaults_to_none() -> None:
    resolved = resolve_auth_runtime()
    assert resolved.auth_config.type == "none"
    assert resolved.initial_headers == {}