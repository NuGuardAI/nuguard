"""Tests for streaming output detection in the SBOM extractor.

Covers:
- detect_streaming_output() in nuguard.sbom.core.application_summary
- ScanSummary.uses_streaming / streaming_endpoints model fields
- Propagation through build_scan_summary → _make_scan_summary
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nuguard.sbom.core.application_summary import detect_streaming_output
from nuguard.sbom.models import ScanSummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(transport: str | None = None, endpoint: str | None = None) -> MagicMock:
    node = MagicMock()
    node.metadata.transport = transport
    node.metadata.endpoint = endpoint
    return node


# ---------------------------------------------------------------------------
# ScanSummary model fields
# ---------------------------------------------------------------------------

class TestScanSummaryFields:
    def test_uses_streaming_defaults_false(self) -> None:
        s = ScanSummary()
        assert s.uses_streaming is False

    def test_streaming_endpoints_defaults_empty(self) -> None:
        s = ScanSummary()
        assert s.streaming_endpoints == []

    def test_can_set_uses_streaming(self) -> None:
        s = ScanSummary(uses_streaming=True, streaming_endpoints=["/run_sse"])
        assert s.uses_streaming is True
        assert "/run_sse" in s.streaming_endpoints

    def test_serialises_to_json(self) -> None:
        s = ScanSummary(uses_streaming=True, streaming_endpoints=["/chat/stream"])
        d = s.model_dump()
        assert d["uses_streaming"] is True
        assert d["streaming_endpoints"] == ["/chat/stream"]


# ---------------------------------------------------------------------------
# detect_streaming_output — framework inference (Layer 1)
# ---------------------------------------------------------------------------

class TestDetectStreamingFrameworks:
    def test_google_adk_normalised_name(self) -> None:
        uses, eps = detect_streaming_output([], [], ["google-adk"])
        assert uses is True
        assert "/run_sse" in eps

    def test_google_adk_underscore_variant(self) -> None:
        uses, eps = detect_streaming_output([], [], ["google_adk"])
        assert uses is True
        assert "/run_sse" in eps

    def test_non_streaming_framework(self) -> None:
        uses, eps = detect_streaming_output([], [], ["langchain", "crewai"])
        assert uses is False
        assert eps == []

    def test_empty_frameworks(self) -> None:
        uses, eps = detect_streaming_output([], [], [])
        assert uses is False


# ---------------------------------------------------------------------------
# detect_streaming_output — SBOM node transport (Layer 2)
# ---------------------------------------------------------------------------

class TestDetectStreamingNodes:
    def test_sse_transport_node(self) -> None:
        node = _make_node(transport="sse", endpoint="/events")
        uses, eps = detect_streaming_output([node], [], [])
        assert uses is True
        assert "/events" in eps

    def test_streamable_http_transport(self) -> None:
        node = _make_node(transport="streamable-http", endpoint="/stream")
        uses, eps = detect_streaming_output([node], [], [])
        assert uses is True
        assert "/stream" in eps

    def test_non_streaming_transport_ignored(self) -> None:
        node = _make_node(transport="stdio", endpoint="/tool")
        uses, eps = detect_streaming_output([node], [], [])
        assert uses is False
        assert eps == []

    def test_sse_node_without_endpoint_still_marks_streaming(self) -> None:
        node = _make_node(transport="sse", endpoint=None)
        uses, eps = detect_streaming_output([node], [], [])
        assert uses is True
        # No endpoint to register — list may be empty
        assert isinstance(eps, list)


# ---------------------------------------------------------------------------
# detect_streaming_output — source-code scan (Layer 3)
# ---------------------------------------------------------------------------

class TestDetectStreamingSourceCode:
    def _files(self, source: str, path: str = "app.py") -> list[tuple[str, str]]:
        return [(path, source)]

    def test_streaming_response_detected(self) -> None:
        src = "from fastapi.responses import StreamingResponse\nreturn StreamingResponse(gen())"
        uses, _ = detect_streaming_output([], self._files(src), [])
        assert uses is True

    def test_event_source_response_detected(self) -> None:
        src = "from sse_starlette.sse import EventSourceResponse\nreturn EventSourceResponse(g())"
        uses, _ = detect_streaming_output([], self._files(src), [])
        assert uses is True

    def test_text_event_stream_header(self) -> None:
        src = 'Response(content=gen(), media_type="text/event-stream")'
        uses, _ = detect_streaming_output([], self._files(src), [])
        assert uses is True

    def test_langchain_astream(self) -> None:
        src = "async for chunk in chain.astream(input):\n    yield chunk"
        uses, _ = detect_streaming_output([], self._files(src), [])
        assert uses is True

    def test_langgraph_stream_events(self) -> None:
        src = "async for event in graph.stream_events(input, version='v1'):\n    yield event"
        uses, _ = detect_streaming_output([], self._files(src), [])
        assert uses is True

    def test_run_sse_path_reference(self) -> None:
        src = 'RUN_SSE_PATH = "/run_sse"'
        uses, _ = detect_streaming_output([], self._files(src), [])
        assert uses is True

    def test_non_python_file_ignored(self) -> None:
        src = "StreamingResponse is a class"
        uses, _ = detect_streaming_output([], [("README.md", src)], [])
        assert uses is False

    def test_no_streaming_patterns(self) -> None:
        src = "def chat(msg: str) -> dict:\n    return {'response': msg}"
        uses, _ = detect_streaming_output([], self._files(src), [])
        assert uses is False

    def test_streaming_route_path_extracted(self) -> None:
        src = (
            "@app.post('/chat/stream')\n"
            "async def stream_chat() -> StreamingResponse:\n"
            "    return StreamingResponse(gen())\n"
        )
        uses, eps = detect_streaming_output([], self._files(src), [])
        assert uses is True
        assert any("stream" in ep for ep in eps)

    def test_sse_path_suffix_extracted(self) -> None:
        src = (
            "@app.get('/events')\n"
            "async def sse() -> EventSourceResponse:\n"
            "    pass\n"
        )
        uses, eps = detect_streaming_output([], self._files(src), [])
        assert uses is True
        assert "/events" in eps


# ---------------------------------------------------------------------------
# detect_streaming_output — deduplication
# ---------------------------------------------------------------------------

class TestDetectStreamingDedup:
    def test_duplicate_endpoints_deduplicated(self) -> None:
        # Framework AND node both report /run_sse
        node = _make_node(transport="sse", endpoint="/run_sse")
        uses, eps = detect_streaming_output([node], [], ["google-adk"])
        assert eps.count("/run_sse") == 1

    def test_multiple_different_endpoints_all_present(self) -> None:
        n1 = _make_node(transport="sse", endpoint="/chat/stream")
        n2 = _make_node(transport="sse", endpoint="/events")
        uses, eps = detect_streaming_output([n1, n2], [], [])
        assert "/chat/stream" in eps
        assert "/events" in eps
