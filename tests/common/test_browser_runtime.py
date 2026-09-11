"""Smoke coverage for the optional Playwright Chromium runtime."""

from __future__ import annotations

import os

import pytest

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
