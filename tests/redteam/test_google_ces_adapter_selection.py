"""Tests for CES adapter selection (issue #552).

Covers:
- :func:`~nuguard.redteam.target.framework_adapters.factory._is_ces_target_url`
- :func:`~nuguard.redteam.target.framework_adapters.factory._make_ces_adapter`
- :func:`~nuguard.redteam.target.framework_adapters.factory.make_framework_adapter`
  CES-vs-target_url gating specifically

Root cause under test: a proxy application (e.g. Blissful Store) whose SBOM
reports it uses Google CES internally must never have its own traffic
redirected to ``ces.googleapis.com`` — CES evidence in the SBOM is
informational only. CES transport is selected only when the *target_url*
NuGuard was actually told to test is itself a CES API URL.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from nuguard.redteam.target.framework_adapters.factory import (
    _is_ces_target_url,
    make_framework_adapter,
)
from nuguard.redteam.target.framework_adapters.google_adk import GoogleADKAdapter
from nuguard.redteam.target.framework_adapters.google_ces import GoogleCESAdapter


def _make_sbom(
    frameworks: list[str] | None = None,
    ces_endpoint: str | None = None,
) -> MagicMock:
    """Build a minimal mock AI-SBOM, optionally with a CES API_ENDPOINT node."""
    summary = MagicMock()
    summary.frameworks = frameworks or []
    sbom = MagicMock()
    sbom.summary = summary
    sbom.nodes = []

    if ces_endpoint:
        node = MagicMock()
        node.component_type.value = "API_ENDPOINT"
        node.metadata.framework = "google-ces"
        node.metadata.endpoint = ces_endpoint
        node.evidence = []
        sbom.nodes = [node]

    return sbom


_REAL_CES_ENDPOINT = (
    "https://ces.googleapis.com/v1beta/projects/my-proj/locations/us/apps/my-app/sessions/x"
)


# ─── _is_ces_target_url ────────────────────────────────────────────────────


def test_is_ces_target_url_true_for_exact_host() -> None:
    assert _is_ces_target_url("https://ces.googleapis.com/v1beta/projects/p") is True


def test_is_ces_target_url_true_with_http_scheme() -> None:
    assert _is_ces_target_url("http://ces.googleapis.com") is True


def test_is_ces_target_url_true_case_insensitive() -> None:
    assert _is_ces_target_url("https://CES.GOOGLEAPIS.COM/v1beta") is True


def test_is_ces_target_url_false_for_proxy_host() -> None:
    assert _is_ces_target_url("http://localhost:8081") is False


def test_is_ces_target_url_false_for_lookalike_subdomain() -> None:
    # A subdomain/prefix trick must not match — hostname must equal
    # ces.googleapis.com exactly, not merely contain it.
    assert _is_ces_target_url("https://ces.googleapis.com.evil.test") is False


def test_is_ces_target_url_false_for_empty_string() -> None:
    assert _is_ces_target_url("") is False


def test_is_ces_target_url_false_for_malformed_url() -> None:
    assert _is_ces_target_url("not a url at all :::") is False


# ─── make_framework_adapter — the actual #552 regression ──────────────────


def test_proxy_target_with_ces_sbom_evidence_does_not_select_ces() -> None:
    """The core #552 bug: a proxy app's SBOM correctly reports internal CES
    usage, but the supplied target_url is the proxy itself — CES must not
    be selected."""
    sbom = _make_sbom(frameworks=["google-ces"], ces_endpoint=_REAL_CES_ENDPOINT)
    adapter = make_framework_adapter(sbom, target_url="http://localhost:8081")
    assert adapter is None


def test_proxy_target_with_ces_and_adk_sbom_evidence_falls_through_to_adk() -> None:
    """Blissful Store's real SBOM lists both google_adk and google-ces.
    Once CES is correctly excluded on target_url grounds, ADK detection
    still runs on its own (separately gated) merits."""
    sbom = _make_sbom(frameworks=["google-ces", "google_adk"], ces_endpoint=_REAL_CES_ENDPOINT)
    adapter = make_framework_adapter(sbom, target_url="http://localhost:8081")
    assert isinstance(adapter, GoogleADKAdapter)
    # SBOM-only trust (no explicit adk.enabled=true) — must defer to live
    # verification, not be trusted immediately either.
    assert adapter._requires_verification is True


def test_target_url_is_ces_selects_ces_adapter() -> None:
    sbom = _make_sbom(frameworks=["google-ces"], ces_endpoint=_REAL_CES_ENDPOINT)
    adapter = make_framework_adapter(
        sbom, target_url="https://ces.googleapis.com/v1beta/projects/my-proj"
    )
    assert isinstance(adapter, GoogleCESAdapter)
    assert adapter.ces_config.project == "my-proj"


def test_target_url_is_ces_selects_adapter_even_without_sbom_evidence() -> None:
    """The caller pointing target_url directly at CES is itself sufficient —
    matches issue #552's 'or when the caller explicitly requests CES mode'."""
    sbom = _make_sbom(frameworks=[])
    adapter = make_framework_adapter(
        sbom, target_url="https://ces.googleapis.com/v1beta/projects/other"
    )
    assert isinstance(adapter, GoogleCESAdapter)


def test_no_sbom_evidence_and_proxy_target_selects_nothing() -> None:
    sbom = _make_sbom(frameworks=["langchain"])
    adapter = make_framework_adapter(sbom, target_url="http://localhost:8081")
    assert adapter is None


def test_missing_target_url_never_selects_ces() -> None:
    """target_url defaults to "" — must behave like a non-CES URL, not raise."""
    sbom = _make_sbom(frameworks=["google-ces"], ces_endpoint=_REAL_CES_ENDPOINT)
    adapter = make_framework_adapter(sbom)
    assert adapter is None
