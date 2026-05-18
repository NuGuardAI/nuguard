"""Benchmark test harness — exercises the nuguard CLI against all fixture repos.

Each test:
  1. Materialises the fixture from cached_files.json into a temp directory
  2. Runs ``nuguard sbom generate`` against the temp directory
  3. For runnable fixtures (ones with a live app config): optionally launches the
     app via ``--launch`` and runs ``nuguard redteam``
  4. Writes output to tests/output/

Opt-in: set ``NUGUARD_REDTEAM_BENCHMARK=1`` in the environment to enable the
full suite (SBOM + redteam).  Without that flag, only the SBOM-generation tests
run, which do not require any API keys or running services.

LLM enrichment (SBOM + redteam) is enabled when any of the following env vars
are present: ``NUGUARD_REDTEAM_LLM_API_KEY``, ``LITELLM_API_KEY``,
``GEMINI_API_KEY``.

Usage::

    # SBOM generation only (no API keys needed)
    uv run pytest tests/benchmark/test_redteam_cli.py -v -s

    # Full benchmark — SBOM + redteam for all runnable fixtures
    NUGUARD_REDTEAM_BENCHMARK=1 OPENAI_API_KEY=sk-... \\
      NUGUARD_REDTEAM_LLM_API_KEY=... \\
      uv run pytest tests/benchmark/test_redteam_cli.py -v -s

    # Single fixture
    uv run pytest tests/benchmark/test_redteam_cli.py -v -s \\
      -k synthetic-simple
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

# AppRunner is used to start fixture apps that the SBOM cannot auto-discover
# (e.g. apps that use Streamlit or non-standard entry points).
try:
    from tests.redteam.app_runner import APP_CONFIGS, AppRunner
    _APP_RUNNER_AVAILABLE = True
except ImportError:
    _APP_RUNNER_AVAILABLE = False

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "tests" / "output"

# Fixtures that have a fully configured AppRunner entry in app_runner.py
# and can be started locally via --launch.  Maps fixture-dir-name → AppConfig
# fields needed by the CLI test.
_RUNNABLE_FIXTURES: dict[str, dict[str, Any]] = {
    "openai-cs-agents-demo": {
        "requirements_path": "python-backend/requirements.txt",
        "startup_cwd": "python-backend",
        "port": 18100,
        "required_env_vars": ["OPENAI_API_KEY"],
    },
    "rag-chatbot-demo": {
        "requirements_path": "backend/requirements.txt",
        "startup_cwd": "backend",
        "port": 18101,
        "required_env_vars": ["OPENAI_API_KEY"],
    },
    "real-estate-agent": {
        "requirements_path": "requirements.txt",
        "startup_cwd": "",
        "port": 18102,
        "required_env_vars": ["OPENAI_API_KEY"],
    },
    "voicelive-api-salescoach-demo": {
        "requirements_path": "backend/requirements.txt",
        "startup_cwd": "backend",
        "port": 18103,
        "required_env_vars": [],
    },
    "Healthcare-voice-agent": {
        "requirements_path": "backend/requirements.txt",
        "startup_cwd": "backend",
        "port": 18104,
        "required_env_vars": ["OPENAI_API_KEY"],
    },
}

_BENCHMARK_ENABLED = os.getenv("NUGUARD_REDTEAM_BENCHMARK") == "1"
_USE_LLM = bool(
    os.getenv("NUGUARD_REDTEAM_LLM_API_KEY")
    or os.getenv("LITELLM_API_KEY")
    or os.getenv("GEMINI_API_KEY")
)


def _all_fixture_names() -> list[str]:
    return sorted(
        d.name
        for d in FIXTURES_DIR.iterdir()
        if d.is_dir() and (d / "cached_files.json").exists()
    )


def _materialize_fixture(name: str) -> Path:
    """Write cached fixture files to a fresh temp directory."""
    cache_path = FIXTURES_DIR / name / "cached_files.json"
    payload: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8"))
    files: list[dict] = payload.get("files", [])
    temp_dir = Path(tempfile.mkdtemp(prefix=f"nuguard-bench-{name}-"))
    for entry in files:
        rel = str(entry.get("path", "")).strip()
        if not rel:
            continue
        target = temp_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(entry.get("content", "")), encoding="utf-8")
    return temp_dir


def _nuguard_cmd(*args: str) -> list[str]:
    """Build a nuguard CLI command.

    Prefer ``uv run nuguard`` when uv is available; otherwise invoke the CLI
    module directly with the current interpreter for local-dev environments.
    """
    if shutil.which("uv"):
        return ["uv", "run", "nuguard", *args]
    return [sys.executable, "-m", "nuguard.cli.main", *args]


def _run_nuguard(*args: str, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run a nuguard CLI command and return the completed process."""
    cmd = _nuguard_cmd(*args)
    return subprocess.run(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# SBOM generation tests — run for ALL 27 fixtures (no API key required)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", _all_fixture_names())
def test_sbom_generate_from_fixture(fixture_name: str, tmp_path: Path) -> None:
    """Generate an AI-SBOM from each benchmark fixture using the nuguard CLI.

    This test is always enabled — it requires no API keys and validates that:
    - ``nuguard sbom generate`` completes without error for every fixture
    - The output SBOM JSON is written and is non-empty
    - The SBOM contains at least one node (basic sanity)
    - App environment discovery (startup commands, URLs) runs without crashing
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = fixture_name.lower().replace(" ", "-")
    sbom_out = OUTPUT_DIR / f"sbom_{safe_name}.json"
    config_path = FIXTURES_DIR / fixture_name / "nuguard.yaml"

    source_dir = _materialize_fixture(fixture_name)
    try:
        cmd_args = [
            "sbom", "generate",
            "--source", str(source_dir),
            "--output", str(sbom_out),
        ]
        if _USE_LLM:
            cmd_args.append("--llm")
        if config_path.exists():
            cmd_args += ["--config", str(config_path)]

        result = _run_nuguard(*cmd_args, timeout=180)

        assert result.returncode == 0, (
            f"nuguard sbom generate failed for {fixture_name}\n"
            f"stdout:\n{result.stdout[-2000:]}\n"
            f"stderr:\n{result.stderr[-2000:]}"
        )
        assert sbom_out.exists(), f"SBOM output not written: {sbom_out}"
        assert sbom_out.stat().st_size > 100, f"SBOM output too small: {sbom_out}"

        sbom = json.loads(sbom_out.read_text(encoding="utf-8"))
        nodes = sbom.get("nodes", [])
        assert len(nodes) >= 1, (
            f"SBOM for {fixture_name} has no nodes — extraction likely failed"
        )

        # Log summary info (visible with -s / --capture=no)
        node_types = {}
        for n in nodes:
            ct = n.get("component_type", "UNKNOWN")
            node_types[ct] = node_types.get(ct, 0) + 1

        summary = sbom.get("summary") or {}
        startup_cmds = summary.get("startup_commands", [])
        local_url = summary.get("local_url")
        staging_urls = summary.get("staging_urls", [])
        prod_urls = summary.get("production_urls", [])

        print(
            f"\n[{fixture_name}] SBOM: {len(nodes)} nodes {dict(sorted(node_types.items()))}"
        )
        if startup_cmds:
            print(f"  startup_commands: {[c.get('command') for c in startup_cmds]}")
        if local_url:
            print(f"  local_url:   {local_url}")
        if staging_urls:
            print(f"  staging_urls: {staging_urls}")
        if prod_urls:
            print(f"  prod_urls:   {prod_urls}")

    finally:
        shutil.rmtree(source_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Redteam tests — opt-in via NUGUARD_REDTEAM_BENCHMARK=1
# ---------------------------------------------------------------------------

@pytest.mark.benchmark_redteam
@pytest.mark.parametrize(
    "fixture_name",
    [n for n in _all_fixture_names() if n in _RUNNABLE_FIXTURES],
)
def test_redteam_cli_runnable_fixture(fixture_name: str) -> None:
    """Run the full nuguard CLI pipeline (sbom generate + redteam) for each
    fixture that has a known app configuration.

    Launch strategy (in priority order):
      1. ``--launch`` — when the SBOM contains startup commands (auto-detected
         by app_env_detector during sbom generate).
      2. AppRunner — when startup commands are absent (e.g. Streamlit/non-standard
         entry points); the app is started via AppRunner and ``--target`` is passed.

    Requires ``NUGUARD_REDTEAM_BENCHMARK=1`` plus the fixture's required env
    vars (e.g. ``OPENAI_API_KEY``).
    """
    if not _BENCHMARK_ENABLED:
        pytest.skip("Set NUGUARD_REDTEAM_BENCHMARK=1 to run full redteam benchmarks")

    app_info = _RUNNABLE_FIXTURES[fixture_name]
    missing = [v for v in app_info.get("required_env_vars", []) if not os.getenv(v)]
    if missing:
        pytest.skip(f"Missing required env vars: {missing}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = fixture_name.lower().replace(" ", "-")
    sbom_out = OUTPUT_DIR / f"sbom_{safe_name}.json"
    findings_out = OUTPUT_DIR / f"redteam_{safe_name}_findings.json"
    config_path = FIXTURES_DIR / fixture_name / "nuguard.yaml"
    policy_path = FIXTURES_DIR / fixture_name / "cognitive_policy.md"

    source_dir = _materialize_fixture(fixture_name)
    start_time = time.monotonic()
    runner: "AppRunner | None" = None  # type: ignore[name-defined]

    try:
        # Step 1: Generate SBOM
        sbom_cmd = [
            "sbom", "generate",
            "--source", str(source_dir),
            "--output", str(sbom_out),
        ]
        if _USE_LLM:
            sbom_cmd.append("--llm")

        sbom_result = _run_nuguard(*sbom_cmd, timeout=180)
        assert sbom_result.returncode == 0, (
            f"SBOM generation failed for {fixture_name}\n"
            f"stderr:\n{sbom_result.stderr[-2000:]}"
        )
        assert sbom_out.exists(), f"SBOM not written: {sbom_out}"

        # Determine launch strategy from the generated SBOM
        sbom_data = json.loads(sbom_out.read_text())
        has_startup_cmds = bool(
            (sbom_data.get("summary") or {}).get("startup_commands")
        )

        # Step 2a: --launch (SBOM has startup commands)
        if has_startup_cmds:
            redteam_cmd = [
                "redteam",
                "--sbom", str(sbom_out),
                "--source", str(source_dir),
                "--launch",
                "--output", str(findings_out),
                "--format", "text",
                "--profile", "full",
                "--fail-on", "critical",
            ]
            if config_path.exists():
                redteam_cmd += ["--config", str(config_path)]
            if policy_path.exists():
                redteam_cmd += ["--policy", str(policy_path)]

            print(f"\n[{fixture_name}] Running redteam (--launch): {' '.join(redteam_cmd)}")
            redteam_result = _run_nuguard(*redteam_cmd, timeout=600)

        # Step 2b: AppRunner fallback (no startup commands in SBOM)
        else:
            if not _APP_RUNNER_AVAILABLE or fixture_name not in APP_CONFIGS:
                pytest.skip(
                    f"No startup commands in SBOM and AppRunner not available for {fixture_name}"
                )
            app_cfg = APP_CONFIGS[fixture_name]
            runner = AppRunner(app_cfg)
            runner.materialize()
            # Replace the materialized source with the one we already have
            runner._source_dir = source_dir  # type: ignore[attr-defined]
            runner._ensure_venv()
            runner._start_process()
            runner._wait_for_health()
            runner.started = True
            target_url = runner.base_url

            redteam_cmd = [
                "redteam",
                "--sbom", str(sbom_out),
                "--target", target_url,
                "--output", str(findings_out),
                "--format", "text",
                "--profile", "full",
                "--fail-on", "critical",
            ]
            if config_path.exists():
                redteam_cmd += ["--config", str(config_path)]
            if policy_path.exists():
                redteam_cmd += ["--policy", str(policy_path)]

            print(
                f"\n[{fixture_name}] Running redteam (AppRunner, target={target_url}): "
                f"{' '.join(redteam_cmd)}"
            )
            redteam_result = _run_nuguard(*redteam_cmd, timeout=600)

        duration = time.monotonic() - start_time
        print(f"[{fixture_name}] Redteam complete in {duration:.0f}s")
        print(f"  return code: {redteam_result.returncode}")
        if redteam_result.stdout:
            print(f"  stdout (tail):\n{redteam_result.stdout[-3000:]}")

        # rc=0 → no findings at/above --fail-on threshold
        # rc=2 → findings at/above threshold (expected; test should still pass)
        # rc=1 → hard error (fail the test)
        assert redteam_result.returncode in (0, 2), (
            f"nuguard redteam errored (rc={redteam_result.returncode}) for {fixture_name}\n"
            f"stdout:\n{redteam_result.stdout[-3000:]}\n"
            f"stderr:\n{redteam_result.stderr[-2000:]}"
        )

        if findings_out.exists():
            findings = json.loads(findings_out.read_text())
            severity_counts: dict[str, int] = {}
            for f in findings:
                sev = f.get("severity", "unknown")
                severity_counts[sev] = severity_counts.get(sev, 0) + 1
            print(f"  findings: {severity_counts}")

    finally:
        if runner is not None:
            runner._kill_process()
        shutil.rmtree(source_dir, ignore_errors=True)


@pytest.mark.benchmark_redteam
@pytest.mark.parametrize(
    "fixture_name",
    [n for n in _all_fixture_names() if n not in _RUNNABLE_FIXTURES],
)
def test_redteam_cli_target_url_fixture(fixture_name: str) -> None:
    """Run ``nuguard redteam`` for fixtures that have an explicit ``target_url``
    in their nuguard.yaml (remote/staging endpoints).

    Skipped when:
    - ``NUGUARD_REDTEAM_BENCHMARK=1`` is not set
    - The fixture's nuguard.yaml has no ``target_url``
    - Required env vars are missing
    """
    if not _BENCHMARK_ENABLED:
        pytest.skip("Set NUGUARD_REDTEAM_BENCHMARK=1 to run full redteam benchmarks")

    config_path = FIXTURES_DIR / fixture_name / "nuguard.yaml"
    if not config_path.exists():
        pytest.skip(f"No nuguard.yaml for {fixture_name}")

    # Check if the config has a target_url
    config_text = config_path.read_text()
    if "target_url:" not in config_text:
        pytest.skip(f"No target_url configured for {fixture_name}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = fixture_name.lower().replace(" ", "-")
    sbom_out = OUTPUT_DIR / f"sbom_{safe_name}.json"
    findings_out = OUTPUT_DIR / f"redteam_{safe_name}_findings.json"
    policy_path = FIXTURES_DIR / fixture_name / "cognitive_policy.md"

    # Reuse existing SBOM if present (avoid re-generating)
    if not sbom_out.exists():
        source_dir = _materialize_fixture(fixture_name)
        try:
            sbom_cmd = [
                "sbom", "generate",
                "--source", str(source_dir),
                "--output", str(sbom_out),
            ]
            if _USE_LLM:
                sbom_cmd.append("--llm")
            result = _run_nuguard(*sbom_cmd, timeout=180)
            assert result.returncode == 0, f"SBOM gen failed: {result.stderr[-1000:]}"
        finally:
            shutil.rmtree(source_dir, ignore_errors=True)

    redteam_cmd = [
        "redteam",
        "--sbom", str(sbom_out),
        "--config", str(config_path),
        "--output", str(findings_out),
        "--format", "text",
        "--profile", "full",
        "--fail-on", "critical",
    ]
    if policy_path.exists():
        redteam_cmd += ["--policy", str(policy_path)]

    print(f"\n[{fixture_name}] Running redteam against target_url from config")
    result = _run_nuguard(*redteam_cmd, timeout=600)

    print(f"  return code: {result.returncode}")
    if result.stdout:
        print(f"  stdout (tail):\n{result.stdout[-3000:]}")

    assert result.returncode in (0, 2), (
        f"nuguard redteam errored (rc={result.returncode}) for {fixture_name}\n"
        f"stdout:\n{result.stdout[-3000:]}\nstderr:\n{result.stderr[-2000:]}"
    )
