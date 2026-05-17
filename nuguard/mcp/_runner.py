"""Async subprocess runner for NuGuard CLI commands."""

from __future__ import annotations

import asyncio
import json
import re
import signal
from dataclasses import dataclass, field
from pathlib import Path

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")

_STATUS_MAP = {0: "ok", 1: "findings", 2: "critical"}


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def exit_code_to_status(exit_code: int, timed_out: bool = False) -> str:
    if timed_out:
        return "timeout"
    return _STATUS_MAP.get(exit_code, "error")


@dataclass
class RunResult:
    exit_code: int
    stdout_text: str
    stderr_text: str
    timed_out: bool
    parsed_json: dict | list | None = field(default=None)


async def run_nuguard_command(
    args: list[str],
    cwd: str | None = None,
    timeout: float = 120.0,
    expect_json: bool = True,
) -> RunResult:
    """Run ``nuguard <args>`` as a subprocess and return a RunResult.

    Uses asyncio.create_subprocess_exec so the MCP server's event loop is not
    blocked. Kills the process on timeout (SIGTERM → 2 s → SIGKILL).
    """
    proc = await asyncio.create_subprocess_exec(
        "nuguard",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        stdout_bytes = b""
        stderr_bytes = b""

    exit_code = proc.returncode if proc.returncode is not None else 1
    stdout_text = _strip_ansi(stdout_bytes.decode("utf-8", errors="replace"))
    stderr_text = _strip_ansi(stderr_bytes.decode("utf-8", errors="replace"))

    parsed_json: dict | list | None = None
    if expect_json and stdout_text.strip():
        try:
            parsed_json = json.loads(stdout_text)
        except json.JSONDecodeError:
            pass

    return RunResult(
        exit_code=exit_code,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
        timed_out=timed_out,
        parsed_json=parsed_json,
    )


def resolve_path(p: str) -> Path:
    """Resolve a user-supplied path string to an absolute Path."""
    return Path(p).expanduser().resolve()


def cwd_for_config(config_path: str | None) -> str | None:
    """Return the directory containing config_path, or None."""
    if config_path:
        return str(resolve_path(config_path).parent)
    return None
