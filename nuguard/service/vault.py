"""In-memory holder for per-job GitHub tokens.

Tokens are never written to the database, logs, results or argv. One lives
here from submission until the job reaches a terminal state; a restart loses
it by design (the job then fails with ``credential_unavailable``).
"""

from __future__ import annotations

import threading

from pydantic import SecretStr


class TokenVault:
    def __init__(self) -> None:
        self._tokens: dict[str, SecretStr] = {}
        self._lock = threading.Lock()

    def put(self, job_id: str, token: SecretStr) -> None:
        with self._lock:
            self._tokens[job_id] = token

    def get(self, job_id: str) -> SecretStr | None:
        with self._lock:
            return self._tokens.get(job_id)

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._tokens.pop(job_id, None)

    def __contains__(self, job_id: object) -> bool:
        with self._lock:
            return job_id in self._tokens

    def __len__(self) -> int:
        with self._lock:
            return len(self._tokens)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()
