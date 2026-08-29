"""Unit tests for nuguard.common.browser_login.session — the pure/non-Playwright
pieces (cookie serialization, payload-field diffing). Importing this module must
not require the `browser` extra (playwright/ruamel.yaml) to be installed; the
Playwright-driving methods themselves are exercised only in a live/manual run
against a real target (see tests/apps/kscope)."""
from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.common.browser_login.config import BrowserDiscoveryConfig
from nuguard.common.browser_login.session import BrowserLoginSession, write_netscape_cookies


class _StubAuthConfig:
    username = "user@example.com"
    password = "pw"


class TestWriteNetscapeCookies:
    def test_writes_seven_column_format(self, tmp_path: Path) -> None:
        cookies = [
            {
                "name": "session",
                "value": "abc123",
                "domain": "example.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "expires": 1999999999,
            }
        ]
        out = tmp_path / "cookies.txt"
        write_netscape_cookies(cookies, out)
        lines = out.read_text(encoding="utf-8").splitlines()
        data_lines = [ln for ln in lines if ln and not ln.startswith("#")]
        assert len(data_lines) == 1
        fields = data_lines[0].split("\t")
        assert fields == ["example.com", "FALSE", "/", "TRUE", "1999999999", "session", "abc123"]

    def test_http_only_cookie_gets_prefix_matching_curl_convention(self, tmp_path: Path) -> None:
        cookies = [
            {
                "name": "sess",
                "value": "v",
                "domain": "example.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expires": 0,
            }
        ]
        out = tmp_path / "cookies.txt"
        write_netscape_cookies(cookies, out)
        text = out.read_text(encoding="utf-8")
        assert "#HttpOnly_example.com\tFALSE\t/\tTRUE\t0\tsess\tv" in text

    def test_subdomain_cookie_marks_include_subdomains_true(self, tmp_path: Path) -> None:
        cookies = [
            {"name": "a", "value": "b", "domain": ".example.com", "path": "/", "secure": False, "httpOnly": False}
        ]
        out = tmp_path / "cookies.txt"
        write_netscape_cookies(cookies, out)
        line = [ln for ln in out.read_text().splitlines() if ln and not ln.startswith("#")][0]
        assert line.split("\t")[1] == "TRUE"

    def test_session_cookie_with_no_expiry_writes_zero(self, tmp_path: Path) -> None:
        cookies = [{"name": "a", "value": "b", "domain": "x.com", "path": "/"}]
        out = tmp_path / "cookies.txt"
        write_netscape_cookies(cookies, out)
        line = [ln for ln in out.read_text().splitlines() if ln and not ln.startswith("#")][0]
        assert line.split("\t")[4] == "0"

    def test_round_trips_through_existing_reader(self, tmp_path: Path) -> None:
        """The whole point of this format is compatibility with the existing
        _parse_netscape_cookies() reader in nuguard.common.auth — verify it
        actually reads back what we wrote."""
        from nuguard.common.auth import AuthConfig

        cookies = [
            {
                "name": "mosaic_session",
                "value": "eyJhbGciOiJIUzI1NiJ9.payload.sig",
                "domain": "example.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expires": 1999999999,
            }
        ]
        out = tmp_path / "cookies.txt"
        write_netscape_cookies(cookies, out)

        auth = AuthConfig(type="cookie_file", cookie_file=str(out))
        headers = auth.to_headers()
        assert headers == {"Cookie": "mosaic_session=eyJhbGciOiJIUzI1NiJ9.payload.sig"}

    def test_permissions_restricted_to_owner(self, tmp_path: Path) -> None:
        out = tmp_path / "cookies.txt"
        write_netscape_cookies([], out)
        mode = out.stat().st_mode & 0o777
        assert mode == 0o600


def _session() -> BrowserLoginSession:
    return BrowserLoginSession(
        "https://example.com", _StubAuthConfig(), BrowserDiscoveryConfig()  # type: ignore[arg-type]
    )


class TestResolveExtraFields:
    def test_confirms_field_matching_identity_value(self) -> None:
        session = _session()
        sniffed = {"consumerID": "actor-123", "message": "hi", "sessionID": ""}
        identity = {"actorId": "actor-123", "email": "user@example.com"}
        confirmed, ambiguous = session._resolve_extra_fields(sniffed, identity)
        assert confirmed == {"consumerID": "actor-123"}
        assert ambiguous == {}

    def test_unconfirmed_field_is_ambiguous_not_dropped(self) -> None:
        session = _session()
        sniffed = {"tenantId": "t-999", "message": "hi"}
        confirmed, ambiguous = session._resolve_extra_fields(sniffed, None)
        assert confirmed == {}
        assert ambiguous == {"tenantId": ["t-999"]}

    def test_known_message_keys_and_dynamic_fields_are_excluded(self) -> None:
        session = _session()
        sniffed = {"message": "hi", "sessionID": "s1", "conversationId": "c1"}
        confirmed, ambiguous = session._resolve_extra_fields(sniffed, None)
        assert confirmed == {}
        assert ambiguous == {}

    def test_empty_or_non_scalar_values_are_ignored(self) -> None:
        session = _session()
        sniffed = {"message": "hi", "meta": {"nested": True}, "empty": ""}
        confirmed, ambiguous = session._resolve_extra_fields(sniffed, None)
        assert confirmed == {}
        assert ambiguous == {}


class TestMissingCredentials:
    async def test_run_raises_before_launching_browser_when_no_credentials(self) -> None:
        from nuguard.common.errors import BrowserLoginError

        class _NoCreds:
            username = ""
            password = ""

        session = BrowserLoginSession(
            "https://example.com", _NoCreds(), BrowserDiscoveryConfig()  # type: ignore[arg-type]
        )
        with pytest.raises(BrowserLoginError) as exc_info:
            await session.run()
        assert exc_info.value.step == "missing_credentials"
