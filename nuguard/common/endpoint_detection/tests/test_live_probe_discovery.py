"""Tests for issue #532: endpoint discovery without an SBOM, the extended
fallback path list, and _blind_probe's early-exit correctness for status
codes that carry no information about payload shape (401/403/429).
"""
from __future__ import annotations

import httpx
import pytest
import respx

from nuguard.common.endpoint_detection.live_probe import probe_chat_endpoints
from nuguard.common.errors import TargetRateLimitedError

TARGET = "http://target.test"


@pytest.mark.anyio
@respx.mock
async def test_probe_without_sbom_finds_new_fallback_path() -> None:
    # sbom=None must not crash (_sbom_post_paths/_sbom_websocket_paths tolerate
    # None) and must still search the extended HTTP_ENDPOINT_FALLBACK_PATHS
    # list, including the 14 paths added for issue #532.
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))  # OpenAPI probes, WS upgrade GETs
    respx.post(f"{TARGET}/api/agent/chat").mock(
        return_value=httpx.Response(200, json={"response": "hello"})
    )
    respx.post(url__regex=r".*").mock(return_value=httpx.Response(404))  # every other candidate

    result = await probe_chat_endpoints(TARGET, sbom=None)

    assert result is not None
    assert result.path == "/api/agent/chat"


@pytest.mark.anyio
@respx.mock
async def test_probe_without_sbom_no_candidate_returns_none() -> None:
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(url__regex=r".*").mock(return_value=httpx.Response(404))

    result = await probe_chat_endpoints(TARGET, sbom=None)

    assert result is None


@pytest.mark.anyio
@respx.mock
async def test_401_on_first_shape_does_not_try_remaining_shapes() -> None:
    # An auth rejection is independent of the payload key — the remaining
    # PROBE_PAYLOADS shapes must not be tried on this path.
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    route = respx.post(f"{TARGET}/chat").mock(return_value=httpx.Response(401))
    respx.post(url__regex=r".*").mock(return_value=httpx.Response(404))

    result = await probe_chat_endpoints(TARGET, sbom=None)

    assert result is None
    assert route.call_count == 1


@pytest.mark.anyio
@respx.mock
async def test_403_on_first_shape_moves_to_next_path() -> None:
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(f"{TARGET}/chat").mock(return_value=httpx.Response(403))
    respx.post(f"{TARGET}/run").mock(
        return_value=httpx.Response(200, json={"response": "hi"})
    )
    respx.post(url__regex=r".*").mock(return_value=httpx.Response(404))

    result = await probe_chat_endpoints(TARGET, sbom=None)

    assert result is not None
    assert result.path == "/run"


@pytest.mark.anyio
@respx.mock
async def test_400_on_first_shape_still_tries_remaining_shapes() -> None:
    # Regression guard: 400/422 must keep sweeping all payload-key shapes on
    # the same path — this is the actual key-discovery mechanism and must not
    # be blunted by the 401/403/429 early-exit fix.
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    route = respx.post(f"{TARGET}/chat").mock(
        side_effect=[
            httpx.Response(400),  # "message" rejected
            httpx.Response(200, json={"response": "hi"}),  # "phrases" accepted
        ]
    )
    respx.post(url__regex=r".*").mock(return_value=httpx.Response(404))

    result = await probe_chat_endpoints(TARGET, sbom=None)

    assert result is not None
    assert result.path == "/chat"
    assert route.call_count == 2


@pytest.mark.anyio
@respx.mock
async def test_429_aborts_entire_probe_instead_of_trying_next_path() -> None:
    # Rate-limiting is a target-wide condition, not per-candidate: continuing
    # to probe the remaining paths would likely hit the same quota again.
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    first_path_route = respx.post(f"{TARGET}/chat").mock(return_value=httpx.Response(429))
    second_path_route = respx.post(f"{TARGET}/run").mock(
        return_value=httpx.Response(200, json={"response": "hi"})
    )

    with pytest.raises(TargetRateLimitedError) as excinfo:
        await probe_chat_endpoints(TARGET, sbom=None)

    assert first_path_route.call_count == 1
    assert second_path_route.call_count == 0  # never reached — probe aborted first
    assert excinfo.value.retry_after is None


@pytest.mark.anyio
@respx.mock
async def test_429_with_retry_after_header_is_parsed() -> None:
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(f"{TARGET}/chat").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "30"})
    )

    with pytest.raises(TargetRateLimitedError) as excinfo:
        await probe_chat_endpoints(TARGET, sbom=None)

    assert excinfo.value.retry_after == 30.0
