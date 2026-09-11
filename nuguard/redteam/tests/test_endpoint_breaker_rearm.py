"""Regression tests: the direct-HTTP endpoint circuit breaker must re-arm.

``RedteamOrchestrator._run_scenarios`` tracks direct-HTTP endpoint-probe
outages (``TargetUnavailableError`` with ``source="endpoint_probe"`` — raised
when ``TargetAppClient.invoke_endpoint`` hits its own consecutive-error
threshold) with a dedicated strike counter and abort event, so an unreachable
SBOM-derived REST path cannot abort unrelated chat-routed scenarios (isolated
in #013a62f8).

Regression: that isolated endpoint breaker was latch-only.  Once 3 consecutive
probe failures tripped it, the orchestrator latched ``_endpoint_circuit_open``
forever and never reset the counter on a recovered response — so a
~3-connection blip (container cold start, LB re-point, transient DNS failure)
silently dropped every remaining API_ATTACK / direct-HTTP scenario for the
whole run, all reported ``chain_status="target_unreachable"`` / ``0/0`` turns,
even after the target recovered.  The chat breaker, by contrast, resets its
counter on success (``consecutive_unavailable = 0``).

The fix bounds the isolated breaker to the pass that tripped it: a fresh
counter + abort event per ``_run_scenarios`` call, no sticky cross-pass latch.
Within-pass behaviour is unchanged — once the breaker trips, the remaining
not-yet-dispatched direct-HTTP-only scenarios in THAT pass are still skipped.
"""
from __future__ import annotations

from typing import Any, cast

import pytest

from nuguard.common.errors import TargetUnavailableError
from nuguard.models.exploit_chain import (
    HTTP_2XX_SENTINEL,
    ExploitChain,
    ExploitStep,
    GoalType,
    ScenarioType,
)
from nuguard.redteam.executor.executor import AttackExecutor
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.session import AttackSession
from nuguard.sbom.models import AiSbomDocument


class _FlakyEndpointClient:
    """Minimal ``TargetAppClient`` stand-in with a recovering endpoint probe.

    ``invoke_endpoint`` mirrors the real client's contract:

    * a connection-level failure below the threshold is returned as
      ``(0, "[REQUEST_ERROR: ...]", {})`` — the step just fails;
    * once ``_consecutive_endpoint_errors`` crosses ``threshold`` it raises
      ``TargetUnavailableError(source="endpoint_probe")``;
    * a successful response resets its own counter (the real client does this
      in ``_record_endpoint_success``).

    ``fail_count`` is the number of consecutive probe failures before the
    probe starts succeeding again.  With ``recover=False`` every probe fails,
    but only the ones that cross the raise threshold raise — exactly like the
    real client.
    """

    def __init__(
        self, threshold: int = 3, fail_count: int = 3, recover: bool = True
    ) -> None:
        self.threshold = threshold
        self.fail_count = fail_count
        self.recover = recover
        self._consecutive_endpoint_errors = 0
        self.probe_attempts = 0
        self._request_sem = None

    def new_session(self, chain_id: str) -> AttackSession:
        return AttackSession(session_id="s1", target_url="http://target", chain_id=chain_id)

    def update_default_headers(self, headers: dict[str, str] | None) -> None:
        pass

    def reset_circuit_breaker(self) -> None:
        self._consecutive_endpoint_errors = 0

    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]:
        self.probe_attempts += 1
        if self.probe_attempts > self.fail_count:
            # Outside the failure window — the endpoint recovered: every probe
            # succeeds and (mirroring _record_endpoint_success) resets the
            # consecutive-error counter.
            self._consecutive_endpoint_errors = 0
            return 200, '{"ok": true}', {"ok": True}
        # Inside the failure window: exactly the real client's contract —
        # failures accumulate and only the probe that crosses the threshold
        # raises TargetUnavailableError(source="endpoint_probe"); the ones
        # below it just fail the request.
        self._consecutive_endpoint_errors += 1
        if self._consecutive_endpoint_errors >= self.threshold:
            raise TargetUnavailableError(
                f"Direct-HTTP endpoint probes returned {self._consecutive_endpoint_errors} "
                f"consecutive connection-level failures (last: {path}) — aborting "
                f"direct-HTTP probing to avoid hammering unreachable paths.",
                source="endpoint_probe",
            )
        return 0, f"[REQUEST_ERROR: ConnectError ({self._consecutive_endpoint_errors})]", {}


def _direct_http_chain(chain_id: str) -> ExploitChain:
    """A static API_ATTACK-style chain whose only step goes through invoke_endpoint."""
    return ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.AUTH_BYPASS,
        steps=[
            ExploitStep(
                step_id="s1",
                step_type="INVOKE",
                description="auth-bypass probe",
                payload="{}",
                target_path="/api/admin",
                http_method="GET",
                success_signal=HTTP_2XX_SENTINEL,
                on_failure="abort",
            )
        ],
    )


def _direct_scenario(chain: ExploitChain) -> AttackScenario:
    return AttackScenario(
        scenario_id=chain.chain_id,
        goal_type=chain.goal_type,
        scenario_type=chain.scenario_type,
        title=f"scenario {chain.chain_id}",
        description="direct-HTTP scenario",
        target_node_ids=["node-1"],
        chain=chain,
    )


def _orchestrator() -> RedteamOrchestrator:
    sbom = AiSbomDocument(target="unit-test", nodes=[], edges=[])
    return RedteamOrchestrator(
        sbom=sbom,
        target_url="http://target",
        concurrency=1,
        # Identical dead-target payloads would otherwise be suppressed as
        # "similar misses" — irrelevant to the circuit-breaker behaviour under
        # test, so disable the similarity tracker with a huge threshold.
        similar_miss_threshold=10_000,
    )


# ── Within-pass: trip skips the remaining direct-HTTP scenarios ──────────────


@pytest.mark.asyncio
async def test_endpoint_breaker_skips_remaining_within_pass() -> None:
    """Established behaviour (unchanged by the fix) still holds.

    Probes 1-2 fail without raising (below the client threshold) and the
    third consecutive probe failure raises ``TargetUnavailableError``
    (``source="endpoint_probe"``) which trips the isolated breaker — the trip
    must not leak into the chat breaker (nothing may be ``skipped`` by the
    general ``abort_event``) and the rest of the pass must not re-hit the
    failing window endlessly.  Exact per-scenario statuses beyond the trip are
    racy (a scenario may race through the semaphore before the trip event is
    set), so only the invariants are asserted.
    """
    client = _FlakyEndpointClient(threshold=3, fail_count=3, recover=True)
    executor = AttackExecutor(client=cast(Any, client))
    orch = _orchestrator()
    scenarios = [_direct_scenario(_direct_http_chain(f"c{i}")) for i in range(1, 8)]

    findings, executed, records = await orch._run_scenarios(scenarios, executor, None)

    statuses = [r.chain_status for r in records]
    # c1-c2: probes below threshold → normal (non-TUE) request failures.
    assert statuses[:2] == ["aborted", "aborted"], statuses
    # The trip happened on a probe that raised TUE, marked target_unreachable.
    assert "target_unreachable" in statuses[2:], statuses
    # The isolated breaker must NOT leak into the chat breaker — nothing may
    # be skipped by the general abort_event (which would report "skipped").
    assert "skipped" not in statuses, statuses
    assert len(executed) == 7
    assert not findings
    # At least the 3 trip probes were attempted; scenarios that race past the
    # trip point probe the (recovered) endpoint, so no exact upper bound.
    assert client.probe_attempts >= 3, client.probe_attempts


# ── Cross-pass: a trip in the main pass re-arms for the escalation pass ──────


@pytest.mark.asyncio
async def test_endpoint_breaker_rearms_across_passes_after_recovery() -> None:
    """A trip in the main pass does not permanently disable the escalation pass.

    Regression: the isolated endpoint breaker latched ``_endpoint_circuit_open``
    across passes, so after 3 consecutive probe failures tripped it in the
    main pass, the escalation pass (a second ``_run_scenarios`` call) skipped
    every direct-HTTP scenario up front — ``chain_status="target_unreachable"``
    with ZERO probe requests — even after the endpoint recovered.  With the
    fix, the breaker is scoped to the pass that tripped it: the escalation
    pass gets a fresh counter + abort event and probes again.
    """
    client = _FlakyEndpointClient(threshold=3, fail_count=5, recover=True)
    executor = AttackExecutor(client=cast(Any, client))
    orch = _orchestrator()
    scenarios = [_direct_scenario(_direct_http_chain(f"c{i}")) for i in range(1, 8)]

    # Main pass: the endpoint is inside its failure window; the trip happens
    # early and the tail of the pass is skipped (target_unreachable), with
    # only ~3-4 probes attempted.
    _f1, _e1, records1 = await orch._run_scenarios(scenarios, executor, None)
    assert "target_unreachable" in [r.chain_status for r in records1]
    probes_after_pass1 = client.probe_attempts
    assert probes_after_pass1 >= 3, probes_after_pass1

    # Escalation pass: the endpoint has RECOVERED.  Pre-fix every scenario was
    # skipped up front by the latched _endpoint_circuit_open (0 probe
    # requests); post-fix the breaker re-arms and the pass re-probes the
    # endpoint — every scenario invokes invoke_endpoint again.
    _f2, _e2, records2 = await orch._run_scenarios(scenarios, executor, None)
    assert client.probe_attempts == probes_after_pass1 + len(scenarios), (
        client.probe_attempts, probes_after_pass1
    )
    # The recovered probes succeed (the pass is no longer dominated by
    # target_unreachable skips — at most the racy trip helper can be one).
    statuses2 = [r.chain_status for r in records2]
    assert statuses2.count("target_unreachable") <= 1, statuses2
    assert "skipped" not in statuses2, statuses2


@pytest.mark.asyncio
async def test_endpoint_breaker_rearms_across_passes_while_down() -> None:
    """A trip re-arms across passes even if the endpoint stays down.

    A genuinely dead endpoint is still bounded: the escalation pass gets a
    fresh counter and re-probes (at most ``_ABORT_THRESHOLD`` failures) before
    tripping again and skipping the rest of that pass — it does not hammer the
    dead endpoint endlessly, and it does not silently skip with a stale latch.
    """
    client = _FlakyEndpointClient(threshold=3, fail_count=100, recover=False)
    executor = AttackExecutor(client=cast(Any, client))
    orch = _orchestrator()
    scenarios = [_direct_scenario(_direct_http_chain(f"c{i}")) for i in range(1, 5)]

    # Pass 1: c1-c2 aborted (below threshold), c3 trips, the remainder of the
    # pass is skipped as target_unreachable.
    _f1, _e1, records1 = await orch._run_scenarios(scenarios, executor, None)
    statuses1 = [r.chain_status for r in records1]
    assert statuses1 == ["aborted", "aborted", "target_unreachable", "target_unreachable"], statuses1
    probes_after_pass1 = client.probe_attempts
    assert probes_after_pass1 >= 3, probes_after_pass1

    # Pass 2: still down but NOT latched — a fresh counter is budgeted, so
    # the probes are re-attempted (and, since every probe still raises at the
    # client level, they trip the fresh pass-level breaker again); the tail is
    # skipped for THIS pass, and the dead endpoint was re-probed (not
    # zero-probe skipped by a stale cross-pass latch).
    _f2, _e2, records2 = await orch._run_scenarios(scenarios, executor, None)
    statuses2 = [r.chain_status for r in records2]
    assert statuses2 == ["target_unreachable"] * 4, statuses2
    # The re-probe happened: probes resumed (fresh budget), bounded to the
    # 3-failure trip plus the racer, rather than staying at pass-1's count.
    assert client.probe_attempts > probes_after_pass1, client.probe_attempts