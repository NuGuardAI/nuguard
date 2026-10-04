"""Health tracking, cooldown queue and retry budget for campaign mode.

Pure logic with an injectable clock (no I/O, no sleeping), so the scheduler can
reason about transport evidence without ever occupying a request slot.

Health is keyed by (origin, route, method, principal, dependency group) and is
separate from attack verdicts: a refusal or a denied unauthorized request is
*control evidence*, never an outage. A bad API route can only block that route
(and its dependents) — nothing here ever trips a run-wide breaker.
"""
from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Generic, NamedTuple, TypeVar

from nuguard.common.transport import RETRIABLE_OUTCOMES, TransportOutcome

T = TypeVar("T")

_DEFAULT_RATE_LIMIT_DELAY = 30.0
_DEAD_ROUTE_THRESHOLD = 2  # consecutive 404/405/422 on a non-baseline route


class HealthKey(NamedTuple):
    """Identity of a health record."""

    origin: str
    route: str
    method: str
    principal: str
    dep_group: str


class Action(str, Enum):
    """What the scheduler should do next."""

    CONTINUE = "continue"
    RETRY_LATER = "retry_later"            # cooldown + retry within budget
    PAUSE_AND_PROBE = "pause_and_probe"    # pause group, one bounded recovery probe
    RESOLVE_ROUTE = "resolve_route"        # baseline route mismatch: run discovery once
    BLOCK_ROUTE = "block_route"            # route unresolved/dead: block dependents only
    REFRESH_AUTH = "refresh_auth"
    AUTH_BLOCKED = "auth_blocked"
    EFFECT_UNKNOWN = "effect_unknown"      # ambiguous write: never retried
    GIVE_UP = "give_up"                    # retry budget exhausted for this incident


@dataclass(frozen=True)
class Decision:
    """Scheduler instruction derived from one transport outcome."""

    action: Action
    delay_seconds: float = 0.0
    reason: str = ""
    check_chat_baseline_first: bool = False


class RetryBudget:
    """At most *max_retries* per incident within a sliding *window_seconds*."""

    def __init__(self, max_retries: int = 2, window_seconds: float = 120.0) -> None:
        self._max = max_retries
        self._window = window_seconds
        self._events: dict[str, list[float]] = {}

    def try_consume(self, incident: str, now: float) -> bool:
        events = [t for t in self._events.get(incident, []) if now - t < self._window]
        if len(events) >= self._max:
            self._events[incident] = events
            return False
        events.append(now)
        self._events[incident] = events
        return True

    def remaining(self, incident: str, now: float) -> int:
        events = [t for t in self._events.get(incident, []) if now - t < self._window]
        return max(0, self._max - len(events))


class CooldownQueue(Generic[T]):
    """Items waiting for ``next_eligible_at``; never holds a request slot."""

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, T]] = []
        self._counter = itertools.count()

    def push(self, item: T, next_eligible_at: float) -> None:
        heapq.heappush(self._heap, (next_eligible_at, next(self._counter), item))

    def pop_ready(self, now: float) -> list[T]:
        ready: list[T] = []
        while self._heap and self._heap[0][0] <= now:
            ready.append(heapq.heappop(self._heap)[2])
        return ready

    def next_eligible_at(self) -> float | None:
        return self._heap[0][0] if self._heap else None

    def __len__(self) -> int:
        return len(self._heap)


@dataclass
class _GroupState:
    paused_until: float = 0.0
    probe_in_flight: bool = False
    healthy: bool = True


@dataclass
class HealthRegistry:
    """Maps transport evidence to scheduler decisions per dependency group."""

    budget: RetryBudget = field(default_factory=RetryBudget)
    max_cooldown_seconds: float = 120.0
    clock: Callable[[], float] = field(default=lambda: 0.0)
    _groups: dict[str, _GroupState] = field(default_factory=dict)
    _dead_counts: dict[tuple[str, str, str], int] = field(default_factory=dict)
    _blocked_routes: dict[tuple[str, str, str], str] = field(default_factory=dict)
    _resolved_routes: set[tuple[str, str, str]] = field(default_factory=set)
    _auth_blocked: set[str] = field(default_factory=set)

    # -- queries -----------------------------------------------------------
    def is_paused(self, key: HealthKey, now: float | None = None) -> bool:
        t = self.clock() if now is None else now
        return self._groups.get(key.dep_group, _GroupState()).paused_until > t

    def resume_at(self, key: HealthKey) -> float:
        """When the key's dependency group becomes eligible again (0.0 = now)."""
        return self._groups.get(key.dep_group, _GroupState()).paused_until

    def blocked_reason(self, key: HealthKey) -> str | None:
        """Why *key* must not run (route/auth blocked), or ``None``."""
        route = (key.origin, key.route, key.method)
        if route in self._blocked_routes:
            return self._blocked_routes[route]
        if key.principal in self._auth_blocked:
            return f"auth_blocked:{key.principal}"
        return None

    def all_paused(self, now: float | None = None) -> bool:
        """True when every known group is paused (checkpoint and report recovery)."""
        t = self.clock() if now is None else now
        return bool(self._groups) and all(g.paused_until > t for g in self._groups.values())

    # -- recovery probe coordination --------------------------------------
    def claim_probe(self, key: HealthKey) -> bool:
        """Allow exactly one in-flight recovery probe per unhealthy group."""
        g = self._groups.setdefault(key.dep_group, _GroupState())
        if g.probe_in_flight:
            return False
        g.probe_in_flight = True
        return True

    def probe_result(self, key: HealthKey, ok: bool, now: float | None = None) -> None:
        t = self.clock() if now is None else now
        g = self._groups.setdefault(key.dep_group, _GroupState())
        g.probe_in_flight = False
        if ok:
            g.healthy, g.paused_until = True, 0.0
        else:
            g.healthy = False
            g.paused_until = t + self.max_cooldown_seconds

    # -- evidence -> decision ---------------------------------------------
    def record(
        self,
        key: HealthKey,
        outcome: TransportOutcome,
        *,
        status: int | None = None,
        baseline: bool = False,
        write_capable: bool = False,
        unauthorized_probe: bool = False,
        retry_after: float | None = None,
        can_refresh_auth: bool = False,
        shares_service_with_chat: bool = False,
        now: float | None = None,
    ) -> Decision:
        """Classify one outcome and return what the scheduler should do."""
        t = self.clock() if now is None else now
        route = (key.origin, key.route, key.method)
        incident = f"{key.dep_group}|{key.principal}"

        if outcome == TransportOutcome.OK:
            self._dead_counts.pop(route, None)
            g = self._groups.setdefault(key.dep_group, _GroupState())
            g.healthy, g.paused_until = True, 0.0
            return Decision(Action.CONTINUE)

        if outcome == TransportOutcome.HTTP_4XX:
            return self._record_4xx(
                key, route, status, baseline, unauthorized_probe, can_refresh_auth
            )

        if outcome == TransportOutcome.RATE_LIMIT:
            delay = min(retry_after or _DEFAULT_RATE_LIMIT_DELAY, self.max_cooldown_seconds)
            if not self.budget.try_consume(incident, t):
                return Decision(Action.GIVE_UP, reason="retry_budget_exhausted")
            self._pause(key.dep_group, t + delay)
            return Decision(Action.RETRY_LATER, delay, "rate_limited")

        if outcome in RETRIABLE_OUTCOMES or outcome in (
            TransportOutcome.HTTP_5XX,
            TransportOutcome.REQUEST_ERROR,
        ):
            if write_capable:
                # Never blindly retry an ambiguous write.
                return Decision(Action.EFFECT_UNKNOWN, reason=outcome.value)
            if not self.budget.try_consume(incident, t):
                self._pause(key.dep_group, t + self.max_cooldown_seconds)
                return Decision(Action.GIVE_UP, reason="retry_budget_exhausted")
            delay = min(retry_after or 10.0, self.max_cooldown_seconds)
            self._pause(key.dep_group, t + delay)
            self._groups[key.dep_group].healthy = False
            return Decision(
                Action.PAUSE_AND_PROBE,
                delay,
                outcome.value,
                check_chat_baseline_first=shares_service_with_chat,
            )

        return Decision(Action.CONTINUE)

    # -- internals ---------------------------------------------------------
    def _pause(self, group: str, until: float) -> None:
        g = self._groups.setdefault(group, _GroupState())
        g.paused_until = max(g.paused_until, until)

    def _record_4xx(
        self,
        key: HealthKey,
        route: tuple[str, str, str],
        status: int | None,
        baseline: bool,
        unauthorized_probe: bool,
        can_refresh_auth: bool,
    ) -> Decision:
        if status in (401, 403):
            if not baseline:
                return Decision(Action.CONTINUE, reason="denied_control_evidence")
            if status == 401 and can_refresh_auth:
                return Decision(Action.REFRESH_AUTH)
            if status == 401:
                self._auth_blocked.add(key.principal)
                return Decision(Action.AUTH_BLOCKED, reason=f"baseline_{status}")
            return Decision(Action.CONTINUE, reason="403_is_not_unavailability")
        if status in (404, 405, 422):
            if unauthorized_probe:
                return Decision(Action.CONTINUE, reason="denied_control_evidence")
            if baseline:
                if route not in self._resolved_routes:
                    self._resolved_routes.add(route)
                    return Decision(Action.RESOLVE_ROUTE, reason=f"baseline_{status}")
                reason = f"baseline_route_unresolved_{status}"
                self._blocked_routes[route] = reason
                return Decision(Action.BLOCK_ROUTE, reason=reason)
            n = self._dead_counts.get(route, 0) + 1
            self._dead_counts[route] = n
            if n >= _DEAD_ROUTE_THRESHOLD:
                reason = f"route_dead_{status}"
                self._blocked_routes[route] = reason
                return Decision(Action.BLOCK_ROUTE, reason=reason)
        return Decision(Action.CONTINUE)

