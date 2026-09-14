"""Tests for configuration-aware payload inference."""

from unittest.mock import AsyncMock, patch

import pytest

from nuguard.common.endpoint_detection import UNSET, EndpointSource
from nuguard.common.endpoint_detection.payload import (
    detect_payload_shape,
    payload_shape_from_probe_result,
)
from nuguard.common.endpoint_probe import ProbeResult


@pytest.mark.asyncio
async def test_explicit_payload_values_are_not_replaced_by_probe() -> None:
    with patch(
        "nuguard.common.endpoint_detection.payload.probe_endpoint",
        new=AsyncMock(return_value=ProbeResult("/api/chat", "prompt", True)),
    ) as probe:
        result = await detect_payload_shape(
            "https://target.example",
            None,
            "/api/chat",
            payload_key="message",
            payload_list=False,
            value_template={},
            response_key="answer",
        )

    probe.assert_not_awaited()
    assert result.key == "message"
    assert result.is_list is False
    assert result.value_template == {}
    assert result.response_key == "answer"
    assert result.source is EndpointSource.CONFIG


@pytest.mark.asyncio
async def test_missing_payload_key_is_inferred_for_configured_endpoint() -> None:
    with patch(
        "nuguard.common.endpoint_detection.payload.probe_endpoint",
        new=AsyncMock(return_value=ProbeResult("/other", "prompt", True)),
    ) as probe:
        result = await detect_payload_shape(
            "https://target.example",
            None,
            "/api/chat",
            payload_key=UNSET,
            payload_list=UNSET,
        )

        probe.assert_awaited_once()
        call = probe.await_args
        assert call is not None
        assert call.kwargs["hint_path"] == "/api/chat"
    assert result.key == "prompt"
    assert result.is_list is True
    assert result.source is EndpointSource.PROBE


def test_probe_result_normalization_is_shared_and_preserves_explicit_fields() -> None:
    result = payload_shape_from_probe_result(
        ProbeResult("/api/chat", "prompt", True),
        payload_key="message",
        payload_list=False,
    )

    assert result.key == "message"
    assert result.is_list is False
    assert result.source is EndpointSource.PROBE
    assert result.explicit_key is True
    assert result.explicit_list is True
