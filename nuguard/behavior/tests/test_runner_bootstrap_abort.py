"""Regression tests for issue #532: BehaviorRunner must abort before any
scenario runs when the default credential's bootstrap health check reports
`auth_failed` or `endpoint_not_found` — reliably, regardless of which public
method (discover(), probe_tool_families(), run()) happens to trigger the
shared target-session resolution first.

BehaviorRunner has four entry points sharing one lazily-resolved client
(_build_client), unlike RedteamOrchestrator's single linear run(): discover()
and probe_tool_families() deliberately treat a _build_client() failure as
non-fatal (their own documented contracts), while run() must not. The fix
checks the cached health report on every _build_client() call rather than
once, so run() reliably aborts regardless of what an earlier, swallowing
caller like discover() already did with the same failure.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nuguard.behavior.runner import BehaviorRunner
from nuguard.behavior.tests.test_runner import (
    _make_config,
    _make_intent,
    _make_mock_policy,
    _make_mock_sbom,
)
from nuguard.common.errors import AuthError, TargetEndpointNotFoundError
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport


def _make_runner() -> BehaviorRunner:
    return BehaviorRunner(
        config=_make_config(),
        sbom=_make_mock_sbom(),
        policy=_make_mock_policy(),
        intent=_make_intent(),
        llm_client=None,
    )


def _fake_session_cfg():
    from nuguard.common.session_resolver import TargetSessionConfig

    class _FakeAuthSession:
        def headers(self) -> dict[str, str]:
            return {}

    return TargetSessionConfig(
        base_url="http://localhost:8080",
        chat_path="/chat",
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras={},
        chat_response_key=None,
        auth_session=_FakeAuthSession(),
        resolution_notes=[],
    )


def _mock_resolve_target_session(monkeypatch: pytest.MonkeyPatch, status: str, http_status: int) -> None:
    async def _fake(**kwargs):
        _ = kwargs
        report = TargetHealthReport(
            target_url="http://localhost:8080",
            endpoint="/chat",
            run_id="r-abort-test",
            checks=[
                CredentialCheckResult(
                    identity="default",
                    auth_type="none",
                    endpoint="http://localhost:8080/chat",
                    status=status,  # type: ignore[arg-type]
                    http_status_code=http_status,
                    error_detail=f"HTTP {http_status}",
                )
            ],
        )
        return _fake_session_cfg(), report

    monkeypatch.setattr("nuguard.common.session_resolver.resolve_target_session", _fake)


@pytest.mark.asyncio
async def test_run_aborts_on_auth_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_resolve_target_session(monkeypatch, "auth_failed", 401)
    runner = _make_runner()
    with pytest.raises(AuthError):
        await runner.run(scenarios=[])


@pytest.mark.asyncio
async def test_run_aborts_on_endpoint_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_resolve_target_session(monkeypatch, "endpoint_not_found", 404)
    runner = _make_runner()
    with pytest.raises(TargetEndpointNotFoundError) as excinfo:
        await runner.run(scenarios=[])
    assert excinfo.value.http_status_code == 404


@pytest.mark.asyncio
async def test_discover_swallows_auth_failed_as_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    # discover()'s documented contract ("Returns None on any failure, non-fatal")
    # must be unchanged by this fix — it swallows the new raise exactly as it
    # already swallows any other _build_client() failure.
    _mock_resolve_target_session(monkeypatch, "auth_failed", 401)
    runner = _make_runner()
    profile = await runner.discover()
    assert profile is None


@pytest.mark.asyncio
async def test_discover_swallows_endpoint_not_found_as_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_resolve_target_session(monkeypatch, "endpoint_not_found", 404)
    runner = _make_runner()
    profile = await runner.discover()
    assert profile is None


@pytest.mark.asyncio
async def test_run_aborts_after_discover_already_swallowed_the_same_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The critical regression case: discover() runs first (the normal
    # BehaviorAnalyzer order) and silently absorbs the failure via its own
    # try/except. run()'s own _build_client() call must still independently
    # re-check the cached health report and abort — not be defeated by the
    # one-shot resolution flag having already been consumed by discover().
    _mock_resolve_target_session(monkeypatch, "endpoint_not_found", 404)
    runner = _make_runner()

    profile = await runner.discover()
    assert profile is None  # discover() swallowed it, as it always has

    with pytest.raises(TargetEndpointNotFoundError):
        await runner.run(scenarios=[])


@pytest.mark.asyncio
async def test_probe_tool_families_swallows_endpoint_not_found_as_non_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_resolve_target_session(monkeypatch, "endpoint_not_found", 404)
    runner = _make_runner()
    # probe_tool_families() returns {} early when there's no SBOM tool data —
    # _make_mock_sbom() is a bare MagicMock, so force it past that early return
    # by giving it no nodes (falls through to the _build_client() call).
    runner._sbom = MagicMock()
    runner._sbom.nodes = []
    result = await runner.probe_tool_families()
    assert result == {}


@pytest.mark.asyncio
async def test_run_unaffected_by_ok_default_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression guard: a healthy default credential must not be affected by
    # the new check — run() with no scenarios completes normally.
    _mock_resolve_target_session(monkeypatch, "ok", 200)
    runner = _make_runner()
    result = await runner.run(scenarios=[])
    assert result.scenario_results == []
