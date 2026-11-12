"""Job persistence. Routes and the dispatcher depend only on :class:`JobStore`."""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_job_id() -> str:
    """``scn_`` + a 26-character ULID (time-ordered, Crockford base32)."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(secrets.token_bytes(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "scn_" + "".join(reversed(chars))


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class JobRecord:
    id: str
    owner_id: str
    status: str
    repo_url: str
    request_json: str
    request_sha256: str
    created_at: datetime
    phase: str | None = None
    ref_requested: str | None = None
    commit_sha: str | None = None
    client_reference: str | None = None
    idempotency_key: str | None = None
    has_token: bool = False
    error_json: str | None = None
    result_ref: str | None = None
    result_sha256: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None


class JobStore(Protocol):
    """Storage contract; a Postgres implementation can slot in behind it."""

    def create(self, record: JobRecord) -> None: ...
    def get(self, job_id: str) -> JobRecord | None: ...
    def list_jobs(
        self, owner_id: str, *, status: str | None, repo: str | None,
        cursor: str | None, limit: int,
    ) -> tuple[list[JobRecord], str | None]: ...
    def find_idempotent(self, owner_id: str, key: str, since: datetime) -> JobRecord | None: ...
    def count(self, statuses: tuple[str, ...], owner_id: str | None = None) -> int: ...
    def queued(self) -> list[JobRecord]: ...
    def claim(self, job_id: str, started_at: datetime) -> bool: ...
    def update(self, job_id: str, **fields: object) -> None: ...
    def finish(
        self, job_id: str, *, status: str, finished_at: datetime, expires_at: datetime,
        error_json: str | None = None, result_json: str | None = None,
        result_sha256: str | None = None, sbom_json: str | None = None,
    ) -> bool: ...
    def get_result(self, job_id: str) -> tuple[str, str | None] | None: ...
    def expire(self, now: datetime) -> list[str]: ...
    def recover_after_restart(self) -> list[tuple[str, str]]: ...
    def audit(
        self, action: str, *, owner_id: str | None = None, job_id: str | None = None,
        repo_url: str | None = None, remote_addr: str | None = None,
        request_sha256: str | None = None,
    ) -> None: ...
    def check_writable(self) -> bool: ...
    def vacuum(self) -> None: ...
    def close(self) -> None: ...


def ensure_private_dir(path: os.PathLike[str] | str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)


__all__ = ["JobRecord", "JobStore", "ensure_private_dir", "new_job_id", "utcnow"]
