"""Deterministic evaluation of direct-HTTP rate-limit probes (issue #766).

A bare HTTP 2xx after a short burst does not show that an application's rate
limit is missing: the burst may be inside the application's own policy. This
module turns recorded observations plus an explicit expected policy into one of
three outcomes: ``defended``, ``finding`` or ``inconclusive`` (with a reason).
It is pure (no network, no LLM), so it can be tested offline.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from nuguard.models.exploit_chain import ScenarioType

Verdict = Literal["defended", "finding", "inconclusive"]

PROBE_TITLE_PREFIX = "Rate-Limit Probe"

# Scanner safety budget: how many requests NuGuard itself will send in one probe
# burst. It bounds the scanner and is independent of any application policy.
DEFAULT_PROBE_REQUESTS = 5
SCANNER_MAX_PROBE_REQUESTS = 10


@dataclass(frozen=True)
class RateLimitPolicy:
    """Expected application policy: at most *max_requests* per *window_seconds*."""

    max_requests: int
    window_seconds: float


@dataclass(frozen=True)
class RateLimitObservation:
    """One recorded response of a probe burst."""

    status_code: int | None
    sent_at: float | None = None  # monotonic seconds when the request was sent
    retries_429: int = 0  # 429 responses retried before this response
    headers: dict[str, str] = field(default_factory=dict)  # rate-limit headers only


@dataclass(frozen=True)
class RateLimitEvaluation:
    verdict: Verdict
    reason: str
    detail: str

    @property
    def chain_status(self) -> str:
        """Status string for ScenarioRecord.chain_status."""
        if self.verdict == "inconclusive":
            return f"inconclusive:{self.reason}"
        return "completed"


def is_rate_limit_probe(scenario: Any) -> bool:
    """True for scenarios produced by ``build_rate_limit_probe``."""
    chain = getattr(scenario, "chain", None)
    if chain is None or not chain.steps:
        return False
    return (
        chain.scenario_type == ScenarioType.RESOURCE_EXHAUSTION
        and str(getattr(scenario, "title", "")).startswith(PROBE_TITLE_PREFIX)
        and all(step.target_path for step in chain.steps)
    )


def _inc(reason: str, detail: str) -> RateLimitEvaluation:
    return RateLimitEvaluation("inconclusive", reason, detail)


def _span(obs: Sequence[RateLimitObservation]) -> float | None:
    times = [o.sent_at for o in obs if o.sent_at is not None]
    if not obs or len(times) != len(obs):
        return None
    return max(times) - min(times)


def _summary(obs: Sequence[RateLimitObservation]) -> str:
    base = next((o.sent_at for o in obs if o.sent_at is not None), None)
    parts: list[str] = []
    for i, o in enumerate(obs, start=1):
        at = ""
        if o.sent_at is not None and base is not None:
            at = f" @+{o.sent_at - base:.2f}s"
        retried = f" (after {o.retries_429}x429 retried)" if o.retries_429 else ""
        parts.append(f"{i}:{o.status_code}{at}{retried}")
    text = ", ".join(parts)
    headers = next((o.headers for o in obs if o.headers), {})
    if headers:
        text += f" | rate-limit headers: {headers}"
    return text


def evaluate_rate_limit(
    observations: Sequence[RateLimitObservation],
    policy: RateLimitPolicy | None,
) -> RateLimitEvaluation:
    """Judge a burst against the expected policy.

    ``finding`` only when more than ``policy.max_requests`` requests were
    accepted inside ``policy.window_seconds`` without enforcement. Anything
    that cannot establish that is ``defended`` (enforcement seen at or below
    the policy limit) or ``inconclusive`` with a reason.
    """
    if policy is None:
        return _inc(
            "no_rate_limit_policy",
            "No expected rate-limit policy is configured, so a burst of successful "
            "responses cannot show a violation.",
        )
    if policy.max_requests < 1 or policy.window_seconds <= 0:
        return _inc("invalid_policy", "Policy needs max_requests >= 1 and window_seconds > 0.")
    obs = list(observations)
    if not obs:
        return _inc("no_observations", "No responses were recorded.")
    evidence = _summary(obs)

    for o in obs:
        s = o.status_code
        if s is None or s == 0:
            return _inc("transport_error", f"Request failed before a response. {evidence}")
        if s == 429 or 200 <= s < 300:
            continue
        if s in (401, 403):
            return _inc("auth_rejected", f"Probe was rejected as unauthenticated. {evidence}")
        if s == 404:
            return _inc("endpoint_not_found", f"Endpoint not found. {evidence}")
        if s >= 500:
            return _inc("server_error", f"Server error during the burst. {evidence}")
        return _inc("unexpected_status", f"Status {s} says nothing about rate limiting. {evidence}")

    first = next(
        (i for i, o in enumerate(obs) if o.status_code == 429 or o.retries_429 > 0), None
    )
    if first is not None:
        if first <= policy.max_requests:
            return RateLimitEvaluation(
                "defended",
                "enforcement_observed",
                f"Rate limiting engaged after {first} accepted request(s); policy allows "
                f"{policy.max_requests}. {evidence}",
            )
        window = _span(obs[: first + 1])
        if window is None:
            return _inc("no_timing", f"No send timestamps recorded. {evidence}")
        if window > policy.window_seconds:
            return _inc(
                "window_not_satisfied",
                f"Burst took {window:.1f}s, longer than the {policy.window_seconds:g}s window. "
                f"{evidence}",
            )
        return RateLimitEvaluation(
            "finding",
            "limit_not_enforced_at_policy",
            f"{first} requests were accepted in {window:.1f}s before the first 429; policy "
            f"allows {policy.max_requests} per {policy.window_seconds:g}s. {evidence}",
        )

    if len(obs) <= policy.max_requests:
        if policy.max_requests >= SCANNER_MAX_PROBE_REQUESTS:
            return _inc(
                "policy_exceeds_scanner_budget",
                f"The policy allows {policy.max_requests} requests but the scanner sends at "
                f"most {SCANNER_MAX_PROBE_REQUESTS} per probe, so a violation cannot be "
                f"shown. {evidence}",
            )
        return _inc(
            "insufficient_observations",
            f"{len(obs)} request(s) sent; the policy allows {policy.max_requests}, so this "
            f"burst cannot exceed it. {evidence}",
        )
    window = _span(obs)
    if window is None:
        return _inc("no_timing", f"No send timestamps recorded. {evidence}")
    if window > policy.window_seconds:
        return _inc(
            "window_not_satisfied",
            f"Burst took {window:.1f}s, longer than the {policy.window_seconds:g}s window. "
            f"{evidence}",
        )
    return RateLimitEvaluation(
        "finding",
        "policy_exceeded_without_enforcement",
        f"{len(obs)} requests were accepted in {window:.1f}s with no 429; policy allows "
        f"{policy.max_requests} per {policy.window_seconds:g}s. {evidence}",
    )


def observations_from_step_results(step_results: Sequence[Any]) -> list[RateLimitObservation]:
    """Build observations from executed steps (StepResult, duck-typed)."""
    out: list[RateLimitObservation] = []
    for sr in step_results:
        raw = getattr(sr, "http_observation", None) or {}
        out.append(
            RateLimitObservation(
                status_code=getattr(sr, "http_status_code", None),
                sent_at=raw.get("sent_at"),
                retries_429=int(raw.get("retries_429") or 0),
                headers=dict(raw.get("headers") or {}),
            )
        )
    return out


def probe_burst_count(max_requests: int | None) -> int:
    """Requests per probe: just over the policy limit, capped by the scanner budget."""
    if max_requests is None:
        return DEFAULT_PROBE_REQUESTS
    return max(2, min(max_requests + 1, SCANNER_MAX_PROBE_REQUESTS))
