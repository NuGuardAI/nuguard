"""Bounded process execution and diagnostics shared by local scanner plugins."""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from nuguard.analysis.models import AnalysisResult
from nuguard.common.logging import get_logger

_log = get_logger(__name__)
_MAX_OUTPUT_BYTES = 32 * 1024 * 1024


def positive_timeout(value: Any) -> float:
    """Validate a finite, positive scanner deadline in seconds."""
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Scanner deadlines must be finite and positive")
    return timeout


def scan_paths(paths: Iterable[str]) -> list[str]:
    """Resolve and deduplicate paths already covered by a directory scan."""
    candidates = sorted({Path(p).resolve() for p in paths}, key=lambda p: (len(p.parts), str(p)))
    selected: list[Path] = []
    for candidate in candidates:
        if not any(parent.is_dir() and candidate.is_relative_to(parent) for parent in selected):
            selected.append(candidate)
    return [str(p) for p in selected]


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill scanner workers and reap the parent, with bounded cleanup."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if result.returncode != 0:
                process.kill()
        else:
            process.kill()
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _log.error("Scanner process cleanup did not finish: pid=%d", process.pid)


def _execute(
    cmd: list[str], timeout: float, tool: str, path: str
) -> tuple[Any, str | None, int | None]:
    """Capture JSON without pipe deadlocks, and never log raw scanner output."""
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                cmd,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                start_new_session=os.name == "posix",
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0,
            )
            heartbeat = started + 15
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    return None, "timeout", None
                try:
                    process.wait(timeout=min(remaining, 1.0))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= heartbeat:
                        _log.info(
                            "Scanner running: tool=%s path=%r elapsed=%.2fs timeout=%.2fs",
                            tool,
                            path,
                            time.monotonic() - started,
                            timeout,
                        )
                        heartbeat = time.monotonic() + 15
                    if (
                        max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size)
                        > _MAX_OUTPUT_BYTES
                    ):
                        return None, "output_limit", None
            if process.returncode not in (0, 1):
                return None, "exit_error", process.returncode
            if (
                max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size)
                > _MAX_OUTPUT_BYTES
            ):
                return None, "output_limit", process.returncode
            stdout.seek(0)
            try:
                return json.load(stdout), None, process.returncode
            except (ValueError, UnicodeError):
                return None, "invalid_json", process.returncode
        except OSError:
            return None, "process_error", None
        finally:
            if process is not None and (os.name == "posix" or process.poll() is None):
                _kill_process_tree(process)


def _reported_errors(data: Any) -> bool:
    """Recognize scanner coverage errors even when the process exits successfully."""
    for entry in data if isinstance(data, list) else [data]:
        if not isinstance(entry, dict):
            continue
        if entry.get("errors"):
            return True
        for key in ("results", "summary"):
            nested = entry.get(key)
            if isinstance(nested, dict) and nested.get("parsing_errors"):
                return True
    return False


def run_scanner(
    tool: str,
    paths: Iterable[str],
    command: Callable[[str], list[str]],
    parse: Callable[[Any, str], list[dict[str, Any]]],
    *,
    timeout: float = 120.0,
    total_timeout: float = 300.0,
) -> AnalysisResult:
    """Run every unique scan path within a total deadline, preserving partial findings."""
    try:
        timeout = positive_timeout(timeout)
        total_timeout = positive_timeout(total_timeout)
    except (TypeError, ValueError, OverflowError):
        _log.warning("Scanner configuration error: tool=%s reason=invalid_deadline", tool)
        return AnalysisResult(
            status="error", plugin=tool, message="Invalid scanner deadline configuration"
        )
    try:
        selected = scan_paths(paths)
    except (OSError, ValueError, TypeError, RuntimeError):
        _log.warning("Scanner configuration error: tool=%s reason=invalid_paths", tool)
        return AnalysisResult(status="error", plugin=tool, message="Invalid scanner paths")
    findings: list[dict[str, Any]] = []
    invocations: list[dict[str, Any]] = []
    started = time.monotonic()
    _log.info(
        "Scanner start: tool=%s paths=%d timeout=%.2fs total_timeout=%.2fs",
        tool,
        len(selected),
        timeout,
        total_timeout,
    )
    for index, path in enumerate(selected, 1):
        remaining = total_timeout - (time.monotonic() - started)
        if remaining <= 0:
            invocations.append(
                {"path": path, "status": "error", "reason": "total_timeout", "elapsed_seconds": 0.0}
            )
            _log.warning(
                "Scanner path untested: tool=%s index=%d/%d path=%r reason=total_timeout",
                tool,
                index,
                len(selected),
                path,
            )
            continue
        path_started = time.monotonic()
        effective_timeout = min(timeout, remaining)
        _log.info(
            "Scanner path start: tool=%s index=%d/%d path=%r timeout=%.2fs",
            tool,
            index,
            len(selected),
            path,
            effective_timeout,
        )
        try:
            data, error, returncode = _execute(command(path), effective_timeout, tool, path)
        except (OSError, ValueError, TypeError):
            data, error, returncode = None, "process_error", None
        path_findings: list[dict[str, Any]] = []
        if error is None:
            try:
                path_findings = parse(data, path)
                if _reported_errors(data):
                    error = "scan_errors"
            except (ValueError, TypeError, KeyError, AttributeError):
                error = "invalid_output"
        findings.extend(path_findings)
        elapsed = time.monotonic() - path_started
        invocation = {
            "path": path,
            "status": "error" if error else "ok",
            "reason": error or "",
            "returncode": returncode,
            "elapsed_seconds": elapsed,
            "findings": len(path_findings),
        }
        invocations.append(invocation)
        (_log.warning if error else _log.info)(
            "Scanner path end: tool=%s index=%d/%d path=%r elapsed=%.2fs outcome=%s returncode=%s findings=%d",
            tool,
            index,
            len(selected),
            path,
            elapsed,
            error or "ok",
            returncode,
            len(path_findings),
        )
    errors = sum(item["status"] == "error" for item in invocations)
    status = "error" if errors else "warning" if findings else "ok"
    elapsed = time.monotonic() - started
    _log.info(
        "Scanner end: tool=%s elapsed=%.2fs paths=%d errors=%d findings=%d",
        tool,
        elapsed,
        len(selected),
        errors,
        len(findings),
    )
    return AnalysisResult(
        status=status,
        plugin=tool,
        message=f"{tool}: scan incomplete ({errors}/{len(selected)} paths failed); {len(findings)} findings retained"
        if errors
        else f"{tool}: {len(findings)} findings",
        findings=findings,
        details={
            "total": len(findings),
            "scanned_paths": [item["path"] for item in invocations if item["status"] == "ok"],
            "invocations": invocations,
            "errors": errors,
            "incomplete": bool(errors),
            "elapsed_seconds": elapsed,
        },
    )
