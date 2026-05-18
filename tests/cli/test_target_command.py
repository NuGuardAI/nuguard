"""CLI tests for ``nuguard target verify``."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()

TARGET = "http://app.test"
ENDPOINT = "/chat"
FULL_URL = f"{TARGET}{ENDPOINT}"


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_canary(tmp_path: Path, *tokens: str) -> Path:
    tenants = [{"tenant_id": f"t{i}", "session_token": tok} for i, tok in enumerate(tokens)]
    data = {"tenants": tenants, "global_watch_values": []}
    p = tmp_path / "canary.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ── happy-path tests ──────────────────────────────────────────────────────────


@respx.mock
def test_verify_ok_bearer() -> None:
    respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    result = runner.invoke(
        app,
        [
            "target",
            "verify",
            "--target",
            TARGET,
            "--endpoint",
            ENDPOINT,
            "--auth-header",
            "Authorization: Bearer tok",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ok" in result.output


@respx.mock
def test_verify_no_target_exits_1(tmp_path: Path, monkeypatch) -> None:
    # No --target and no config file with redteam.target
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["target", "verify"])
    assert result.exit_code == 1
    assert "redteam.target" in result.output


# ── failure tests ─────────────────────────────────────────────────────────────


@respx.mock
def test_verify_auth_failed() -> None:
    respx.post(FULL_URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
    result = runner.invoke(
        app,
        ["target", "verify", "--target", TARGET, "--endpoint", ENDPOINT],
    )
    assert result.exit_code == 1
    assert "auth_failed" in result.output


@respx.mock
def test_verify_target_unavailable() -> None:
    respx.post(FULL_URL).mock(side_effect=httpx.ConnectError("refused"))
    result = runner.invoke(
        app,
        ["target", "verify", "--target", TARGET, "--endpoint", ENDPOINT],
    )
    assert result.exit_code == 2
    assert "unavailable" in result.output.lower()


# ── canary tenant tests ───────────────────────────────────────────────────────


@respx.mock
def test_verify_with_canary_tenants(tmp_path: Path) -> None:
    canary_path = _make_canary(tmp_path, "tok-tenant-1", "tok-tenant-2")
    respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    result = runner.invoke(
        app,
        [
            "target",
            "verify",
            "--target",
            TARGET,
            "--endpoint",
            ENDPOINT,
            "--canary",
            str(canary_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ok" in result.output


def test_verify_canary_file_not_found_warns(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-canary.json"
    with respx.mock:
        respx.post(FULL_URL).mock(return_value=httpx.Response(200))
        result = runner.invoke(
            app,
            [
                "target",
                "verify",
                "--target",
                TARGET,
                "--endpoint",
                ENDPOINT,
                "--canary",
                str(missing),
            ],
        )
    # Should warn but still proceed with default credential
    assert "Warning" in result.output or "warning" in result.output.lower()
    assert result.exit_code == 0, result.output


# ── --mode tests — removed (legacy mode selection no longer supported) ──────


def test_verify_no_mode_option() -> None:
    # --mode is no longer a supported option
    result = runner.invoke(app, ["target", "verify", "--mode", "bogus"])
    assert result.exit_code != 0
