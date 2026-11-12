"""Job dispatch: claim queued jobs, run each in its own worker process, finalize.

Runs as asyncio tasks inside the API process (the only database writer).
Tokens move from the :class:`TokenVault` straight into the worker's stdin and
are wiped from the vault when the job reaches a terminal state.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from pydantic import SecretStr

from nuguard.common.logging import get_logger
from nuguard.service.models import ScanCreateRequest, ScanJob, ScanStatus
from nuguard.service.pipeline import JobSpec, PipelineError, combine_subpaths, split_repo_url
from nuguard.service.settings import ServiceSettings
from nuguard.service.store import JobRecord, JobStore, ensure_private_dir, utcnow
from nuguard.service.vault import TokenVault
from nuguard.service.worker import TEST_HOOKS_ENV

_log = get_logger("nuguard.service.dispatcher")

KILL_GRACE_S = 5.0

# Inherited by every worker; everything else is dropped (see worker_env).
_BASE_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT")


@dataclass
class WorkerOutcome:
    kind: str  # "result" | "error" | "timeout" | "cancelled" | "lost"
    result_path: str | None = None
    sbom_path: str | None = None
    code: str | None = None
    detail: str | None = None
    events: list[dict[str, Any]] | None = None


EventFn = Callable[[dict[str, Any]], Awaitable[None]]


class WorkerRunner(Protocol):
    async def run(
        self,
        spec: JobSpec,
        token: str | None,
        *,
        scratch: Path,
        env: dict[str, str],
        timeout_s: float,
        cancel: asyncio.Event,
        on_event: EventFn,
    ) -> WorkerOutcome: ...


def worker_env(
    settings: ServiceSettings, scratch: Path, *, use_llm: bool, test_hooks: bool = False
) -> dict[str, str]:
    """Explicit worker environment: no ambient credentials, private HOME/TMPDIR."""
    env = {k: os.environ[k] for k in _BASE_ENV if k in os.environ}
    for name in settings.jobs.passthrough_env:
        if name in os.environ:
            env[name] = os.environ[name]
    home, tmp = scratch / "home", scratch / "tmp"
    ensure_private_dir(home)
    ensure_private_dir(tmp)
    env.update({"HOME": str(home), "TMPDIR": str(tmp), "PYTHONDONTWRITEBYTECODE": "1",
                "NUGUARD_LOG_FORMAT": "json"})
    # Non-secret NuGuard settings the worker needs (credentials are never copied).
    for name in ("NUGUARD_LOG_LEVEL", "NUGUARD_TOOLS_JSON"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    # Source checkouts only: make `-m nuguard.service.worker` importable. Never for an
    # installed package — PYTHONPATH=site-packages would leak into scanner CLIs that live
    # in their own virtualenvs (Semgrep, Checkov) and break their imports.
    package_root = Path(__file__).resolve().parents[2]
    if (package_root / "pyproject.toml").is_file():
        env["PYTHONPATH"] = str(package_root)
    if settings.jobs.rlimit_as_bytes:
        env["NUGUARD_WORKER_RLIMIT_AS"] = str(settings.jobs.rlimit_as_bytes)
    env["NUGUARD_WORKER_RLIMIT_FSIZE"] = str(settings.jobs.rlimit_fsize_bytes)
    if settings.jobs.rlimit_nproc:
        env["NUGUARD_WORKER_RLIMIT_NPROC"] = str(settings.jobs.rlimit_nproc)
    if use_llm and settings.llm.enabled and settings.llm.api_key is not None:
        env["LITELLM_API_KEY"] = settings.llm.api_key.get_secret_value()
    if test_hooks:
        env[TEST_HOOKS_ENV] = "1"
    return env


def _descendants(pid: int) -> list[int]:
    """All descendant pids of *pid* (scanners run in their own sessions)."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="], capture_output=True, timeout=5, check=False
        ).stdout.decode()
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found: list[int] = []
    stack = [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found


def _signal_tree(root: int, pids: list[int], sig: int) -> None:
    groups: set[int] = set()
    for pid in [root, *pids]:
        try:
            groups.add(os.getpgid(pid))
        except (ProcessLookupError, PermissionError):
            continue
    own = os.getpgid(0)
    for pgid in groups - {own}:
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


async def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the worker and every descendant, then SIGKILL after a grace period."""
    if os.name != "posix":
        proc.kill()
        await proc.wait()
        return
    tree = _descendants(proc.pid)
    _signal_tree(proc.pid, tree, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), KILL_GRACE_S)
    except asyncio.TimeoutError:
        pass
    _signal_tree(proc.pid, list({*tree, *_descendants(proc.pid)}), signal.SIGKILL)
    await proc.wait()


class SubprocessRunner:
    """Runs ``python -m nuguard.service.worker`` with an explicit environment."""

    async def run(
        self,
        spec: JobSpec,
        token: str | None,
        *,
        scratch: Path,
        env: dict[str, str],
        timeout_s: float,
        cancel: asyncio.Event,
        on_event: EventFn,
    ) -> WorkerOutcome:
        # -P: do not put the working directory (job scratch) on sys.path.
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-P", "-m", "nuguard.service.worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(scratch),
            env=env,
            start_new_session=True,
            limit=1024 * 1024,
        )
        assert proc.stdin and proc.stdout and proc.stderr
        envelope = json.dumps({"spec": spec.model_dump(mode="json"), "token": token})
        proc.stdin.write(envelope.encode() + b"\n")
        try:
            await proc.stdin.drain()
        finally:
            proc.stdin.close()
            del envelope

        terminal: dict[str, Any] | None = None

        async def read_events() -> None:
            nonlocal terminal
            assert proc.stdout
            async for raw in proc.stdout:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if message.get("type") in ("result", "error"):
                    terminal = message
                await on_event(message)

        async def drain_stderr() -> None:
            assert proc.stderr
            async for line in proc.stderr:
                _log.debug("worker[%s]: %s", spec.job_id, line.decode(errors="replace").rstrip()[:500])

        readers = asyncio.gather(read_events(), drain_stderr())
        waiter = asyncio.ensure_future(proc.wait())
        cancelled = asyncio.ensure_future(cancel.wait())
        done, _ = await asyncio.wait({waiter, cancelled}, timeout=timeout_s,
                                     return_when=asyncio.FIRST_COMPLETED)
        outcome: WorkerOutcome | None = None
        if waiter not in done:
            outcome = WorkerOutcome(kind="cancelled" if cancelled in done else "timeout")
            await kill_process_tree(proc)
        cancelled.cancel()
        try:
            await asyncio.wait_for(readers, 10)
        except (asyncio.TimeoutError, Exception):
            readers.cancel()
        if outcome is not None:
            return outcome
        if terminal is None:
            return WorkerOutcome(kind="lost", code="worker_lost",
                                 detail=f"worker exited with status {proc.returncode}")
        if terminal["type"] == "result":
            return WorkerOutcome(kind="result", result_path=terminal.get("path"),
                                 sbom_path=terminal.get("sbom_path"))
        return WorkerOutcome(kind="error", code=str(terminal.get("code") or "analysis_crashed"),
                             detail=str(terminal.get("detail") or ""))


# ── error envelopes ──────────────────────────────────────────────────────────

# Failures caused by the service rather than the request: the job releases its
# Idempotency-Key so the caller can resubmit with the same key.
RETRYABLE_CODES = frozenset({"credential_unavailable", "worker_lost"})

_ERROR_STATUS = {
    "repo_not_found_or_no_access": 404, "auth_failed": 401, "ref_not_found": 404,
    "repo_too_large": 413, "clone_timeout": 504, "clone_failed": 502,
    "subpath_not_found": 404, "sbom_failed": 500, "analysis_crashed": 500,
    "job_timeout": 504, "worker_lost": 503, "credential_unavailable": 409,
    "cancelled": 409, "invalid_request": 422,
}
_ERROR_TITLE = {
    "repo_not_found_or_no_access": "Repository not found or not accessible",
    "auth_failed": "Authentication failed",
    "ref_not_found": "Ref not found",
    "repo_too_large": "Repository too large",
    "clone_timeout": "Clone timed out",
    "clone_failed": "Clone failed",
    "subpath_not_found": "Subpath not found",
    "sbom_failed": "AI-SBOM generation failed",
    "analysis_crashed": "Analysis failed",
    "job_timeout": "Job timed out",
    "worker_lost": "Worker lost",
    "credential_unavailable": "Credential unavailable",
    "cancelled": "Scan cancelled",
    "invalid_request": "Invalid request",
}


def problem(code: str, detail: str | None = None, *, status: int | None = None) -> dict[str, Any]:
    from nuguard.service.models import ProblemDetail  # noqa: PLC0415

    return ProblemDetail(
        type="https://nuguard.ai/problems/" + code.replace("_", "-"),
        title=_ERROR_TITLE.get(code, code.replace("_", " ").capitalize()),
        status=status or _ERROR_STATUS.get(code, 500),
        code=code,
        detail=detail,
    ).model_dump(mode="json")


# ── dispatcher ───────────────────────────────────────────────────────────────


def job_view(record: JobRecord, links: dict[str, str]) -> ScanJob:
    from nuguard.service.models import ProblemDetail, ScanPhase  # noqa: PLC0415

    return ScanJob(
        id=record.id,
        status=ScanStatus(record.status),
        phase=ScanPhase(record.phase) if record.phase else None,
        repo_url=record.repo_url,
        ref_requested=record.ref_requested,
        commit_sha=record.commit_sha,
        client_reference=record.client_reference,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error=ProblemDetail.model_validate_json(record.error_json) if record.error_json else None,
        links=links,
    )


class Dispatcher:
    def __init__(
        self,
        settings: ServiceSettings,
        store: JobStore,
        vault: TokenVault,
        runner: WorkerRunner | None = None,
        *,
        links_for: Callable[[str], dict[str, str]] | None = None,
        on_terminal: Callable[[JobRecord], Awaitable[None]] | None = None,
        test_hooks: dict[str, list[str]] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.vault = vault
        self.runner: WorkerRunner = runner or SubprocessRunner()
        self.links_for = links_for or (lambda job_id: {})
        self.on_terminal = on_terminal
        # job_id → hooks, or "*" for every job; process-level tests only.
        self.test_hooks = test_hooks or {}
        self._wake = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._owners: dict[str, str] = {}
        self._cancels: dict[str, asyncio.Event] = {}
        self._done: dict[str, asyncio.Event] = {}
        self._loop_task: asyncio.Task[None] | None = None
        self.metrics: dict[str, Any] = {"scans_total": {}, "tool_runs_total": {},
                                        "durations": []}

    # lifecycle

    async def start(self) -> None:
        now = utcnow()
        expires = now + timedelta(hours=self.settings.jobs.result_ttl_hours)
        for job_id, code in self.store.recover_after_restart():
            self.store.finish(
                job_id, status="failed", finished_at=now, expires_at=expires,
                error_json=json.dumps(problem(code, _ERROR_TITLE.get(code))),
            )
            self.store.update(job_id, idempotency_key=None)
            _log.warning("job %s failed after restart: %s", job_id, code,
                         extra={"job_id": job_id})
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
        for event in self._cancels.values():
            event.set()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self.vault.clear()

    def notify(self) -> None:
        self._wake.set()

    @property
    def running(self) -> int:
        return len(self._tasks)

    def running_for(self, owner_id: str) -> int:
        return sum(1 for job, owner in self._owners.items() if owner == owner_id)

    async def wait_for(self, job_id: str, timeout_s: float) -> None:
        """Return when *job_id* reaches a terminal status, or after *timeout_s*."""
        # Register before checking the store so a completion in between is not missed.
        event = self._done.setdefault(job_id, asyncio.Event())
        record = self.store.get(job_id)
        if record is None or record.status not in ("queued", "running"):
            if not event.is_set():
                self._done.pop(job_id, None)
            return
        try:
            await asyncio.wait_for(event.wait(), timeout_s)
        except asyncio.TimeoutError:
            pass

    def cancel(self, job_id: str) -> None:
        """Cancel a queued job now, or ask a running worker to stop."""
        if job_id in self._cancels:
            self._cancels[job_id].set()
            return
        record = self.store.get(job_id)
        if record is not None and record.status == "queued":
            self._finish(record, "cancelled", error=problem("cancelled", "Cancelled by caller"))

    # scheduling

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), 1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            try:
                self._schedule()
            except Exception as exc:  # keep dispatching even if one pass fails
                _log.error("dispatch pass failed: %s", type(exc).__name__)

    def _owner_limit(self, owner_id: str) -> int:
        for key in self.settings.api_keys:
            if key.id == owner_id:
                return key.max_concurrent_scans
        return 1

    def _schedule(self) -> None:
        for record in self.store.queued():
            if self.running >= self.settings.jobs.max_concurrent:
                return
            if self.running_for(record.owner_id) >= self._owner_limit(record.owner_id):
                continue
            if record.has_token and record.id not in self.vault:
                self._finish(record, "failed", error=problem("credential_unavailable",
                                                            "The token was not retained"))
                continue
            if not self.store.claim(record.id, utcnow()):
                continue
            self._owners[record.id] = record.owner_id
            self._cancels[record.id] = asyncio.Event()
            self._tasks[record.id] = asyncio.create_task(self._run_job(record))

    def build_spec(self, record: JobRecord) -> JobSpec:
        request = ScanCreateRequest.model_validate_json(record.request_json)
        clone_url, ref, url_subpath = split_repo_url(request.repo_url, request.ref)
        options = request.options
        allowed = self.settings.analysis_defaults.tools
        tools = [t for t in allowed if options.tools is None or t in options.tools]
        return JobSpec(
            job_id=record.id,
            repo_url=record.repo_url,
            clone_url=clone_url,
            ref=ref,
            subpath=combine_subpaths(url_subpath, options.subpath),
            options=options,
            tools=tools,
            allowed_hosts=list(self.settings.git.allowed_hosts),
            clone_timeout_s=self.settings.git.clone_timeout_s,
            max_repo_bytes=self.settings.git.max_repo_bytes,
            llm_model=self.settings.llm.model if options.use_llm else None,
            llm_api_base=self.settings.llm.api_base if options.use_llm else None,
            image_digest=os.environ.get("NUGUARD_IMAGE_DIGEST") or None,
            test_hooks=self.test_hooks.get(record.id) or self.test_hooks.get("*") or [],
        )

    async def _run_job(self, record: JobRecord) -> None:
        job_id = record.id
        scratch = Path(self.settings.jobs.scratch_dir) / job_id
        started = time.monotonic()
        try:
            try:
                spec = self.build_spec(record)
            except (PipelineError, ValueError) as exc:
                code = getattr(exc, "code", "invalid_request")
                self._finish(record, "failed", error=problem(code, getattr(exc, "detail", None)))
                return
            ensure_private_dir(scratch)
            secret: SecretStr | None = self.vault.get(job_id)
            env = worker_env(self.settings, scratch, use_llm=spec.options.use_llm,
                             test_hooks=bool(spec.test_hooks))

            async def on_event(message: dict[str, Any]) -> None:
                kind = message.get("type")
                if kind == "phase" and message.get("phase") in (
                    "cloning", "sbom", "analyzing", "finalizing"
                ):
                    self.store.update(job_id, phase=message["phase"])
                elif kind == "commit" and isinstance(message.get("sha"), str):
                    self.store.update(job_id, commit_sha=message["sha"][:64])

            outcome = await self.runner.run(
                spec,
                secret.get_secret_value() if secret else None,
                scratch=scratch,
                env=env,
                timeout_s=self.settings.jobs.job_timeout_s,
                cancel=self._cancels[job_id],
                on_event=on_event,
            )
            del secret
            self._finalize(record, outcome)
        except Exception as exc:
            _log.error("job %s crashed in dispatcher: %s", job_id, type(exc).__name__,
                       extra={"job_id": job_id})
            self._finish(record, "failed", error=problem("worker_lost", "internal error"))
        finally:
            self.vault.discard(job_id)
            shutil.rmtree(scratch, ignore_errors=True)
            self._tasks.pop(job_id, None)
            self._owners.pop(job_id, None)
            self._cancels.pop(job_id, None)
            self.metrics["durations"].append(time.monotonic() - started)
            del self.metrics["durations"][:-1000]
            self.notify()

    def _finalize(self, record: JobRecord, outcome: WorkerOutcome) -> None:
        from nuguard.service.results import ScanBody, assemble_result  # noqa: PLC0415

        if outcome.kind == "timeout":
            self._finish(record, "failed", error=problem("job_timeout", "The scan exceeded its time limit"))
            return
        if outcome.kind == "cancelled":
            self._finish(record, "cancelled", error=problem("cancelled", "Cancelled by caller"))
            return
        if outcome.kind != "result" or not outcome.result_path:
            code = outcome.code or "worker_lost"
            self._finish(record, "failed", error=problem(code, _safe_detail(outcome.detail, code)))
            return
        body = ScanBody.model_validate_json(Path(outcome.result_path).read_text(encoding="utf-8"))
        sbom_json = None
        if outcome.sbom_path and Path(outcome.sbom_path).is_file():
            sbom_json = Path(outcome.sbom_path).read_text(encoding="utf-8")
        current = self.store.get(record.id) or record
        current.commit_sha = body.commit_sha or current.commit_sha
        finished = utcnow()
        current.status = body.status.value
        current.finished_at = finished
        current.phase = None
        result = assemble_result(body, job_view(current, self.links_for(record.id)))
        for run in body.coverage.tool_runs:
            key = (run.tool, run.status.value)
            self.metrics["tool_runs_total"][key] = self.metrics["tool_runs_total"].get(key, 0) + 1
        if body.commit_sha:
            self.store.update(record.id, commit_sha=body.commit_sha)
        self._finish(
            record, body.status.value, result_json=result.model_dump_json(),
            result_sha256=result.result_sha256, sbom_json=sbom_json, finished_at=finished,
        )

    def _finish(
        self,
        record: JobRecord,
        status: str,
        *,
        error: dict[str, Any] | None = None,
        result_json: str | None = None,
        result_sha256: str | None = None,
        sbom_json: str | None = None,
        finished_at: Any = None,
    ) -> None:
        finished = finished_at or utcnow()
        changed = self.store.finish(
            record.id,
            status=status,
            finished_at=finished,
            expires_at=finished + timedelta(hours=self.settings.jobs.result_ttl_hours),
            error_json=json.dumps(error) if error else None,
            result_json=result_json,
            result_sha256=result_sha256,
            sbom_json=sbom_json,
        )
        self.vault.discard(record.id)
        if not changed:
            return
        if error and error.get("code") in RETRYABLE_CODES:
            self.store.update(record.id, idempotency_key=None)
        totals = self.metrics["scans_total"]
        totals[status] = totals.get(status, 0) + 1
        _log.info("job %s finished: %s", record.id, status,
                  extra={"job_id": record.id, "owner_id": record.owner_id})
        event = self._done.pop(record.id, None)
        if event is not None:
            event.set()
        if self.on_terminal is not None:
            final = self.store.get(record.id)
            if final is not None:
                asyncio.ensure_future(self.on_terminal(final))


def _safe_detail(detail: str | None, code: str) -> str:
    """Worker details are already sanitized; cap length and fall back to the title."""
    text = (detail or "").strip().splitlines()[0] if (detail or "").strip() else ""
    return text[:300] or _ERROR_TITLE.get(code, "The scan failed")
