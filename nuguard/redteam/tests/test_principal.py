"""Credential fingerprint stability, identity isolation and redaction."""

from __future__ import annotations

import subprocess
import sys

import pytest

from nuguard.common.auth import AuthConfig
from nuguard.redteam.campaign.transport import Principal


def test_fingerprint_is_stable_across_processes() -> None:
    """Pin the KDF parameters so checkpoint identities do not silently drift."""
    expected = "9b3d1bee5459eaeeb8c679cce8843c3656e863c2ff95aca35b72e011fa2c2bb5"
    principal = Principal.from_headers("primary", {"Authorization": "Bearer test-token"})
    assert principal.auth_scope == expected
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nuguard.redteam.campaign.transport import Principal; "
            "print(Principal.from_headers('primary', "
            "{'Authorization': 'Bearer test-token'}).auth_scope)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == expected


def test_fingerprint_normalizes_header_order_and_case() -> None:
    first = Principal.from_headers("primary", {"Authorization": "Bearer token", "X-Key": "key"})
    second = Principal.from_headers("other", {"x-key": "key", "authorization": "Bearer token"})
    assert first.auth_scope == second.auth_scope


def test_fingerprint_distinguishes_header_boundaries() -> None:
    first = Principal.from_headers("primary", {"X-Key": "key\ny-key=other"})
    second = Principal.from_headers("primary", {"X-Key": "key", "Y-Key": "other"})
    assert first.auth_scope != second.auth_scope


@pytest.mark.parametrize("auth_type", ["basic", "bearer", "api_key"])
def test_credentials_are_redacted_and_rotation_changes_scope(
    auth_type: str, caplog: pytest.LogCaptureFixture
) -> None:
    def headers(secret: str) -> dict[str, str]:
        if auth_type == "basic":
            return AuthConfig(type="basic", username="fixture-user", password=secret).to_headers()
        name = "Authorization" if auth_type == "bearer" else "X-API-Key"
        value = f"Bearer {secret}" if auth_type == "bearer" else secret
        return AuthConfig.from_header_string(f"{name}: {value}").to_headers()

    original_headers = headers("fixture-secret")
    principal = Principal.from_headers("primary", original_headers)
    rotated = Principal.from_headers("primary", headers("rotated-secret"))
    assert principal.auth_scope != rotated.auth_scope
    assert len(principal.auth_scope) == 64
    assert principal.headers == original_headers
    assert principal.headers is not original_headers
    visible = repr(principal) + principal.auth_scope + caplog.text
    assert "fixture-secret" not in visible
    assert "fixture-user" not in visible
    assert all(value not in visible for value in original_headers.values())


def test_empty_headers_preserve_anonymous_identity() -> None:
    assert Principal.from_headers("primary", {}).auth_scope == Principal.anonymous().auth_scope
