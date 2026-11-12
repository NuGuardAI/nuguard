"""SQLite job store: WAL mode, one writer (the API process), serialized by a lock."""

from __future__ import annotations

import base64
import os
import sqlite3
import threading
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from nuguard.service.store import JobRecord, ensure_private_dir

_COLUMNS = (
    "id", "owner_id", "status", "phase", "repo_url", "ref_requested", "commit_sha",
    "client_reference", "request_json", "request_sha256", "idempotency_key", "has_token",
    "error_json", "result_ref", "result_sha256", "created_at", "started_at", "finished_at",
    "expires_at",
)
_TIME_FIELDS = {"created_at", "started_at", "finished_at", "expires_at"}
_UPDATABLE = {"phase", "commit_sha", "status", "error_json", "idempotency_key"}
_TERMINAL = ("succeeded", "partial", "failed", "cancelled")
_INLINE = "inline"


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_row(row: sqlite3.Row) -> JobRecord:
    data: dict[str, Any] = {k: row[k] for k in _COLUMNS}
    for key in _TIME_FIELDS:
        data[key] = datetime.fromisoformat(data[key]) if data[key] else None
    data["has_token"] = bool(data["has_token"])
    return JobRecord(**data)


class SqliteJobStore:
    def __init__(self, db_path: Path, *, inline_result_max_bytes: int = 5 * 1024 * 1024) -> None:
        self.db_path = Path(db_path)
        ensure_private_dir(self.db_path.parent)
        self.results_dir = self.db_path.parent / "results"
        ensure_private_dir(self.results_dir)
        self._inline_max = inline_result_max_bytes
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None, timeout=5.0
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            schema = resources.files("nuguard.service.store").joinpath(
                "migrations/001_jobs.sql"
            ).read_text(encoding="utf-8")
            self._conn.executescript(schema)
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    # ── jobs ────────────────────────────────────────────────────────────────

    def create(self, record: JobRecord) -> None:
        values = []
        for key in _COLUMNS:
            value = getattr(record, key)
            values.append(_ts(value) if key in _TIME_FIELDS else value)
        values[_COLUMNS.index("has_token")] = int(record.has_token)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO scan_jobs ({', '.join(_COLUMNS)}) VALUES ({placeholders})", values
            )

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM scan_jobs WHERE id = ?", (job_id,)).fetchone()
        return _from_row(row) if row else None

    def list_jobs(
        self,
        owner_id: str,
        *,
        status: str | None = None,
        repo: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[JobRecord], str | None]:
        sql = "SELECT * FROM scan_jobs WHERE owner_id = ?"
        params: list[Any] = [owner_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if repo:
            sql += " AND repo_url = ?"
            params.append(repo)
        if cursor:
            created, job_id = _decode_cursor(cursor)
            sql += " AND (created_at < ? OR (created_at = ? AND id < ?))"
            params += [created, created, job_id]
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit + 1)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        records = [_from_row(r) for r in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and records:
            last = records[-1]
            next_cursor = _encode_cursor(_ts(last.created_at) or "", last.id)
        return records, next_cursor

    def find_idempotent(self, owner_id: str, key: str, since: datetime) -> JobRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scan_jobs WHERE owner_id = ? AND idempotency_key = ?",
                (owner_id, key),
            ).fetchone()
        if row is None:
            return None
        record = _from_row(row)
        if record.created_at < since:
            # Expired key: free it so the caller can reuse it for a new job.
            with self._lock:
                self._conn.execute(
                    "UPDATE scan_jobs SET idempotency_key = NULL WHERE id = ?", (record.id,)
                )
            return None
        return record

    def count(self, statuses: tuple[str, ...], owner_id: str | None = None) -> int:
        sql = f"SELECT COUNT(*) FROM scan_jobs WHERE status IN ({', '.join('?' for _ in statuses)})"
        params: list[Any] = list(statuses)
        if owner_id is not None:
            sql += " AND owner_id = ?"
            params.append(owner_id)
        with self._lock:
            return int(self._conn.execute(sql, params).fetchone()[0])

    def queued(self) -> list[JobRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scan_jobs WHERE status = 'queued' ORDER BY created_at, id"
            ).fetchall()
        return [_from_row(r) for r in rows]

    def claim(self, job_id: str, started_at: datetime) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE scan_jobs SET status = 'running', phase = 'cloning', started_at = ? "
                "WHERE id = ? AND status = 'queued'",
                (_ts(started_at), job_id),
            )
            return cur.rowcount == 1

    def update(self, job_id: str, **fields: object) -> None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE scan_jobs SET {assignments} WHERE id = ?", [*fields.values(), job_id]
            )

    def finish(
        self,
        job_id: str,
        *,
        status: str,
        finished_at: datetime,
        expires_at: datetime,
        error_json: str | None = None,
        result_json: str | None = None,
        result_sha256: str | None = None,
        sbom_json: str | None = None,
    ) -> bool:
        """Move a non-terminal job to *status*; results are immutable once written."""
        result_ref = None
        stored_json = result_json
        if result_json is not None:
            if len(result_json.encode("utf-8")) > self._inline_max:
                path = self.results_dir / f"{job_id}.json"
                _write_private(path, result_json)
                result_ref, stored_json = str(path), ""
            else:
                result_ref = _INLINE
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "UPDATE scan_jobs SET status = ?, finished_at = ?, expires_at = ?, "
                    "error_json = ?, result_ref = ?, result_sha256 = ?, phase = NULL "
                    "WHERE id = ? AND status IN ('queued', 'running')",
                    (status, _ts(finished_at), _ts(expires_at), error_json, result_ref,
                     result_sha256, job_id),
                )
                if cur.rowcount != 1:
                    self._conn.execute("ROLLBACK")
                    return False
                if result_json is not None:
                    self._conn.execute(
                        "INSERT INTO scan_results (job_id, result_json, sbom_json) VALUES (?, ?, ?)",
                        (job_id, stored_json, sbom_json),
                    )
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def get_result(self, job_id: str) -> tuple[str, str | None] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT j.result_ref, r.result_json, r.sbom_json FROM scan_jobs j "
                "JOIN scan_results r ON r.job_id = j.id WHERE j.id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        if row["result_ref"] and row["result_ref"] != _INLINE:
            try:
                return Path(row["result_ref"]).read_text(encoding="utf-8"), row["sbom_json"]
            except OSError:
                return None
        return row["result_json"], row["sbom_json"]

    def expire(self, now: datetime) -> list[str]:
        """Delete results past their TTL and mark those jobs ``expired``."""
        placeholders = ", ".join("?" for _ in _TERMINAL)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, result_ref FROM scan_jobs WHERE status IN ({placeholders}) "
                "AND expires_at IS NOT NULL AND expires_at <= ?",
                (*_TERMINAL, _ts(now)),
            ).fetchall()
            for row in rows:
                if row["result_ref"] and row["result_ref"] != _INLINE:
                    Path(row["result_ref"]).unlink(missing_ok=True)
                self._conn.execute("DELETE FROM scan_results WHERE job_id = ?", (row["id"],))
                self._conn.execute(
                    "UPDATE scan_jobs SET status = 'expired', result_ref = NULL WHERE id = ?",
                    (row["id"],),
                )
        return [row["id"] for row in rows]

    def recover_after_restart(self) -> list[tuple[str, str]]:
        """Jobs that cannot continue after a restart, as (job_id, error code).

        Running jobs lost their worker (``worker_lost``). Queued jobs that were
        given a token lost it with the old process (``credential_unavailable``);
        queued jobs without a token simply run.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, status, has_token FROM scan_jobs WHERE status IN ('queued', 'running')"
            ).fetchall()
        failed: list[tuple[str, str]] = []
        for row in rows:
            if row["status"] == "running":
                failed.append((row["id"], "worker_lost"))
            elif row["has_token"]:
                failed.append((row["id"], "credential_unavailable"))
        return failed

    def audit(
        self,
        action: str,
        *,
        owner_id: str | None = None,
        job_id: str | None = None,
        repo_url: str | None = None,
        remote_addr: str | None = None,
        request_sha256: str | None = None,
    ) -> None:
        from nuguard.service.store import utcnow  # noqa: PLC0415

        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (ts, owner_id, action, job_id, repo_url, remote_addr, "
                "request_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_ts(utcnow()), owner_id, action, job_id, repo_url, remote_addr, request_sha256),
            )

    def check_writable(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute("ROLLBACK")
            return True
        except sqlite3.Error:
            return False

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _write_private(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def _encode_cursor(created_at: str, job_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at}|{job_id}".encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        created, _, job_id = raw.partition("|")
        datetime.fromisoformat(created)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid cursor") from None
    return created, job_id
