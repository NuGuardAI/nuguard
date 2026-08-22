"""Tests for discover_api_origin_from_frontend_bundle.

Covers the case where a SPA (Vite/CRA/webpack) calls a separate-origin
backend directly from client-side JS instead of proxying /api through its
own server — SBOM candidate rotation and live endpoint probing both fail
against the frontend origin in that case, since every path is served by the
SPA's catch-all route. This mirrors what a real browser does: fetch the
page, follow its <script src> tags, and read the API origin out of the
bundle the same way the app's own JS would at runtime.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from nuguard.common.endpoint_probe import discover_api_origin_from_frontend_bundle

BASE = "http://frontend-only.test"

_SPA_HTML = """\
<!doctype html>
<html>
<head><script type="module" src="/assets/index-abc123.js"></script></head>
<body><div id="root"></div></body>
</html>
"""


@pytest.mark.asyncio
@respx.mock
async def test_discovers_baked_in_api_origin_from_bundle():
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(200, text=_SPA_HTML))
    bundle = 'const API_CONFIG={baseURL:"http://backend-host.internal:3010/api/v1",timeout:3e4};'
    respx.get(f"{BASE}/assets/index-abc123.js").mock(
        return_value=httpx.Response(200, text=bundle)
    )

    origin, notes = await discover_api_origin_from_frontend_bundle(BASE)

    assert origin == "http://backend-host.internal:3010"
    assert len(notes) == 1
    assert "backend-host.internal:3010" in notes[0]


@pytest.mark.asyncio
@respx.mock
async def test_no_script_tags_returns_none():
    respx.get(BASE).mock(return_value=httpx.Response(200, text="<html><body>ok</body></html>"))

    origin, notes = await discover_api_origin_from_frontend_bundle(BASE)

    assert origin is None
    assert notes == []


@pytest.mark.asyncio
@respx.mock
async def test_bundle_with_no_base_url_pattern_returns_none():
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(200, text=_SPA_HTML))
    respx.get(f"{BASE}/assets/index-abc123.js").mock(
        return_value=httpx.Response(200, text="console.log('no api config here')")
    )

    origin, notes = await discover_api_origin_from_frontend_bundle(BASE)

    assert origin is None
    assert notes == []


@pytest.mark.asyncio
@respx.mock
async def test_same_origin_match_is_not_treated_as_an_override():
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(200, text=_SPA_HTML))
    # baseURL host/port match target_url's own host/port (default port 80) — nothing to change.
    bundle = 'const cfg={apiUrl:"http://frontend-only.test/api/v1"};'
    respx.get(f"{BASE}/assets/index-abc123.js").mock(
        return_value=httpx.Response(200, text=bundle)
    )

    origin, notes = await discover_api_origin_from_frontend_bundle(BASE)

    assert origin is None
    assert notes == []


@pytest.mark.asyncio
@respx.mock
async def test_unreachable_target_returns_none_without_raising():
    respx.get(BASE).mock(side_effect=httpx.ConnectError("connection refused"))

    origin, notes = await discover_api_origin_from_frontend_bundle(BASE)

    assert origin is None
    assert notes == []
