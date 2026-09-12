"""Tests for AuthSession.refresh_if_needed() honestly reporting success/failure.

A refresh that silently fails but is reported as True causes callers to retry
with the same already-rejected token, manufacturing a fake consecutive-failure
streak that looks identical to a target outage. See the corresponding fix in
nuguard/redteam/executor/executor.py and guided_executor.py.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from nuguard.common.auth import AuthConfig, AuthSession, LoginFlowConfig

TARGET = "http://target.test"
LOGIN_URL = f"{TARGET}/login"


def _login_flow_config(refresh_on_401: bool = True) -> AuthConfig:
    return AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(
            endpoint="/login",
            payload={"username": "u", "password": "p"},
            token_response_key="access_token",
            refresh_on_401=refresh_on_401,
        ),
    )


@pytest.mark.parametrize(
    "config",
    [
        AuthConfig(type="bearer", header="Authorization: Bearer t"),
        AuthConfig(type="api_key", header="X-API-Key: k"),
        AuthConfig(type="basic", username="u", password="p"),
        AuthConfig(type="none"),
    ],
    ids=["bearer", "api_key", "basic", "none"],
)
async def test_refresh_returns_false_for_non_login_flow_types(config: AuthConfig) -> None:
    session = AuthSession(config, base_url=TARGET)
    assert await session.refresh_if_needed() is False


async def test_refresh_returns_false_when_refresh_on_401_disabled() -> None:
    session = AuthSession(_login_flow_config(refresh_on_401=False), base_url=TARGET)
    assert await session.refresh_if_needed() is False


@respx.mock
async def test_refresh_returns_true_on_successful_relogin() -> None:
    respx.post(LOGIN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "fresh-token"})
    )
    session = AuthSession(_login_flow_config(), base_url=TARGET)

    assert await session.refresh_if_needed() is True
    assert session.headers().get("Authorization") == "Bearer fresh-token"


@respx.mock
async def test_refresh_returns_false_on_401_from_login_endpoint() -> None:
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(401, text="bad creds"))
    session = AuthSession(_login_flow_config(), base_url=TARGET)

    assert await session.refresh_if_needed() is False


@respx.mock
async def test_refresh_returns_false_on_500_from_login_endpoint() -> None:
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(500))
    session = AuthSession(_login_flow_config(), base_url=TARGET)

    assert await session.refresh_if_needed() is False


@respx.mock
async def test_refresh_returns_false_on_non_json_login_response() -> None:
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, text="not json"))
    session = AuthSession(_login_flow_config(), base_url=TARGET)

    assert await session.refresh_if_needed() is False


@respx.mock
async def test_refresh_returns_false_when_token_key_missing() -> None:
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"unrelated": "field"}))
    session = AuthSession(_login_flow_config(), base_url=TARGET)

    assert await session.refresh_if_needed() is False


@respx.mock
async def test_refresh_returns_false_on_login_timeout() -> None:
    respx.post(LOGIN_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    session = AuthSession(_login_flow_config(), base_url=TARGET)

    assert await session.refresh_if_needed() is False


@respx.mock
async def test_failed_refresh_does_not_replace_stale_token() -> None:
    """A failed refresh must not clobber headers() with garbage — callers
    check the boolean return value, not headers(), to decide whether to
    retry, so this just confirms no exception/crash on repeated failure."""
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(401))
    session = AuthSession(_login_flow_config(), base_url=TARGET)

    first = await session.refresh_if_needed()
    second = await session.refresh_if_needed()

    assert first is False
    assert second is False
