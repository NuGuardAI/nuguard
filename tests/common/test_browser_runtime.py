"""Smoke coverage for the optional Playwright Chromium runtime."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterator

import pytest

if TYPE_CHECKING:
    from playwright.async_api import Route

    from nuguard.common.browser_login.session import BrowserLoginSession
    from nuguard.common.endpoint_detection.live_probe import ProbeResult

_REQUIRE_BROWSER_TESTS_ENV = "NUGUARD_REQUIRE_BROWSER_TESTS"


def test_chromium_runtime_can_render_and_execute_javascript() -> None:
    if os.getenv(_REQUIRE_BROWSER_TESTS_ENV) != "1":
        pytest.skip(f"set {_REQUIRE_BROWSER_TESTS_ENV}=1 to require the Playwright browser runtime")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise AssertionError(
            "browser tests require the 'browser' extra to install Playwright"
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<main data-nuguard-browser-ready="true">ready</main>')

            ready = page.locator('[data-nuguard-browser-ready="true"]')

            assert ready.inner_text() == "ready"
            assert page.evaluate("6 * 7") == 42
        finally:
            browser.close()


@pytest.fixture
async def chat_browser() -> AsyncIterator[BrowserLoginSession]:
    if os.getenv(_REQUIRE_BROWSER_TESTS_ENV) != "1":
        pytest.skip(f"set {_REQUIRE_BROWSER_TESTS_ENV}=1 to require Chromium")
    from nuguard.common.auth import AuthConfig
    from nuguard.common.browser_login.config import BrowserDiscoveryConfig
    from nuguard.common.browser_login.session import BrowserLoginSession

    async with BrowserLoginSession(
        "https://app.test", AuthConfig(type="none"),
        BrowserDiscoveryConfig(chat_ui_timeout_ms=1000),
    ) as session:
        yield session


async def _serve_chat_page(session: BrowserLoginSession, html: str) -> list[dict[str, Any]]:
    requests = []

    async def serve(route: Route) -> None:
        if route.request.url.endswith("/api/chat"):
            requests.append(json.loads(route.request.post_data))
            await route.fulfill(content_type="application/json", body='{"outputs":[{"text":"Hello"}]}')
        elif route.request.url.endswith("/api/profiles"):
            await route.fulfill(content_type="application/json", body='{"profiles":[]}')
        elif route.request.resource_type == "document":
            await route.fulfill(content_type="text/html", body=html)
        else:
            await route.fulfill(status=204)

    await session.page.route("**/*", serve)
    await session.page.goto("https://app.test")
    return requests


async def test_browser_discovers_actual_collapsed_blissful_widget(chat_browser: BrowserLoginSession) -> None:
    html = (Path(__file__).parents[1] / "apps/blissful-store/webapp/index.html").read_text(encoding="utf-8")
    requests = await _serve_chat_page(chat_browser, html)
    assert not await chat_browser.page.locator("#chat-input").is_visible()

    endpoint, body = await chat_browser._sniff_chat_request("issue628 probe")

    assert endpoint == "https://app.test/api/chat"
    assert body["text"] == "issue628 probe"
    assert requests[0]["text"] == "issue628 probe"
    assert await chat_browser.page.locator("#chat-input").is_visible()


@pytest.mark.parametrize("initially_open", [False, True])
async def test_browser_ignores_unrelated_form_and_background_post(
    chat_browser: BrowserLoginSession, initially_open: bool
) -> None:
    hidden = "" if initially_open else "hidden"
    html = f'''<form id="contact"><textarea></textarea><button>Send</button></form>
    <button aria-label="Open chat" onclick="document.getElementById('chat').hidden=false;fetch('/metrics',{{method:'POST',body:'{{}}'}})">Chat</button>
    <form id="chat" {hidden} onsubmit="event.preventDefault();fetch('/api/chat',{{method:'POST',body:JSON.stringify({{message:this.querySelector('textarea').value}})}})">
    <textarea placeholder="Message" oninput="fetch('/metrics',{{method:'POST',body:'{{}}'}})"></textarea><button>Send</button></form>'''
    requests = await _serve_chat_page(chat_browser, html)

    endpoint, body = await chat_browser._sniff_chat_request("issue628 probe")

    assert endpoint == "https://app.test/api/chat"
    assert body == {"message": "issue628 probe"}
    assert len(requests) == 1
    assert await chat_browser.page.locator("#contact textarea").input_value() == ""


async def test_browser_does_not_close_visible_loading_widget(chat_browser: BrowserLoginSession) -> None:
    html = '''<button aria-label="Open chat" onclick="document.getElementById('chat').hidden=true">Toggle</button>
    <form id="chat" onsubmit="event.preventDefault();fetch('/api/chat',{method:'POST',body:JSON.stringify({message:this.querySelector('textarea').value})})">
    <textarea placeholder="Message" disabled></textarea><button>Send</button></form>
    <script>setTimeout(()=>document.querySelector('textarea').disabled=false,300)</script>'''
    await _serve_chat_page(chat_browser, html)

    endpoint, _ = await chat_browser._sniff_chat_request("issue628 probe")

    assert endpoint == "https://app.test/api/chat"
    assert await chat_browser.page.locator("#chat").is_visible()


async def test_browser_failed_opener_is_bounded_and_recovery_works(chat_browser: BrowserLoginSession) -> None:
    html = '''<button aria-label="Open chat" onclick="window.clicks=(window.clicks||0)+1">Chat</button>
    <form id="chat" hidden onsubmit="event.preventDefault();fetch('/api/chat',{method:'POST',body:JSON.stringify({message:this.querySelector('textarea').value})})">
    <textarea placeholder="Message"></textarea><button>Send</button></form>'''
    await _serve_chat_page(chat_browser, html)

    assert await asyncio.wait_for(chat_browser._sniff_chat_request("probe"), timeout=2) == (None, None)
    assert await chat_browser.page.evaluate("window.clicks") == 1
    assert "timeout" in chat_browser.chat_sniff_note
    await chat_browser.page.evaluate("document.getElementById('chat').hidden=false")
    assert (await chat_browser._sniff_chat_request("probe"))[0] == "https://app.test/api/chat"


async def test_browser_configured_opener_and_hidden_first_match(chat_browser: BrowserLoginSession) -> None:
    chat_browser.browser_cfg.chat_opener_selector = "#custom-open"
    chat_browser.browser_cfg.chat_input_selector = "textarea"
    html = '''<textarea hidden></textarea>
    <button id="custom-open" onclick="document.getElementById('chat').hidden=false">Support</button>
    <form id="chat" hidden onsubmit="event.preventDefault();fetch('/api/chat',{method:'POST',body:JSON.stringify({messages:[{role:'user',content:this.querySelector('textarea').value}]})})">
    <textarea></textarea><button>Send</button></form>'''
    await _serve_chat_page(chat_browser, html)

    endpoint, body = await chat_browser._sniff_chat_request("probe")

    assert endpoint == "https://app.test/api/chat"
    assert body == {"messages": [{"role": "user", "content": "probe"}]}
    assert await chat_browser.page.locator("textarea").first.input_value() == ""


@pytest.mark.parametrize("delayed", [False, True])
async def test_browser_waits_for_inserted_or_readonly_input(
    chat_browser: BrowserLoginSession, delayed: bool,
) -> None:
    initial = "" if delayed else '<textarea placeholder="Message" readonly></textarea>'
    change = (
        "document.getElementById('chat').innerHTML='<textarea placeholder=Message></textarea>'"
        if delayed else "document.querySelector('textarea').readOnly=false"
    )
    await _serve_chat_page(chat_browser, f'''<button aria-label="Open chat"
        onclick="window.toggles=(window.toggles||0)+1">Chat</button>
        <form id="chat">{initial}</form><script>setTimeout(()=>{{{change}}},200)</script>''')

    ready = await asyncio.wait_for(chat_browser._prepare_chat_input(), timeout=2)

    assert ready is not None
    assert await ready.is_visible()
    assert await ready.is_enabled()
    assert await ready.is_editable()
    if not delayed:
        assert await chat_browser.page.evaluate("window.toggles||0") == 0


@pytest.mark.parametrize("enter_only", [False, True])
async def test_browser_skips_unusable_openers_and_send_controls(
    chat_browser: BrowserLoginSession, enter_only: bool,
) -> None:
    chat_browser.browser_cfg.chat_opener_selector = ".support-open"
    send = "" if enter_only else '<button hidden>Send</button><button disabled>Send</button><button>Send</button>'
    html = f'''<form id="contact"><input><button onclick="window.contactClicks=1">Send</button></form>
        <button class="support-open" hidden>Support</button>
        <button class="support-open" disabled>Support</button>
        <button class="support-open" onclick="document.getElementById('chat').hidden=false;window.opens=(window.opens||0)+1">Support</button>
        <form id="chat" hidden onsubmit="event.preventDefault();fetch('/api/chat',{{method:'POST',body:JSON.stringify({{message:this.querySelector('input').value}})}})">
        <input placeholder="Message">{send}</form>'''
    requests = await _serve_chat_page(chat_browser, html)

    endpoint, body = await chat_browser._sniff_chat_request("probe")

    assert endpoint == "https://app.test/api/chat"
    assert body == {"message": "probe"}
    assert len(requests) == 1
    assert await chat_browser.page.evaluate("window.opens") == 1
    assert await chat_browser.page.evaluate("window.contactClicks||0") == 0
    assert await chat_browser.page.locator("#contact input").input_value() == ""


async def test_browser_invalid_selectors_fail_bounded_without_leaking_values(
    chat_browser: BrowserLoginSession,
) -> None:
    chat_browser.browser_cfg.chat_ui_timeout_ms = 250
    chat_browser.browser_cfg.chat_input_selector = "textarea[fixture-secret="
    chat_browser.browser_cfg.chat_opener_selector = "button[fixture-secret="
    await _serve_chat_page(chat_browser, '<form id="contact"><textarea></textarea></form>')

    assert await asyncio.wait_for(chat_browser._sniff_chat_request("probe"), timeout=2) == (None, None)
    assert "timeout" in chat_browser.chat_sniff_note.lower()
    assert "fixture-secret" not in chat_browser.chat_sniff_note


@pytest.fixture
def loopback_chat_app() -> Iterator[tuple[str, dict[str, Any]]]:
    """Serve a collapsed custom widget and optional cookie-protected login."""
    if os.getenv(_REQUIRE_BROWSER_TESTS_ENV) != "1":
        pytest.skip(f"set {_REQUIRE_BROWSER_TESTS_ENV}=1 to require Chromium")
    state: dict[str, Any] = {"posts": [], "require_auth": False, "usable_reply": True}
    chat_html = '''<button id="reveal" onclick="document.getElementById('support').hidden=false">Assistance</button>
        <form id="support" hidden onsubmit="event.preventDefault();fetch('/dispatch-628',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({utterance:document.getElementById('entry').value})})">
        <textarea id="entry"></textarea><button id="submit-chat">Submit</button></form>'''
    login_html = '''<form onsubmit="event.preventDefault();fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:this.username.value,password:this.password.value})}).then(()=>location.href='/')">
        <input name="username" autocomplete="username"><input name="password" type="password">
        <button type="submit">Sign in</button></form>'''

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def respond(self, code: int, payload: Any, *, html: bool = False, cookie: bool = False) -> None:
            body = (payload if html else json.dumps(payload)).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html" if html else "application/json")
            self.send_header("Content-Length", str(len(body)))
            if cookie:
                self.send_header("Set-Cookie", "sid=fixture-cookie-secret; HttpOnly; Path=/; SameSite=Lax")
            self.end_headers()
            self.wfile.write(body)

        def authenticated(self) -> bool:
            return not state["require_auth"] or "sid=fixture-cookie-secret" in self.headers.get("Cookie", "")

        def do_GET(self) -> None:
            if self.path == "/":
                self.respond(200, chat_html if self.authenticated() else login_html, html=True)
            elif self.path == "/whoami" and self.authenticated():
                self.respond(200, {"id": "fixture-user"})
            else:
                self.respond(404, {"error": "not found"})

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if self.path == "/login":
                if body == {"username": "fixture-user", "password": "fixture-password-secret"}:
                    self.respond(200, {"ok": True}, cookie=True)
                else:
                    self.respond(401, {"error": "invalid credentials"})
                return
            if self.path != "/dispatch-628":
                self.respond(404, {"error": "not found"})
                return
            state["posts"].append({
                "body": body, "cookie": self.headers.get("Cookie", ""),
                "agent": self.headers.get("User-Agent", ""),
            })
            if not self.authenticated():
                self.respond(401, {"error": "unauthorized"})
            elif not isinstance(body.get("utterance"), str):
                self.respond(400, {"error": "utterance is required"})
            else:
                self.respond(200, {"response": "Hello from support"} if state["usable_reply"] else {"ok": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize("usable_reply", [True, False])
async def test_browser_resolver_confirms_http_before_persisting(
    loopback_chat_app: tuple[str, dict[str, Any]], tmp_path: Path, usable_reply: bool,
) -> None:
    from nuguard.common.auto_sbom_enricher import persist_probe_result_to_sbom
    from nuguard.common.browser_login.config import BrowserDiscoveryConfig
    from nuguard.common.endpoint_detection.resolver import resolve_chat_endpoint
    from nuguard.common.endpoint_detection.sbom import find_confirmed_chat_endpoint
    from nuguard.sbom.models import AiSbomDocument

    target, state = loopback_chat_app
    state["usable_reply"] = usable_reply
    document = AiSbomDocument(target=target)
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(document.model_dump_json(), encoding="utf-8")
    confirmations: list[ProbeResult] = []

    def persist(probe: ProbeResult) -> None:
        confirmations.append(probe)
        persist_probe_result_to_sbom(probe, document, sbom_path)

    result = await resolve_chat_endpoint(
        target, document, timeout=2, enable_browser_fallback=True,
        browser_discovery=BrowserDiscoveryConfig(
            chat_opener_selector="#reveal", chat_input_selector="#entry",
            send_button_selector="#submit-chat", chat_ui_timeout_ms=1000,
        ), probe_result_callback=persist,
    )

    assert any("Mozilla" in request["agent"] for request in state["posts"])
    assert any("nuguard-probe" in request["agent"] for request in state["posts"])
    if usable_reply:
        assert result.path == "/dispatch-628"
        assert result.path_source.value == "browser"
        assert result.payload_key == "utterance"
        assert len(confirmations) == 1
        enriched = AiSbomDocument.model_validate_json(
            (tmp_path / "app.sbom.enriched.json").read_text(encoding="utf-8")
        )
        assert find_confirmed_chat_endpoint(enriched)[:3] == ("/dispatch-628", "utterance", False)
        assert "fixture-cookie-secret" not in enriched.model_dump_json()
    else:
        assert result.path is None
        assert confirmations == []
        assert not (tmp_path / "app.sbom.enriched.json").exists()
        assert any("usable chat reply" in note for note in result.notes)


async def test_browser_authenticated_widget_reuses_cookie_without_diagnostic_leaks(
    loopback_chat_app: tuple[str, dict[str, Any]], caplog: pytest.LogCaptureFixture,
) -> None:
    from nuguard.common.auth import AuthConfig
    from nuguard.common.browser_login.config import BrowserDiscoveryConfig
    from nuguard.common.browser_login.session import BrowserLoginSession

    target, state = loopback_chat_app
    state["require_auth"] = True
    config = BrowserDiscoveryConfig(
        username_selector="input[name=username]", password_selector="input[name=password]",
        submit_selector="button[type=submit]", post_login_wait_selector="#reveal",
        identity_endpoint="/whoami", chat_opener_selector="#reveal",
        chat_input_selector="#entry", send_button_selector="#submit-chat",
        chat_ui_timeout_ms=1000, extra_wait_ms=0,
    )
    async with BrowserLoginSession(target, AuthConfig(
        type="basic", username="fixture-user", password="fixture-password-secret",
    ), config) as session:
        result = await session.run(chat_message="Hello")
        assert result.sniffed_endpoint == target + "/dispatch-628"
        assert result.identity_payload == {"id": "fixture-user"}
        async with session.page.expect_response(target + "/dispatch-628") as response:
            assert (await session._sniff_chat_request("Second benign probe"))[0] == result.sniffed_endpoint
        assert (await response.value).status == 200
        assert len(state["posts"]) == 2
        assert all("sid=fixture-cookie-secret" in request["cookie"] for request in state["posts"])
    diagnostics = json.dumps(result.warnings) + caplog.text
    assert "fixture-password-secret" not in diagnostics
    assert "fixture-cookie-secret" not in diagnostics


@pytest.mark.parametrize("usable_reply", [True, False])
async def test_browser_public_verification_returns_confirmed_or_structured_failure(
    loopback_chat_app: tuple[str, dict[str, Any]], usable_reply: bool,
) -> None:
    from nuguard.common.browser_login.config import BrowserDiscoveryConfig
    from nuguard.common.target_verify_public_api import TargetVerifyRequest, verify_target

    target, state = loopback_chat_app
    state["usable_reply"] = usable_reply
    result = await verify_target(TargetVerifyRequest(
        target_url=target, request_timeout=2,
        browser_discovery=BrowserDiscoveryConfig(
            chat_opener_selector="#reveal", chat_input_selector="#entry",
            send_button_selector="#submit-chat", chat_ui_timeout_ms=1000,
        ),
    ))

    if usable_reply:
        assert result.all_ok
        assert result.endpoint == "/dispatch-628"
        assert result.endpoint_source == "browser"
    else:
        assert not result.all_ok
        assert result.checks[0].status == "endpoint_not_found"
    assert "fixture-password-secret" not in result.model_dump_json()
    assert "fixture-cookie-secret" not in result.model_dump_json()


@pytest.mark.parametrize("command", ["target", "behavior", "redteam"])
def test_browser_custom_settings_through_real_cli_consumers(
    loopback_chat_app: tuple[str, dict[str, Any]], tmp_path: Path, command: str,
) -> None:
    import yaml
    from typer.testing import CliRunner

    from nuguard.cli.main import app
    from nuguard.sbom.models import AiSbomDocument

    target, state = loopback_chat_app
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(AiSbomDocument(target=target).model_dump_json(), encoding="utf-8")
    settings: dict[str, Any] = {
        "sbom": str(sbom_path), "target": {
            "url": target, "browser_discovery": {
                "chat_opener_selector": "#reveal" if command == "target" else "#wrong-shared",
                "chat_input_selector": "#entry", "send_button_selector": "#submit-chat",
                "chat_ui_timeout_ms": 1000,
            },
        },
    }
    if command != "target":
        settings[command] = {"target": target, "skip_discovery": True,
                             "browser_discovery": {"chat_opener_selector": "#reveal"}}
    config_path = tmp_path / "nuguard.yaml"
    config_path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    args = ["target", "verify"] if command == "target" else [command]
    args += ["--config", str(config_path), "--sbom", str(sbom_path)]
    if command == "behavior":
        args.append("--dynamic")
    if command != "target":
        args += ["--output", str(tmp_path / f"{command}-report.md")]

    result = CliRunner().invoke(app, args, catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert any("Mozilla" in request["agent"] for request in state["posts"])
    assert any("nuguard-probe" in request["agent"] for request in state["posts"])
    assert "fixture-cookie-secret" not in result.output
    assert "fixture-password-secret" not in result.output
