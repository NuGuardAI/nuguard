"""E2E analyze benchmark — ``nuguard sbom generate`` + ``nuguard analyze``.

Tests the full static analysis pipeline against the openai-cs-agents-demo
fixture without requiring any running app, API keys, or external scanners.

Two modes
---------
Default (always runs):
    Uses the pre-cached fixture under ``tests/benchmark/fixtures/``.  No
    network access required.

Fresh clone (opt-in via ``NUGUARD_ANALYZE_E2E=1``):
    Clones ``https://github.com/NuGuardAI/openai-cs-agents-demo`` at the
    pinned ref, then runs the full pipeline.  Requires network access and
    optionally ``GH_TOKEN`` / ``GITHUB_TOKEN`` for private repos.

Usage::

    # Default — cached fixture, always available
    uv run pytest tests/benchmark/test_analyze_e2e.py -v -s

    # Fresh clone from GitHub
    NUGUARD_ANALYZE_E2E=1 \\
      uv run pytest tests/benchmark/test_analyze_e2e.py -v -s
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

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
OUTPUT_DIR = REPO_ROOT / "tests" / "output"

FIXTURE_NAME = "openai-cs-agents-demo"
REPO_URL = "https://github.com/NuGuardAI/openai-cs-agents-demo"
REPO_REF = "main"

_E2E_ENABLED = os.getenv("NUGUARD_ANALYZE_E2E") == "1"

# ---------------------------------------------------------------------------
# Helpers (mirrors test_redteam_cli.py style)
# ---------------------------------------------------------------------------


def _materialize_fixture(name: str) -> Path:
    """Write cached fixture files to a fresh temp directory."""
    cache_path = FIXTURES_DIR / name / "cached_files.json"
    payload: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8"))
    temp_dir = Path(tempfile.mkdtemp(prefix=f"nuguard-analyze-bench-{name}-"))
    for entry in payload.get("files", []):
        rel = str(entry.get("path", "")).strip()
        if not rel:
            continue
        target = temp_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(entry.get("content", "")), encoding="utf-8")
    return temp_dir


def _run_nuguard(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    cmd = (
        ["uv", "run", "nuguard", *args]
        if shutil.which("uv")
        else [sys.executable, "-m", "nuguard.cli.main", *args]
    )
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        sev = str(f.get("severity", "unknown")).lower()
        counts[sev] = counts.get(sev, 0) + 1
    return counts


_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _sev_sort_key(f: dict[str, Any]) -> int:
    return _SEV_RANK.get(str(f.get("severity", "info")).lower(), 99)


def _print_findings_table(findings: list[dict[str, Any]]) -> None:
    """Print a compact findings table to stdout (visible with -s)."""
    col_w = [20, 8, 50]
    header = f"{'Finding ID':<{col_w[0]}}  {'Severity':<{col_w[1]}}  {'Title'}"
    print(f"\n  {header}")
    print(f"  {'-' * sum(col_w)}")
    for f in sorted(findings, key=_sev_sort_key):
        fid = str(f.get("finding_id", ""))[:col_w[0]]
        sev = str(f.get("severity", ""))[:col_w[1]]
        title = str(f.get("title", ""))[:col_w[2]]
        print(f"  {fid:<{col_w[0]}}  {sev:<{col_w[1]}}  {title}")


# ---------------------------------------------------------------------------
# Core test: cached fixture (always runs)
# ---------------------------------------------------------------------------


def test_analyze_e2e_cached_fixture(tmp_path: Path) -> None:
    """Full SBOM + analyze pipeline against the pre-cached openai-cs-agents-demo fixture.

    Validates:
    - ``nuguard sbom generate`` succeeds and produces a non-empty SBOM
    - ``nuguard analyze`` succeeds (exit 0 = clean, 1 = findings — both valid)
    - Output is valid JSON with the expected structure
    - At least one NGA finding is returned (the app has known security issues)
    - Findings have required fields (finding_id, severity, title, description)
    - Critical or high findings reference ATLAS techniques
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sbom_out = OUTPUT_DIR / "sbom_openai-cs-agents-demo.json"
    analysis_out = OUTPUT_DIR / "analyze_openai-cs-agents-demo.json"

    source_dir = _materialize_fixture(FIXTURE_NAME)
    t0 = time.monotonic()

    try:
        # ── Step 1: SBOM generation ──────────────────────────────────────────
        print(f"\n[{FIXTURE_NAME}] Step 1: nuguard sbom generate …")
        sbom_result = _run_nuguard(
            "sbom", "generate",
            "--source", str(source_dir),
            "--output", str(sbom_out),
            timeout=180,
        )
        assert sbom_result.returncode == 0, (
            f"nuguard sbom generate failed (rc={sbom_result.returncode})\n"
            f"stdout:\n{sbom_result.stdout[-2000:]}\n"
            f"stderr:\n{sbom_result.stderr[-2000:]}"
        )
        assert sbom_out.exists() and sbom_out.stat().st_size > 100

        sbom = json.loads(sbom_out.read_text())
        nodes = sbom.get("nodes", [])
        assert len(nodes) >= 1, "SBOM has no nodes — extraction failed"

        node_type_counts: dict[str, int] = {}
        for n in nodes:
            ct = n.get("component_type", "UNKNOWN")
            node_type_counts[ct] = node_type_counts.get(ct, 0) + 1
        print(f"  SBOM: {len(nodes)} nodes — {dict(sorted(node_type_counts.items()))}")

        # ── Step 2: Static analysis (NGA + ATLAS, offline only) ─────────────
        print(f"[{FIXTURE_NAME}] Step 2: nuguard analyze …")
        analyze_result = _run_nuguard(
            "analyze",
            "--sbom", str(sbom_out),
            "--format", "json",
            "--min-severity", "low",
            "--source", str(source_dir),
            "--no-osv",      # skip network call for deterministic test
            "--no-grype",    # requires grype binary
            "--no-checkov",  # requires checkov binary
            "--no-trivy",    # requires trivy binary
            "--no-semgrep",  # requires semgrep binary
            "--output", str(analysis_out),
            timeout=120,
        )

        # rc=0 → no findings at/above threshold, rc=1 → findings found (both valid)
        assert analyze_result.returncode in (0, 1), (
            f"nuguard analyze errored (rc={analyze_result.returncode})\n"
            f"stdout:\n{analyze_result.stdout[-3000:]}\n"
            f"stderr:\n{analyze_result.stderr[-2000:]}"
        )
        assert analysis_out.exists(), "Analysis output file was not written"

        # ── Parse and validate output ────────────────────────────────────────
        analysis_data = json.loads(analysis_out.read_text())
        assert "findings" in analysis_data, "Output missing 'findings' key"
        assert "total" in analysis_data, "Output missing 'total' key"

        findings: list[dict[str, Any]] = analysis_data["findings"]
        total: int = analysis_data["total"]
        assert total == len(findings), "total count mismatch"

        # ── Structural assertions on each finding ────────────────────────────
        for f in findings:
            assert f.get("finding_id"), f"Finding missing finding_id: {f}"
            assert f.get("severity"), f"Finding missing severity: {f}"
            assert f.get("title"), f"Finding missing title: {f}"
            assert f.get("description"), f"Finding missing description: {f}"
            assert str(f["severity"]).lower() in (
                "critical", "high", "medium", "low", "info"
            ), f"Unknown severity: {f['severity']}"

        # ── At least one finding expected (real app with known issues) ───────
        assert len(findings) >= 1, (
            "Expected at least one NGA finding for openai-cs-agents-demo "
            "(external LLM calls, missing guardrails, etc.) — got 0"
        )

        # ── High/critical findings should include ATLAS annotation ───────────
        high_critical = [
            f for f in findings
            if str(f.get("severity", "")).lower() in ("critical", "high")
        ]
        atlas_annotated = [
            f for f in high_critical
            if f.get("mitre_atlas_technique")
        ]
        if high_critical:
            assert len(atlas_annotated) >= 1, (
                f"Expected at least one high/critical finding with ATLAS annotation, "
                f"got {len(high_critical)} high/critical findings but none annotated"
            )

        # ── Summary output ───────────────────────────────────────────────────
        elapsed = time.monotonic() - t0
        sev_counts = _severity_counts(findings)
        print(
            f"  Analysis: {total} finding(s) in {elapsed:.1f}s — "
            + ", ".join(f"{s}={c}" for s, c in sorted(sev_counts.items()))
        )
        _print_findings_table(findings)

    finally:
        shutil.rmtree(source_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Fresh-clone test: opt-in via NUGUARD_ANALYZE_E2E=1
# ---------------------------------------------------------------------------


@pytest.mark.analyze_e2e
def test_analyze_e2e_from_github(tmp_path: Path) -> None:
    """Clone openai-cs-agents-demo from GitHub and run the full analysis pipeline.

    Opt-in: set ``NUGUARD_ANALYZE_E2E=1``.  Requires network access.
    A GitHub token (``GH_TOKEN`` / ``GITHUB_TOKEN``) is needed for private repos.
    """
    if not _E2E_ENABLED:
        pytest.skip("Set NUGUARD_ANALYZE_E2E=1 to run fresh-clone E2E analyze tests")

    sbom_out = tmp_path / "sbom.json"
    analysis_out = tmp_path / "analysis.json"

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    print(f"\n[{FIXTURE_NAME}] Cloning {REPO_URL} @ {REPO_REF} …")
    t0 = time.monotonic()

    # ── Step 1: SBOM from remote repo ───────────────────────────────────────
    sbom_cmd = [
        "sbom", "generate",
        "--from-repo", REPO_URL,
        "--ref", REPO_REF,
        "--output", str(sbom_out),
    ]
    if token:
        sbom_cmd += ["--token", token]

    sbom_result = _run_nuguard(*sbom_cmd, timeout=300)
    assert sbom_result.returncode == 0, (
        f"nuguard sbom generate failed (rc={sbom_result.returncode})\n"
        f"stdout:\n{sbom_result.stdout[-2000:]}\n"
        f"stderr:\n{sbom_result.stderr[-2000:]}"
    )
    assert sbom_out.exists() and sbom_out.stat().st_size > 100

    sbom = json.loads(sbom_out.read_text())
    nodes = sbom.get("nodes", [])
    assert len(nodes) >= 1, "SBOM has no nodes"
    print(f"  SBOM: {len(nodes)} nodes in {time.monotonic() - t0:.1f}s")

    # ── Step 2: Full analysis (all offline detectors) ────────────────────────
    print(f"[{FIXTURE_NAME}] Running nuguard analyze …")
    analyze_result = _run_nuguard(
        "analyze",
        "--sbom", str(sbom_out),
        "--format", "json",
        "--min-severity", "low",
        "--no-osv",
        "--no-grype",
        "--no-checkov",
        "--no-trivy",
        "--no-semgrep",
        "--output", str(analysis_out),
        timeout=120,
    )
    assert analyze_result.returncode in (0, 1), (
        f"nuguard analyze errored (rc={analyze_result.returncode})\n"
        f"stdout:\n{analyze_result.stdout[-3000:]}\n"
        f"stderr:\n{analyze_result.stderr[-2000:]}"
    )
    assert analysis_out.exists()

    analysis_data = json.loads(analysis_out.read_text())
    findings: list[dict[str, Any]] = analysis_data["findings"]
    assert len(findings) >= 1, "Expected at least one finding from fresh clone"

    elapsed = time.monotonic() - t0
    sev_counts = _severity_counts(findings)
    print(
        f"  Analysis: {len(findings)} finding(s) in {elapsed:.1f}s — "
        + ", ".join(f"{s}={c}" for s, c in sorted(sev_counts.items()))
    )
    _print_findings_table(findings)

    # Copy outputs to the shared output dir for inspection
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(sbom_out, OUTPUT_DIR / "sbom_openai-cs-agents-demo-fresh.json")
    shutil.copy(analysis_out, OUTPUT_DIR / "analyze_openai-cs-agents-demo-fresh.json")
    print(f"  Outputs written to {OUTPUT_DIR}/")
