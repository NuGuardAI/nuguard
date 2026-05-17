"""Unit tests for nuguard.mcp._runner — subprocess execution helper.

All subprocess calls are mocked; no nuguard binary is required.
"""

from __future__ import annotations

import asyncio
import json
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nuguard.mcp._runner import RunResult, exit_code_to_status, run_nuguard_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.send_signal = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# exit_code_to_status
# ---------------------------------------------------------------------------


def test_status_ok() -> None:
    assert exit_code_to_status(0) == "ok"


def test_status_findings() -> None:
    assert exit_code_to_status(1) == "findings"


def test_status_critical() -> None:
    assert exit_code_to_status(2) == "critical"


def test_status_error_on_unknown() -> None:
    assert exit_code_to_status(3) == "error"
    assert exit_code_to_status(99) == "error"


def test_status_timeout_overrides_exit_code() -> None:
    assert exit_code_to_status(0, timed_out=True) == "timeout"
    assert exit_code_to_status(1, timed_out=True) == "timeout"


# ---------------------------------------------------------------------------
# run_nuguard_command — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_success_json() -> None:
    payload = {"total": 3, "severity_counts": {"high": 1}}
    proc = _make_proc(stdout=json.dumps(payload).encode())

    with patch("nuguard.mcp._runner.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = proc
        result = await run_nuguard_command(["analyze", "--sbom", "a.json"], expect_json=True)

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.parsed_json == payload
    assert result.stdout_text == json.dumps(payload)


@pytest.mark.asyncio
async def test_run_passes_nuguard_as_executable() -> None:
    proc = _make_proc()

    with patch("nuguard.mcp._runner.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = proc
        await run_nuguard_command(["sbom", "generate"])

    call_args = mock_exec.call_args
    assert call_args.args[0] == "nuguard"
    assert call_args.args[1] == "sbom"
    assert call_args.args[2] == "generate"


@pytest.mark.asyncio
async def test_run_passes_cwd() -> None:
    proc = _make_proc()

    with patch("nuguard.mcp._runner.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = proc
        await run_nuguard_command(["init"], cwd="/tmp/myproject")

    assert mock_exec.call_args.kwargs["cwd"] == "/tmp/myproject"


@pytest.mark.asyncio
async def test_run_no_json_when_expect_json_false() -> None:
    proc = _make_proc(stdout=b"created  ./nuguard.yaml\n")

    with patch("nuguard.mcp._runner.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = proc
        result = await run_nuguard_command(["init"], expect_json=False)

    assert result.parsed_json is None
    assert "nuguard.yaml" in result.stdout_text


# ---------------------------------------------------------------------------
# ANSI stripping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ansi_codes_stripped_from_stderr() -> None:
    raw_stderr = b"\x1b[31mError:\x1b[0m something bad"
    proc = _make_proc(stdout=b"{}", stderr=raw_stderr, returncode=3)

    with patch("nuguard.mcp._runner.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = proc
        result = await run_nuguard_command(["analyze", "--sbom", "x.json"], expect_json=True)

    assert "\x1b" not in result.stderr_text
    assert "Error:" in result.stderr_text
    assert "something bad" in result.stderr_text


# ---------------------------------------------------------------------------
# JSON parse failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_invalid_json_yields_none_parsed() -> None:
    proc = _make_proc(stdout=b"not json at all\nsome output")

    with patch("nuguard.mcp._runner.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = proc
        result = await run_nuguard_command(["analyze", "--sbom", "x.json"], expect_json=True)

    assert result.parsed_json is None
    assert result.stdout_text == "not json at all\nsome output"


# ---------------------------------------------------------------------------
# Exit code mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_code_1_is_findings() -> None:
    proc = _make_proc(stdout=b'{"findings": []}', returncode=1)

    with patch("nuguard.mcp._runner.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = proc
        result = await run_nuguard_command(["analyze", "--sbom", "x.json"], expect_json=True)

    assert result.exit_code == 1
    assert exit_code_to_status(result.exit_code) == "findings"


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_sends_sigterm_and_sets_flag() -> None:
    async def _slow_communicate():
        await asyncio.sleep(100)
        return b"", b""

    proc = MagicMock()
    proc.returncode = None
    proc.communicate = _slow_communicate
    proc.send_signal = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.kill = MagicMock()

    with patch("nuguard.mcp._runner.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = proc
        result = await run_nuguard_command(["redteam", "--config", "c.yaml"], timeout=0.05)

    assert result.timed_out is True
    proc.send_signal.assert_called_once_with(signal.SIGTERM)
