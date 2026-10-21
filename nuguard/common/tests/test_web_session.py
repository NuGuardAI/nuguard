"""Automatic cookie + CSRF session bootstrap during endpoint discovery."""

from __future__ import annotations

import logging
import uuid

import httpx
import pytest
import respx

from nuguard.common.endpoint_detection.live_probe import probe_endpoint
from nuguard.common.endpoint_detection.resolver import resolve_chat_endpoint
from nuguard.common.web_session import (
    SessionSignals,
    WebSession,
    WebSessionBootstrapper,
    extract_csrf_token,
    ui_root_for,
)
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType

BASE = "http://app.test"
CHAT = "/vendor/api/v1/chat"
COOKIE_VALUE = "s3cr3t-cookie"
TOKEN_VALUE = "t0ken-abc123"


def _sbom(path: str = CHAT) -> AiSbomDocument:
    node = Node(
        id=uuid.uuid5(uuid.NAMESPACE_URL, path),
        name=path,
        component_type=ComponentType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(endpoint=path, method="POST", chat_payload_key="message", chat_payload_list=False),
    )
    return AiSbomDocument(target="./app", nodes=[node])


def _mock_csrf_app(mock: respx.MockRouter, *, token_in: str = "meta") -> dict[str, int]:
    """FinBot-like app: GET /vendor/ issues cookie + token; chat requires both."""
    calls = {"chat": 0, "ui": 0}

    def ui(request: httpx.Request) -> httpx.Response:
        calls["ui"] += 1
        headers = {"set-cookie": f"finbot_session={COOKIE_VALUE}; Path=/"}
        if token_in == "meta":
            body = f'<html><head><meta name="csrf-token" content="{TOKEN_VALUE}"></head></html>'
        elif token_in == "input":
            body = f'<form><input type="hidden" name="csrf_token" value="{TOKEN_VALUE}"></form>'
        else:  # XSRF-TOKEN cookie
            body = "<html></html>"
            headers = {"set-cookie": f"XSRF-TOKEN={TOKEN_VALUE}; Path=/"}
        return httpx.Response(200, text=body, headers={"content-type": "text/html", **headers})

    def chat(request: httpx.Request) -> httpx.Response:
        calls["chat"] += 1
        sent = {k.lower(): v for k, v in request.headers.items()}
        header = "x-xsrf-token" if token_in == "cookie" else "x-csrf-token"
        if sent.get(header) != TOKEN_VALUE or TOKEN_VALUE not in sent.get("cookie", "") + sent.get(header, ""):
            return httpx.Response(403, json={"detail": "CSRF token missing or invalid"})
        body = request.content.decode() or "{}"
        if '"message"' not in body:
            return httpx.Response(400, json={"detail": "message is required"})
        return httpx.Response(200, json={"response": "Hello! How can I help?"})

    mock.get("/vendor/").mock(side_effect=ui)
    mock.get("/").mock(return_value=httpx.Response(404))
    mock.get("/openapi.json").mock(return_value=httpx.Response(404))
    mock.post(CHAT).mock(side_effect=chat)
    mock.route(method="POST").mock(return_value=httpx.Response(404))
    return calls


class TestPureHelpers:
    def test_ui_root_for(self) -> None:
        assert ui_root_for("/vendor/api/v1/chat") == "/vendor/"
        assert ui_root_for("/api/chat") is None
        assert ui_root_for("/chat") is None

    @pytest.mark.parametrize(
        ("status", "body", "attr"),
        [
            (403, "CSRF token missing", "csrf_rejections"),
            (400, "Invalid session", "session_rejections"),
            (401, "nope", "other_rejections"),
        ],
    )
    def test_signals_classify_rejections(self, status: int, body: str, attr: str) -> None:
        signals = SessionSignals()
        signals.observe(httpx.Response(status, text=body), "/vendor/api/v1/chat")
        assert getattr(signals, attr) == 1
        assert signals.triggered
        assert signals.ui_roots == ["/vendor/"]

    def test_success_and_404_do_not_trigger(self) -> None:
        signals = SessionSignals()
        signals.observe(httpx.Response(200, json={}), "/a")
        signals.observe(httpx.Response(404), "/b")
        assert not signals.triggered

    def test_extract_token_prefers_html_meta(self) -> None:
        resp = httpx.Response(
            200, text=f'<meta name="csrf-token" content="{TOKEN_VALUE}">', headers={"content-type": "text/html"}
        )
        assert extract_csrf_token(resp, httpx.Cookies()) == ("X-CSRF-Token", TOKEN_VALUE)

    def test_extract_token_none(self) -> None:
        resp = httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})
        assert extract_csrf_token(resp, httpx.Cookies()) == ("", "")

    def test_repr_hides_secrets(self) -> None:
        session = WebSession(f"a={COOKIE_VALUE}", "X-CSRF-Token", TOKEN_VALUE, "/vendor/")
        assert COOKIE_VALUE not in repr(session)
        assert TOKEN_VALUE not in repr(session)


class TestBootstrapper:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("token_in", "header"), [("meta", "X-CSRF-Token"), ("input", "X-CSRF-Token"), ("cookie", "X-XSRF-TOKEN")])
    async def test_acquire_token_sources(self, token_in: str, header: str) -> None:
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            _mock_csrf_app(mock, token_in=token_in)
            session = await WebSessionBootstrapper(BASE).acquire(["/vendor/"])
        assert session is not None
        assert session.headers()[header] == TOKEN_VALUE
        assert "Cookie" in session.headers()

    @pytest.mark.asyncio
    async def test_acquire_returns_none_when_pages_missing(self) -> None:
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            mock.route().mock(return_value=httpx.Response(404))
            assert await WebSessionBootstrapper(BASE).acquire(["/vendor/", "/"]) is None

    @pytest.mark.asyncio
    async def test_acquire_survives_network_error(self) -> None:
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            mock.route().mock(side_effect=httpx.ConnectError("boom"))
            assert await WebSessionBootstrapper(BASE).acquire(["/"]) is None

    @pytest.mark.asyncio
    async def test_validate_rejects_useless_session(self) -> None:
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            _mock_csrf_app(mock)
            bad = WebSession("x=y", "X-CSRF-Token", "wrong", "/vendor/")
            good = await WebSessionBootstrapper(BASE).acquire(["/vendor/"])
            assert good is not None
            boot = WebSessionBootstrapper(BASE)
            assert not await boot.validate(bad, CHAT)
            assert await boot.validate(good, CHAT)


class TestDiscoveryIntegration:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("token_in", ["meta", "input", "cookie"])
    async def test_probe_acquires_session_and_confirms(self, token_in: str) -> None:
        sink: dict[str, str] = {}
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            _mock_csrf_app(mock, token_in=token_in)
            result = await probe_endpoint(BASE, _sbom(), session_sink=sink)
        assert result is not None and result.confirmed
        assert result.path == CHAT
        assert TOKEN_VALUE in " ".join(sink.values()) or TOKEN_VALUE in sink.get("Cookie", "")

    @pytest.mark.asyncio
    async def test_resolver_returns_session_headers(self) -> None:
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            _mock_csrf_app(mock)
            resolved = await resolve_chat_endpoint(BASE, _sbom())
        assert resolved.path == CHAT
        assert resolved.session_headers.get("X-CSRF-Token") == TOKEN_VALUE
        assert COOKIE_VALUE in resolved.session_headers["Cookie"]

    @pytest.mark.asyncio
    async def test_disabled_flag_skips_bootstrap(self) -> None:
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            calls = _mock_csrf_app(mock)
            result = await probe_endpoint(BASE, _sbom(), session_bootstrap=False)
        assert calls["ui"] == 0
        assert result is None or not result.confirmed

    @pytest.mark.asyncio
    async def test_explicit_cookie_header_is_not_overridden(self) -> None:
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            calls = _mock_csrf_app(mock)
            await probe_endpoint(BASE, _sbom(), auth_headers={"Cookie": "mine=1"})
        assert calls["ui"] == 0

    @pytest.mark.asyncio
    async def test_app_without_csrf_is_unaffected(self) -> None:
        sink: dict[str, str] = {}
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            mock.get("/openapi.json").mock(return_value=httpx.Response(404))
            ui = mock.get("/vendor/").mock(return_value=httpx.Response(200, text="<html></html>"))
            mock.post(CHAT).mock(
                side_effect=lambda req: httpx.Response(200, json={"response": "hi there"})
                if b'"message"' in req.content
                else httpx.Response(400, json={"detail": "message is required"})
            )
            mock.route(method="POST").mock(return_value=httpx.Response(404))
            result = await probe_endpoint(BASE, _sbom(), session_sink=sink)
        assert result is not None and result.confirmed
        assert sink == {} and not ui.called

    @pytest.mark.asyncio
    async def test_unacquirable_session_degrades_gracefully(self) -> None:
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            mock.get("/openapi.json").mock(return_value=httpx.Response(404))
            mock.get("/vendor/").mock(return_value=httpx.Response(404))
            mock.get("/").mock(return_value=httpx.Response(404))
            mock.route(method="POST").mock(return_value=httpx.Response(403, json={"detail": "CSRF token missing"}))
            result = await probe_endpoint(BASE, _sbom())
        assert result is None or not result.confirmed

    @pytest.mark.asyncio
    async def test_no_secrets_in_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            _mock_csrf_app(mock)
            await probe_endpoint(BASE, _sbom(), session_sink={})
        assert COOKIE_VALUE not in caplog.text
        assert TOKEN_VALUE not in caplog.text


class TestMidRunRefresh:
    """TargetAppClient re-acquires an expired web session and retries once."""

    @staticmethod
    def _client(refresher):  # type: ignore[no-untyped-def]
        from nuguard.redteam.target.client import TargetAppClient

        client = TargetAppClient(
            BASE, chat_path=CHAT, default_headers={"Cookie": "old=1", "X-CSRF-Token": "stale"},
        )
        client.set_session_refresher(refresher)
        return client

    @staticmethod
    def _session():  # type: ignore[no-untyped-def]
        from nuguard.redteam.target.session import AttackSession

        return AttackSession(session_id="s", target_url=BASE, chain_id="c")

    @staticmethod
    def _chat_route(mock: respx.MockRouter, calls: list[str]) -> None:
        def chat(request: httpx.Request) -> httpx.Response:
            calls.append(request.headers.get("x-csrf-token", ""))
            if request.headers.get("x-csrf-token") != TOKEN_VALUE:
                return httpx.Response(403, json={"detail": "CSRF token expired"})
            return httpx.Response(200, json={"response": "ok"})

        mock.post(CHAT).mock(side_effect=chat)

    @pytest.mark.asyncio
    async def test_refreshes_and_retries_once(self) -> None:
        refreshed: list[int] = []

        async def refresher() -> dict[str, str]:
            refreshed.append(1)
            return {"X-CSRF-Token": TOKEN_VALUE}

        calls: list[str] = []
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            self._chat_route(mock, calls)
            client = self._client(refresher)
            text, _ = await client.send("hi", self._session())
        assert text == "ok"
        assert calls == ["stale", TOKEN_VALUE]
        assert len(refreshed) == 1

    @pytest.mark.asyncio
    async def test_concurrent_rejections_refresh_once(self) -> None:
        import asyncio

        refreshed: list[int] = []

        async def refresher() -> dict[str, str]:
            refreshed.append(1)
            await asyncio.sleep(0.05)
            return {"X-CSRF-Token": TOKEN_VALUE}

        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            self._chat_route(mock, [])
            client = self._client(refresher)
            results = await asyncio.gather(*(client.send("hi", self._session()) for _ in range(4)))
        assert all(text == "ok" for text, _ in results)
        assert len(refreshed) == 1

    @pytest.mark.asyncio
    async def test_failed_refresh_keeps_original_error(self) -> None:
        async def refresher() -> dict[str, str]:
            return {}

        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            self._chat_route(mock, [])
            client = self._client(refresher)
            text, _ = await client.send("hi", self._session())
        assert text.startswith("[HTTP 403]")

    @pytest.mark.asyncio
    async def test_plain_authz_denial_is_not_replayed(self) -> None:
        refreshed: list[int] = []

        async def refresher() -> dict[str, str]:
            refreshed.append(1)
            return {"X-CSRF-Token": TOKEN_VALUE}

        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            route = mock.post(CHAT).mock(return_value=httpx.Response(403, json={"detail": "Forbidden: admin only"}))
            client = self._client(refresher)
            await client.send("hi", self._session())
        assert route.call_count == 1 and not refreshed

    @pytest.mark.asyncio
    async def test_server_error_is_not_replayed(self) -> None:
        async def refresher() -> dict[str, str]:
            raise AssertionError("must not refresh on 500")

        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            route = mock.post(CHAT).mock(return_value=httpx.Response(500, text="boom"))
            client = self._client(refresher)
            await client.send("hi", self._session())
        assert route.call_count >= 1

    @pytest.mark.asyncio
    async def test_invoke_endpoint_refreshes_but_strip_auth_does_not(self) -> None:
        async def refresher() -> dict[str, str]:
            return {"X-CSRF-Token": TOKEN_VALUE}

        calls: list[str] = []
        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            self._chat_route(mock, calls)
            client = self._client(refresher)
            status, _, _ = await client.invoke_endpoint(CHAT, body={"message": "x"})
            assert status == 200
            calls.clear()
            status, _, _ = await client.invoke_endpoint(CHAT, body={"message": "x"}, strip_auth=True)
            assert status == 403 and len(calls) == 1

    @pytest.mark.asyncio
    async def test_make_session_refresher_reacquires(self) -> None:
        from nuguard.common.web_session import make_session_refresher

        with respx.mock(base_url=BASE, assert_all_called=False) as mock:
            _mock_csrf_app(mock)
            fresh = await make_session_refresher(BASE, ["/vendor/"])()
        assert fresh["X-CSRF-Token"] == TOKEN_VALUE

    def test_is_session_rejection(self) -> None:
        from nuguard.common.web_session import is_session_rejection

        assert is_session_rejection(419, "")
        assert is_session_rejection(403, "invalid CSRF token")
        assert not is_session_rejection(403, "admin only")
        assert not is_session_rejection(500, "csrf")
