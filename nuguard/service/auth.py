"""API-key authentication and failed-auth rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass

from nuguard.service.settings import ApiKeyConfig, ServiceSettings

KEY_PREFIX = "ngs_"


def generate_key() -> str:
    """A new random API key (shown once; store only its SHA-256)."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Caller:
    id: str
    max_concurrent_scans: int
    max_queued_scans: int


class Authenticator:
    """Constant-time lookup of a bearer key against the configured SHA-256 digests."""

    def __init__(self, keys: list[ApiKeyConfig]) -> None:
        self._keys = [(bytes.fromhex(k.sha256), k) for k in keys]

    @classmethod
    def from_settings(cls, settings: ServiceSettings) -> "Authenticator":
        return cls(settings.api_keys)

    def authenticate(self, authorization: str | None) -> Caller | None:
        if not authorization:
            return None
        scheme, _, key = authorization.partition(" ")
        if scheme.lower() != "bearer" or not key.strip():
            return None
        digest = hashlib.sha256(key.strip().encode("utf-8")).digest()
        found: ApiKeyConfig | None = None
        # Compare against every key so timing does not reveal which one matched.
        for stored, config in self._keys:
            if hmac.compare_digest(stored, digest):
                found = config
        if found is None:
            return None
        return Caller(found.id, found.max_concurrent_scans, found.max_queued_scans)


class FailedAuthLimiter:
    """Sliding one-minute window of failed authentications per remote address."""

    def __init__(self, limit_per_minute: int) -> None:
        self._limit = limit_per_minute
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _window(self, address: str, now: float) -> deque[float]:
        events = self._events.setdefault(address, deque())
        while events and now - events[0] > 60:
            events.popleft()
        return events

    def blocked(self, address: str) -> bool:
        with self._lock:
            return len(self._window(address, time.monotonic())) >= self._limit

    def record_failure(self, address: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._window(address, now).append(now)
            if len(self._events) > 10_000:  # bound memory under address churn
                for key in [k for k, v in self._events.items() if not v][:5_000]:
                    del self._events[key]
