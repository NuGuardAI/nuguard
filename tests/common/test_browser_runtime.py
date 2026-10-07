"""Smoke coverage for the optional Playwright Chromium runtime."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

import pytest

if TYPE_CHECKING:
    from playwright.async_api import Route

    from nuguard.common.browser_login.session import BrowserLoginSession

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
