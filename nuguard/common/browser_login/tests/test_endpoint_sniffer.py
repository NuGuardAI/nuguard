"""Tests for nuguard.common.browser_login.endpoint_sniffer (Phase 2 —
browser-based endpoint discovery)."""
from __future__ import annotations

import pytest

from nuguard.common.browser_login.endpoint_sniffer import crawl_and_sniff


class FakeRequest:
    def __init__(self, method: str, url: str, post_data: str | None = None) -> None:
        self.method = method
        self.url = url
        self.post_data = post_data


class FakeResponse:
    def __init__(self, request: FakeRequest, status: int, body: str = "") -> None:
        self.request = request
        self.status = status
        self.url = request.url
        self._body = body

    async def text(self) -> str:
        return self._body


class FakePage:
    """Minimal stand-in for playwright.async_api.Page: records request/response
    listeners and replays a scripted (request, response) pair (or raises a
    scripted exception) whenever ``goto`` is called for a matching URL."""

    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}
        self.goto_calls: list[str] = []
        self.nav_script: dict[
            str, list[tuple[FakeRequest, FakeResponse | None]] | Exception
        ] = {}

    def on(self, event: str, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler) -> None:
        handlers = self._handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    def _fire(self, event: str, *args: object) -> None:
        for h in list(self._handlers.get(event, [])):
            h(*args)

    async def goto(self, url: str, timeout: int | None = None) -> None:
        self.goto_calls.append(url)
        outcome = self.nav_script.get(url)
        if outcome is None:
            return
        if isinstance(outcome, Exception):
            raise outcome
        for req, resp in outcome:
            self._fire("request", req)
            if resp is not None:
                self._fire("response", resp)

    async def wait_for_timeout(self, ms: int) -> None:
        return None


class FakeSession:
    """Minimal stand-in for BrowserLoginSession — just enough surface for
    crawl_and_sniff (target_url, .page)."""

    def __init__(self, target_url: str, page: FakePage | None) -> None:
        self.target_url = target_url
        self._page = page

    @property
    def page(self) -> FakePage:
        if self._page is None:
            raise AssertionError("BrowserLoginSession not started — use 'async with'")
        return self._page

    async def _sniff_chat_request(self, message: str) -> tuple[str | None, dict | None]:
        return None, None


@pytest.mark.asyncio
async def test_crawl_captures_multiple_requests_not_just_first() -> None:
    page = FakePage()
    session = FakeSession("http://example.test", page)
    req_a = FakeRequest("GET", "http://example.test/a")
    req_b = FakeRequest("GET", "http://example.test/b")
    page.nav_script["http://example.test/a"] = [(req_a, FakeResponse(req_a, 200))]
    page.nav_script["http://example.test/b"] = [(req_b, FakeResponse(req_b, 200))]

    result = await crawl_and_sniff(session, nav_targets=["/a", "/b"])

    paths = {r.path for r in result}
    assert paths == {"/a", "/b"}


@pytest.mark.asyncio
async def test_asset_urls_filtered() -> None:
    page = FakePage()
    session = FakeSession("http://example.test", page)
    req_js = FakeRequest("GET", "http://example.test/bundle.js")
    req_api = FakeRequest("GET", "http://example.test/api/data")
    page.nav_script["http://example.test/page"] = [
        (req_js, FakeResponse(req_js, 200)),
        (req_api, FakeResponse(req_api, 200)),
    ]

    result = await crawl_and_sniff(session, nav_targets=["/page"])

    assert [r.path for r in result] == ["/api/data"]


@pytest.mark.asyncio
async def test_cross_origin_filtered() -> None:
    page = FakePage()
    session = FakeSession("http://example.test", page)
    req_cdn = FakeRequest("GET", "http://cdn.other.test/analytics")
    req_api = FakeRequest("GET", "http://example.test/api/data")
    page.nav_script["http://example.test/page"] = [
        (req_cdn, FakeResponse(req_cdn, 200)),
        (req_api, FakeResponse(req_api, 200)),
    ]

    result = await crawl_and_sniff(session, nav_targets=["/page"])

    assert [r.path for r in result] == ["/api/data"]


@pytest.mark.asyncio
async def test_response_body_not_captured_full_only_keys() -> None:
    page = FakePage()
    session = FakeSession("http://example.test", page)
    req = FakeRequest(
        "POST",
        "http://example.test/api/orders",
        post_data='{"item_id": "sku-1", "quantity": 2}',
    )
    page.nav_script["http://example.test/page"] = [(req, FakeResponse(req, 201))]

    result = await crawl_and_sniff(session, nav_targets=["/page"])

    assert len(result) == 1
    assert set(result[0].request_body_keys) == {"item_id", "quantity"}
    # No raw body field exists on SniffedRequest at all — this is a
    # structural guarantee, not just an empty-string check.
    assert not hasattr(result[0], "request_body")


@pytest.mark.asyncio
async def test_max_capture_count_bounds_collection() -> None:
    page = FakePage()
    session = FakeSession("http://example.test", page)
    requests = [FakeRequest("GET", f"http://example.test/api/item/{i}") for i in range(10)]
    page.nav_script["http://example.test/page"] = [(r, FakeResponse(r, 200)) for r in requests]

    result = await crawl_and_sniff(session, nav_targets=["/page"], max_requests=3)

    assert len(result) <= 3


@pytest.mark.asyncio
async def test_missing_chromium_returns_empty_list_not_raise() -> None:
    session = FakeSession("http://example.test", None)

    result = await crawl_and_sniff(session, nav_targets=["/a"])

    assert result == []


@pytest.mark.asyncio
async def test_nav_target_failure_does_not_abort_remaining_targets() -> None:
    page = FakePage()
    session = FakeSession("http://example.test", page)
    page.nav_script["http://example.test/broken"] = TimeoutError("navigation timed out")
    req = FakeRequest("GET", "http://example.test/api/ok")
    page.nav_script["http://example.test/ok"] = [(req, FakeResponse(req, 200))]

    result = await crawl_and_sniff(session, nav_targets=["/broken", "/ok"])

    assert [r.path for r in result] == ["/api/ok"]
