"""Tests for AuthSession.auth_config (issue #611 Phase 2).

nuguard.common.discovery.profile_cache_fingerprint needs a stable identity
fingerprint for the *configured* credential — never the dynamically-acquired
login_flow token, which changes on every run even for the same underlying
identity and would make the fingerprint useless for cross-run cache reuse.
This file locks in that guarantee directly against AuthSession, since
nuguard/behavior/runner.py's _discovery_auth_config() depends entirely on it.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from nuguard.common.auth import AuthConfig, AuthSession, LoginFlowConfig

TARGET = "http://target.test"
LOGIN_URL = f"{TARGET}/login"


@pytest.mark.parametrize(
    "config",
    [
        AuthConfig(type="bearer", header="Authorization: Bearer t"),
        AuthConfig(type="api_key", header="X-API-Key: k"),
        AuthConfig(type="basic", username="u", password="p"),
        AuthConfig(type="cookie_file", cookie_file="/tmp/cookies.txt"),
        AuthConfig(type="none"),
    ],
    ids=["bearer", "api_key", "basic", "cookie_file", "none"],
)
def test_auth_config_returns_the_configured_object_for_static_types(config: AuthConfig) -> None:
    session = AuthSession(config, base_url=TARGET)
    assert session.auth_config is config


def test_auth_config_returns_the_configured_object_for_login_flow() -> None:
    config = AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(endpoint="/login", payload={"username": "u", "password": "p"}),
    )
    session = AuthSession(config, base_url=TARGET)
    assert session.auth_config is config


@respx.mock
async def test_auth_config_unaffected_by_login_flow_token_acquisition() -> None:
    """The core guarantee: after initialize() acquires a live token,
    .auth_config must still return the original static config untouched —
    not something carrying the live token — so two AuthSession instances
    built from equivalent configs keep producing the same fingerprint
    regardless of what token either one happens to be holding."""
    respx.post(LOGIN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "live-token-xyz"})
    )
    config = AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(endpoint="/login", payload={"username": "u", "password": "p"}),
    )
    session = AuthSession(config, base_url=TARGET)

    await session.initialize()

    assert session.headers().get("Authorization") == "Bearer live-token-xyz"
    assert session.auth_config is config
    assert session.auth_config.login_flow is not None
    assert session.auth_config.login_flow.payload == {"username": "u", "password": "p"}


def test_auth_config_reflects_replace_config_after_fallback() -> None:
    """replace_config() (called by AuthBootstrapper when a login_flow endpoint
    proves broken and a basic-auth fallback is used instead) is a legitimate,
    deliberate identity change — .auth_config must reflect the new config
    afterward, not the stale original. This is distinct from the
    live-token guarantee above: a real strategy swap SHOULD change the
    fingerprint, since it genuinely is a different credential now."""
    original = AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(endpoint="/login", payload={"username": "u", "password": "p"}),
    )
    session = AuthSession(original, base_url=TARGET)
    fallback = AuthConfig(type="basic", username="u", password="p")

    session.replace_config(fallback)

    assert session.auth_config is fallback
    assert session.auth_config is not original
