"""Unit tests for nuguard.common.browser_login.public_api — the JSON-safe
Pydantic wrapper around BrowserLoginSession."""
from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.common.browser_login.config import BrowserDiscoveryConfig
from nuguard.common.browser_login.public_api import (
    BrowserDiscoveryRequest,
    BrowserDiscoveryResult,
    discover_browser,
)
from nuguard.common.browser_login.session import BrowserLoginResult
from nuguard.common.errors import BrowserLoginError


class _FakeSession:
    last_init_kwargs: dict | None = None

    def __init__(self, target_url, auth_config, browser_cfg, *, headless, timeout_s):
        _FakeSession.last_init_kwargs = {
            "target_url": target_url,
            "auth_config": auth_config,
            "browser_cfg": browser_cfg,
            "headless": headless,
            "timeout_s": timeout_s,
        }
        self.exported_to: Path | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def run(self, *, chat_message, sniff_chat):
        return BrowserLoginResult(
            cookies_written_to=Path(),
            identity_url="http://target/api/identity",
            identity_payload={"user_id": "u1"},
            sniffed_endpoint="http://target/api/chat",
            sniffed_chat_request={"message": chat_message, "consumer_id": "u1"},
            candidate_extra_fields={"consumer_id": "u1"},
            ambiguous_fields={},
            warnings=["some warning"] if not sniff_chat else [],
        )

    async def export_cookies(self, path: Path) -> Path:
        self.exported_to = path
        return path


class _FailingSession(_FakeSession):
    async def run(self, *, chat_message, sniff_chat):
        raise BrowserLoginError("boom", step="navigate", url="http://target")


@pytest.mark.asyncio
async def test_discover_browser_returns_json_safe_result(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nuguard.common.browser_login.public_api.BrowserLoginSession", _FakeSession)

    cookie_file = tmp_path / "cookies.txt"
    request = BrowserDiscoveryRequest(
        target_url="http://target",
        auth_type="basic",
        auth_username="alice",
        auth_password="super-secret",
        browser_discovery=BrowserDiscoveryConfig(),
    )

    result = await discover_browser(request, cookie_file=cookie_file)

    assert isinstance(result, BrowserDiscoveryResult)
    assert result.cookies_written_to == str(cookie_file)
    assert isinstance(result.cookies_written_to, str)
    assert result.identity_payload == {"user_id": "u1"}
    assert result.candidate_extra_fields == {"consumer_id": "u1"}

    init_kwargs = _FakeSession.last_init_kwargs
    assert init_kwargs["target_url"] == "http://target"
    assert init_kwargs["auth_config"].username == "alice"
    assert init_kwargs["auth_config"].password == "super-secret"


@pytest.mark.asyncio
async def test_discover_browser_propagates_browser_login_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nuguard.common.browser_login.public_api.BrowserLoginSession", _FailingSession)

    request = BrowserDiscoveryRequest(target_url="http://target")

    with pytest.raises(BrowserLoginError):
        await discover_browser(request, cookie_file=tmp_path / "cookies.txt")
