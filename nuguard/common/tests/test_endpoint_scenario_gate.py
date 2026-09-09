"""Unit tests for nuguard/common/endpoint_scenario_gate.py."""
from __future__ import annotations

from nuguard.common.endpoint_scenario_gate import should_skip_direct_http_scenario
from nuguard.sbom.models import NodeMetadata


def test_confirmed_dead_endpoint_skipped() -> None:
    meta = NodeMetadata(endpoint="/api/transfer", operational=False)
    skip, reason = should_skip_direct_http_scenario(meta)
    assert skip is True
    assert reason is not None and "non-operational" in reason


def test_structurally_invalid_endpoint_skipped() -> None:
    meta = NodeMetadata(endpoint="0.0.0.0:8080 (sse)")
    skip, reason = should_skip_direct_http_scenario(meta)
    assert skip is True
    assert reason is not None and "not an HTTP path" in reason


def test_operational_unknown_not_skipped() -> None:
    meta = NodeMetadata(endpoint="/api/transfer", operational=None)
    skip, reason = should_skip_direct_http_scenario(meta)
    assert skip is False
    assert reason is None


def test_operational_true_not_skipped() -> None:
    meta = NodeMetadata(endpoint="/api/transfer", operational=True)
    skip, reason = should_skip_direct_http_scenario(meta)
    assert skip is False
    assert reason is None
