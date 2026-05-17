"""Unit tests for nuguard.mcp.server tools.

run_nuguard_command is patched so no real nuguard process is launched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nuguard.mcp._runner import RunResult
from nuguard.mcp.server import (
    nuguard_analyze,
    nuguard_behavior,
    nuguard_init,
    nuguard_policy_check,
    nuguard_redteam,
    nuguard_sbom_generate,
    nuguard_scan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RUNNER = "nuguard.mcp.server.run_nuguard_command"


def _ok(stdout: str = "", parsed: dict | None = None) -> RunResult:
    return RunResult(
        exit_code=0,
        stdout_text=stdout,
        stderr_text="",
        timed_out=False,
        parsed_json=parsed,
    )


def _findings(parsed: dict | None = None) -> RunResult:
    return RunResult(
        exit_code=1,
        stdout_text=json.dumps(parsed or {}),
        stderr_text="",
        timed_out=False,
        parsed_json=parsed,
    )


def _error() -> RunResult:
    return RunResult(
        exit_code=3,
        stdout_text="",
        stderr_text="Internal error",
        timed_out=False,
        parsed_json=None,
    )


def _timeout() -> RunResult:
    return RunResult(
        exit_code=1,
        stdout_text="",
        stderr_text="",
        timed_out=True,
        parsed_json=None,
    )


# ---------------------------------------------------------------------------
# nuguard_init
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_ok_parses_created_files(tmp_path: Path) -> None:
    stdout = "  created  nuguard.yaml\n  created  canary.example.json\n"
    mock = AsyncMock(return_value=_ok(stdout=stdout))

    with patch(_RUNNER, mock):
        result = await nuguard_init(project_dir=str(tmp_path))

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert "nuguard.yaml" in result["created_files"]
    assert "canary.example.json" in result["created_files"]
    assert result["skipped_files"] == []


@pytest.mark.asyncio
async def test_init_skipped_files_parsed(tmp_path: Path) -> None:
    stdout = "  skipped  nuguard.yaml  (already exists — use --force to overwrite)\n"
    mock = AsyncMock(return_value=_ok(stdout=stdout))

    with patch(_RUNNER, mock):
        result = await nuguard_init(project_dir=str(tmp_path))

    assert "nuguard.yaml" in result["skipped_files"]
    assert result["created_files"] == []


@pytest.mark.asyncio
async def test_init_force_flag_added(tmp_path: Path) -> None:
    mock = AsyncMock(return_value=_ok())

    with patch(_RUNNER, mock):
        await nuguard_init(project_dir=str(tmp_path), force=True)

    args = mock.call_args.args[0]
    assert "--force" in args


@pytest.mark.asyncio
async def test_init_target_url_added(tmp_path: Path) -> None:
    mock = AsyncMock(return_value=_ok())

    with patch(_RUNNER, mock):
        await nuguard_init(project_dir=str(tmp_path), target_url="http://localhost:9000")

    args = mock.call_args.args[0]
    assert "--target" in args
    assert "http://localhost:9000" in args


@pytest.mark.asyncio
async def test_init_timeout_propagated(tmp_path: Path) -> None:
    mock = AsyncMock(return_value=_ok())

    with patch(_RUNNER, mock):
        await nuguard_init(project_dir=str(tmp_path), timeout_seconds=99)

    assert mock.call_args.kwargs["timeout"] == 99


# ---------------------------------------------------------------------------
# nuguard_sbom_generate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sbom_generate_reads_output_file(tmp_path: Path) -> None:
    sbom_data = {
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [{"source": "a", "target": "b"}],
        "summary": {"frameworks": ["LangChain"]},
    }
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(json.dumps(sbom_data))

    mock = AsyncMock(return_value=_ok())

    with patch(_RUNNER, mock):
        result = await nuguard_sbom_generate(
            source=str(tmp_path),
            output=str(sbom_path),
        )

    assert result["node_count"] == 2
    assert result["edge_count"] == 1
    assert result["sbom_summary"] == {"frameworks": ["LangChain"]}
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_sbom_generate_from_repo_args(tmp_path: Path) -> None:
    mock = AsyncMock(return_value=_ok())

    with patch(_RUNNER, mock):
        await nuguard_sbom_generate(
            from_repo="https://github.com/org/repo",
            ref="develop",
            output=str(tmp_path / "out.json"),
        )

    args = mock.call_args.args[0]
    assert "--from-repo" in args
    assert "https://github.com/org/repo" in args
    assert "--ref" in args
    assert "develop" in args


@pytest.mark.asyncio
async def test_sbom_generate_llm_flag(tmp_path: Path) -> None:
    mock = AsyncMock(return_value=_ok())

    with patch(_RUNNER, mock):
        await nuguard_sbom_generate(source=str(tmp_path), llm=True)

    args = mock.call_args.args[0]
    assert "--llm" in args


# ---------------------------------------------------------------------------
# nuguard_analyze
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_injects_status_and_exit_code(tmp_path: Path) -> None:
    sbom = tmp_path / "app.sbom.json"
    sbom.write_text("{}")
    parsed = {"total": 2, "findings": [{"severity": "high"}]}
    mock = AsyncMock(return_value=_findings(parsed=parsed))

    with patch(_RUNNER, mock):
        result = await nuguard_analyze(sbom=str(sbom))

    assert result["status"] == "findings"
    assert result["exit_code"] == 1
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_analyze_disabled_tools_add_no_flags(tmp_path: Path) -> None:
    sbom = tmp_path / "app.sbom.json"
    sbom.write_text("{}")
    mock = AsyncMock(return_value=_ok(parsed={}))

    with patch(_RUNNER, mock):
        await nuguard_analyze(
            sbom=str(sbom),
            enable_atlas=False,
            enable_osv=False,
            enable_grype=False,
        )

    args = mock.call_args.args[0]
    assert "--no-atlas" in args
    assert "--no-osv" in args
    assert "--no-grype" in args
    assert "--no-checkov" not in args


@pytest.mark.asyncio
async def test_analyze_nga_only_flag(tmp_path: Path) -> None:
    sbom = tmp_path / "app.sbom.json"
    sbom.write_text("{}")
    mock = AsyncMock(return_value=_ok(parsed={}))

    with patch(_RUNNER, mock):
        await nuguard_analyze(sbom=str(sbom), nga_only=True)

    args = mock.call_args.args[0]
    assert "--nga" in args


@pytest.mark.asyncio
async def test_analyze_raw_output_on_parse_failure(tmp_path: Path) -> None:
    sbom = tmp_path / "app.sbom.json"
    sbom.write_text("{}")
    mock = AsyncMock(
        return_value=RunResult(0, "plain text output", "", False, None)
    )

    with patch(_RUNNER, mock):
        result = await nuguard_analyze(sbom=str(sbom))

    assert result["raw_output"] == "plain text output"
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# nuguard_scan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_reads_findings_json(tmp_path: Path) -> None:
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    findings_data = {
        "total": 5,
        "severity_counts": {"high": 2, "medium": 3},
    }
    (out_dir / "findings.json").write_text(json.dumps(findings_data))

    mock = AsyncMock(return_value=_ok())

    with patch(_RUNNER, mock):
        result = await nuguard_scan(
            source=str(tmp_path),
            output_dir=str(out_dir),
        )

    assert result["summary"]["total"] == 5
    assert result["summary"]["high"] == 2


@pytest.mark.asyncio
async def test_scan_artifacts_map_includes_existing_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    (out_dir / "sbom.json").write_text("{}")
    (out_dir / "report.md").write_text("# Report")

    mock = AsyncMock(return_value=_ok())

    with patch(_RUNNER, mock):
        result = await nuguard_scan(source=str(tmp_path), output_dir=str(out_dir))

    assert "sbom" in result["artifacts"]
    assert "report_md" in result["artifacts"]
    assert "findings_sarif" not in result["artifacts"]  # file not written


@pytest.mark.asyncio
async def test_scan_optional_flags(tmp_path: Path) -> None:
    mock = AsyncMock(return_value=_ok())

    with patch(_RUNNER, mock):
        await nuguard_scan(
            source=str(tmp_path),
            policy="/tmp/policy.md",
            target="http://app:8000",
            steps="sbom,analyze,redteam",
            llm=True,
        )

    args = mock.call_args.args[0]
    assert "--policy" in args
    assert "--target" in args
    assert "http://app:8000" in args
    assert "--steps" in args
    assert "sbom,analyze,redteam" in args
    assert "--llm" in args


# ---------------------------------------------------------------------------
# nuguard_behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_behavior_requires_config() -> None:
    # With no config_path and no NUGUARD_DEFAULT_CONFIG env var, returns error.
    env = {k: v for k, v in os.environ.items() if k != "NUGUARD_DEFAULT_CONFIG"}
    with patch.dict(os.environ, env, clear=True):
        with patch("nuguard.mcp.server._DEFAULT_CONFIG", ""):
            result = await nuguard_behavior(config_path="")

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_behavior_passes_mode(tmp_path: Path) -> None:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text("")
    mock = AsyncMock(return_value=_ok(parsed={}))

    with patch(_RUNNER, mock):
        await nuguard_behavior(config_path=str(cfg), mode="static")

    args = mock.call_args.args[0]
    assert "--mode" in args
    assert "static" in args


# ---------------------------------------------------------------------------
# nuguard_redteam
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redteam_guided_true_adds_flag(tmp_path: Path) -> None:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text("")
    mock = AsyncMock(return_value=_ok(parsed={}))

    with patch(_RUNNER, mock):
        await nuguard_redteam(config_path=str(cfg), guided=True)

    args = mock.call_args.args[0]
    assert "--guided" in args
    assert "--no-guided" not in args


@pytest.mark.asyncio
async def test_redteam_guided_false_adds_no_guided_flag(tmp_path: Path) -> None:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text("")
    mock = AsyncMock(return_value=_ok(parsed={}))

    with patch(_RUNNER, mock):
        await nuguard_redteam(config_path=str(cfg), guided=False)

    args = mock.call_args.args[0]
    assert "--no-guided" in args


@pytest.mark.asyncio
async def test_redteam_injects_summary(tmp_path: Path) -> None:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text("")
    findings = [
        {"severity": "high", "title": "Prompt injection"},
        {"severity": "high", "title": "Data leak"},
        {"severity": "medium", "title": "Policy bypass"},
    ]
    parsed = {"findings": findings}
    mock = AsyncMock(return_value=_findings(parsed=parsed))

    with patch(_RUNNER, mock):
        result = await nuguard_redteam(config_path=str(cfg))

    assert result["summary"]["total"] == 3
    assert result["summary"]["high"] == 2
    assert result["summary"]["medium"] == 1


@pytest.mark.asyncio
async def test_redteam_profile_flag(tmp_path: Path) -> None:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text("")
    mock = AsyncMock(return_value=_ok(parsed={}))

    with patch(_RUNNER, mock):
        await nuguard_redteam(config_path=str(cfg), profile="full")

    args = mock.call_args.args[0]
    assert "--profile" in args
    assert "full" in args


@pytest.mark.asyncio
async def test_redteam_timeout_status(tmp_path: Path) -> None:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text("")
    mock = AsyncMock(return_value=_timeout())

    with patch(_RUNNER, mock):
        result = await nuguard_redteam(config_path=str(cfg))

    assert result["status"] == "timeout"
    assert result["timed_out"] is True


# ---------------------------------------------------------------------------
# nuguard_policy_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_check_framework_flag() -> None:
    mock = AsyncMock(return_value=_ok(parsed={"controls": []}))

    with patch(_RUNNER, mock):
        await nuguard_policy_check(
            policy="/tmp/policy.md",
            framework="owasp-llm-top10",
        )

    args = mock.call_args.args[0]
    assert "--framework" in args
    assert "owasp-llm-top10" in args


@pytest.mark.asyncio
async def test_policy_check_verbose_flag() -> None:
    mock = AsyncMock(return_value=_ok(parsed={}))

    with patch(_RUNNER, mock):
        await nuguard_policy_check(policy="/tmp/p.md", verbose=True)

    args = mock.call_args.args[0]
    assert "--verbose" in args


@pytest.mark.asyncio
async def test_policy_check_raw_fallback() -> None:
    mock = AsyncMock(return_value=RunResult(0, "No gaps found.", "", False, None))

    with patch(_RUNNER, mock):
        result = await nuguard_policy_check()

    assert result["raw_output"] == "No gaps found."
    assert result["status"] == "ok"
