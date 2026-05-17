"""NuGuard MCP server — 7 tools wrapping the nuguard CLI."""

from __future__ import annotations

import json
import os
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from nuguard.mcp._runner import (
    RunResult,
    cwd_for_config,
    exit_code_to_status,
    resolve_path,
    run_nuguard_command,
)

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = os.environ.get("NUGUARD_DEFAULT_CONFIG", "")

mcp = FastMCP(
    name="nuguard",
    instructions=(
        "NuGuard AI Application Security. Use these tools to generate an AI Bill of "
        "Materials (AI-SBOM), run static analysis, behavioral validation, and adversarial "
        "red-team testing for AI agents and LLM-powered applications. "
        "Typical workflow: nuguard_init → nuguard_sbom_generate → nuguard_analyze → "
        "nuguard_behavior → nuguard_redteam. "
        "API keys (LITELLM_API_KEY, etc.) must be set as environment variables on the "
        "MCP server process — never pass them as tool parameters."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _effective_config(config_path: str | None) -> str | None:
    """Return the caller-supplied config_path, falling back to NUGUARD_DEFAULT_CONFIG."""
    return config_path or (_DEFAULT_CONFIG or None)


def _enrich(result: RunResult, payload: dict) -> dict:
    """Inject status, exit_code, and optional stderr/timed_out into payload."""
    payload["status"] = exit_code_to_status(result.exit_code, result.timed_out)
    payload["exit_code"] = result.exit_code
    if result.timed_out:
        payload["timed_out"] = True
    if result.stderr_text.strip():
        payload["stderr"] = result.stderr_text.strip()
    return payload


# ---------------------------------------------------------------------------
# Tool 1: nuguard_init
# ---------------------------------------------------------------------------

@mcp.tool()
async def nuguard_init(
    project_dir: Annotated[str, "Directory to initialize. nuguard.yaml will be written here."],
    target_url: Annotated[str | None, "URL of the running AI application (e.g. http://localhost:8080)."] = None,
    source_dir: Annotated[str | None, "Source code directory for SBOM generation (sets source: in nuguard.yaml)."] = None,
    force: Annotated[bool, "Overwrite existing files."] = False,
    timeout_seconds: Annotated[int, "Maximum seconds to wait for the command."] = 30,
) -> dict:
    """Initialize a nuguard.yaml config file with auto-detected defaults.

    Creates nuguard.yaml, canary.example.json, and cognitive-policy.md in the
    project directory. Auto-detects existing SBOM files, policy files, and
    project language (Python / TypeScript).
    """
    project_path = resolve_path(project_dir)
    args = ["init", "--path", str(project_path)]
    if target_url:
        args += ["--target", target_url]
    if source_dir:
        args += ["--source", str(resolve_path(source_dir))]
    if force:
        args.append("--force")

    result = await run_nuguard_command(
        args, cwd=str(project_path), timeout=timeout_seconds, expect_json=False
    )

    # Parse text output: lines prefixed "  created  " or "  skipped  "
    created: list[str] = []
    skipped: list[str] = []
    for line in result.stdout_text.splitlines():
        s = line.strip()
        if s.startswith("created  "):
            created.append(s[len("created  "):].strip())
        elif s.startswith("skipped  "):
            skipped.append(s[len("skipped  "):].split("(")[0].strip())

    return _enrich(result, {
        "project_dir": str(project_path),
        "created_files": created,
        "skipped_files": skipped,
        "message": result.stdout_text.strip(),
    })


# ---------------------------------------------------------------------------
# Tool 2: nuguard_sbom_generate
# ---------------------------------------------------------------------------

@mcp.tool()
async def nuguard_sbom_generate(
    source: Annotated[str | None, "Path to local source directory to scan."] = None,
    from_repo: Annotated[str | None, "Git repository URL (e.g. https://github.com/org/repo)."] = None,
    ref: Annotated[str, "Branch, tag, or commit to scan when using from_repo."] = "main",
    output: Annotated[str, "Output file path for the generated AI-SBOM JSON."] = "app.sbom.json",
    llm: Annotated[bool, "Enable LLM enrichment of SBOM nodes (requires LITELLM_API_KEY)."] = False,
    config_path: Annotated[str | None, "Path to nuguard.yaml config file."] = None,
    timeout_seconds: Annotated[int, "Maximum seconds to wait."] = 300,
) -> dict:
    """Generate an AI Bill of Materials (AI-SBOM) from source code or a git repository.

    Detects AI components — agents, models, tools, datastores, guardrails, MCP servers,
    API endpoints — and their relationships. Either source or from_repo must be provided.
    Returns the SBOM path, node/edge counts, and a summary of detected components.
    """
    output_path = resolve_path(output)
    config = _effective_config(config_path)

    args = ["sbom", "generate", "--output", str(output_path)]
    if source:
        args += ["--source", str(resolve_path(source))]
    if from_repo:
        args += ["--from-repo", from_repo, "--ref", ref]
    if llm:
        args.append("--llm")
    if config:
        args += ["--config", str(resolve_path(config))]

    result = await run_nuguard_command(
        args,
        cwd=cwd_for_config(config),
        timeout=timeout_seconds,
        expect_json=False,
    )

    sbom_summary: dict = {}
    node_count = 0
    edge_count = 0
    if result.exit_code == 0 and output_path.exists():
        try:
            sbom_data = json.loads(output_path.read_text(encoding="utf-8"))
            sbom_summary = sbom_data.get("summary", {})
            node_count = len(sbom_data.get("nodes", []))
            edge_count = len(sbom_data.get("edges", []))
        except (json.JSONDecodeError, OSError):
            pass

    return _enrich(result, {
        "sbom_path": str(output_path),
        "node_count": node_count,
        "edge_count": edge_count,
        "sbom_summary": sbom_summary,
        "message": result.stdout_text.strip() or f"SBOM written to {output_path}",
    })


# ---------------------------------------------------------------------------
# Tool 3: nuguard_analyze
# ---------------------------------------------------------------------------

@mcp.tool()
async def nuguard_analyze(
    sbom: Annotated[str, "Path to the AI-SBOM JSON file to analyze."],
    config_path: Annotated[str | None, "Path to nuguard.yaml."] = None,
    nga_only: Annotated[bool, "Run only the 18 NGA structural rules; skip external tools."] = False,
    min_severity: Annotated[str, "Minimum severity to report: critical | high | medium | low | info."] = "medium",
    source: Annotated[str | None, "Source directory for Checkov / Trivy / Semgrep scans."] = None,
    enable_atlas: Annotated[bool, "Enable MITRE ATLAS technique mapping."] = True,
    enable_osv: Annotated[bool, "Enable OSV dependency CVE scan."] = True,
    enable_grype: Annotated[bool, "Enable Grype CVE scan."] = True,
    enable_checkov: Annotated[bool, "Enable Checkov IaC scan."] = True,
    enable_trivy: Annotated[bool, "Enable Trivy container / filesystem scan."] = True,
    enable_semgrep: Annotated[bool, "Enable Semgrep AI-security rules."] = True,
    llm: Annotated[bool, "Enable LLM enrichment in the ATLAS pass."] = False,
    timeout_seconds: Annotated[int, "Maximum seconds to wait."] = 300,
) -> dict:
    """Run static risk analysis on an AI-SBOM.

    Evaluates 18 NGA structural rules (NGA-001 to NGA-018) and optionally runs
    MITRE ATLAS mapping, OSV / Grype CVE scans, Checkov IaC, Trivy container
    scans, and Semgrep AI-security rules. Returns findings grouped by severity.
    Exit status: 'ok' (no findings), 'findings' (issues found), 'error' (crash).
    """
    config = _effective_config(config_path)
    sbom_path = resolve_path(sbom)
    args = [
        "analyze",
        "--sbom", str(sbom_path),
        "--format", "json",
        "--min-severity", min_severity,
    ]
    if config:
        args += ["--config", str(resolve_path(config))]
    if nga_only:
        args.append("--nga")
    if not enable_atlas:
        args.append("--no-atlas")
    if not enable_osv:
        args.append("--no-osv")
    if not enable_grype:
        args.append("--no-grype")
    if not enable_checkov:
        args.append("--no-checkov")
    if not enable_trivy:
        args.append("--no-trivy")
    if not enable_semgrep:
        args.append("--no-semgrep")
    if source:
        args += ["--source", str(resolve_path(source))]
    if llm:
        args.append("--llm")

    result = await run_nuguard_command(
        args,
        cwd=cwd_for_config(config),
        timeout=timeout_seconds,
        expect_json=True,
    )

    payload: dict = result.parsed_json if isinstance(result.parsed_json, dict) else {}
    if not payload:
        payload = {"raw_output": result.stdout_text}
    return _enrich(result, payload)


# ---------------------------------------------------------------------------
# Tool 4: nuguard_scan
# ---------------------------------------------------------------------------

@mcp.tool()
async def nuguard_scan(
    source: Annotated[str, "Path to AI application source directory."] = ".",
    output_dir: Annotated[str, "Directory for output files (sbom.json, findings.json, report.md, findings.sarif)."] = "nuguard-reports",
    fail_on: Annotated[str, "Severity threshold for a non-zero exit: critical | high | medium | low."] = "high",
    steps: Annotated[str, "Comma-separated pipeline steps: sbom,analyze,policy,redteam."] = "sbom,analyze",
    policy: Annotated[str | None, "Path to Cognitive Policy Markdown."] = None,
    target: Annotated[str | None, "Live application URL (required for the redteam step)."] = None,
    container_image: Annotated[str | None, "Container image ref for Trivy scan (e.g. myapp:latest)."] = None,
    llm: Annotated[bool, "Enable LLM enrichment in the ATLAS analysis pass."] = False,
    config_path: Annotated[str | None, "Path to nuguard.yaml."] = None,
    timeout_seconds: Annotated[int, "Maximum seconds to wait."] = 600,
) -> dict:
    """Run the full unified security scan pipeline: SBOM → analyze → policy → redteam.

    Orchestrates all NuGuard capabilities in sequence. By default runs the 'sbom'
    and 'analyze' steps. Add 'redteam' to steps and supply target to include
    adversarial testing. Writes sbom.json, findings.json, findings.sarif, and
    report.md to output_dir and returns a summary with artifact paths.
    """
    source_path = resolve_path(source)
    out_path = resolve_path(output_dir)
    config = _effective_config(config_path)

    args = [
        "scan",
        "--source", str(source_path),
        "--output-dir", str(out_path),
        "--fail-on", fail_on,
        "--steps", steps,
    ]
    if policy:
        args += ["--policy", str(resolve_path(policy))]
    if target:
        args += ["--target", target]
    if container_image:
        args += ["--container-image", container_image]
    if llm:
        args.append("--llm")

    result = await run_nuguard_command(
        args,
        cwd=cwd_for_config(config) or str(source_path),
        timeout=timeout_seconds,
        expect_json=False,
    )

    # Build summary by reading findings.json from output dir
    summary: dict = {}
    findings_json_path = out_path / "findings.json"
    if findings_json_path.exists():
        try:
            findings_data = json.loads(findings_json_path.read_text(encoding="utf-8"))
            counts = findings_data.get("severity_counts", {})
            total = findings_data.get("total", sum(counts.values()))
            summary = {"total": total, **counts}
        except (json.JSONDecodeError, OSError):
            pass

    artifacts = {
        name: str(out_path / fname)
        for name, fname in {
            "sbom": "sbom.json",
            "findings_json": "findings.json",
            "findings_sarif": "findings.sarif",
            "report_md": "report.md",
        }.items()
        if (out_path / fname).exists()
    }

    return _enrich(result, {
        "output_dir": str(out_path),
        "artifacts": artifacts,
        "summary": summary,
        "message": result.stdout_text.strip(),
    })


# ---------------------------------------------------------------------------
# Tool 5: nuguard_behavior
# ---------------------------------------------------------------------------

@mcp.tool()
async def nuguard_behavior(
    config_path: Annotated[str, "Path to nuguard.yaml (must contain target URL, auth, and SBOM path)."],
    mode: Annotated[str, "Analysis mode: static | dynamic | static+dynamic."] = "static+dynamic",
    target: Annotated[str | None, "Override behavior.target URL from nuguard.yaml."] = None,
    policy: Annotated[str | None, "Path to Cognitive Policy Markdown."] = None,
    intent: Annotated[str | None, "Override the app intent description (one-line summary)."] = None,
    output: Annotated[str | None, "Write the behavior report to this file path."] = None,
    fail_on: Annotated[str, "Severity threshold for a non-zero exit: critical | high | medium | low."] = "high",
    timeout_seconds: Annotated[int, "Maximum seconds to wait."] = 300,
) -> dict:
    """Run intent-aware behavioral testing against a live AI application.

    Static mode checks SBOM–policy alignment without hitting the live app.
    Dynamic mode sends probe conversations to the running app and judges each
    turn for intent drift, policy violations, and data leakage.
    Requires the target app to be running and reachable.
    """
    config = _effective_config(config_path)
    if not config:
        return {"status": "error", "exit_code": 1, "message": "config_path is required for nuguard_behavior"}

    config_resolved = resolve_path(config)
    args = [
        "behavior",
        "--config", str(config_resolved),
        "--mode", mode,
        "--format", "json",
        "--fail-on", fail_on,
    ]
    if target:
        args += ["--target", target]
    if policy:
        args += ["--policy", str(resolve_path(policy))]
    if intent:
        args += ["--intent", intent]
    if output:
        args += ["--output", str(resolve_path(output))]

    result = await run_nuguard_command(
        args,
        cwd=str(config_resolved.parent),
        timeout=timeout_seconds,
        expect_json=True,
    )

    payload: dict = result.parsed_json if isinstance(result.parsed_json, dict) else {}
    if not payload:
        payload = {"raw_output": result.stdout_text}
    return _enrich(result, payload)


# ---------------------------------------------------------------------------
# Tool 6: nuguard_redteam
# ---------------------------------------------------------------------------

@mcp.tool()
async def nuguard_redteam(
    config_path: Annotated[str, "Path to nuguard.yaml (must contain SBOM path, target URL, and LLM config)."],
    sbom: Annotated[str | None, "Override SBOM path from nuguard.yaml."] = None,
    target: Annotated[str | None, "Override target URL from nuguard.yaml."] = None,
    policy: Annotated[str | None, "Path to Cognitive Policy Markdown."] = None,
    profile: Annotated[str, "Scan profile: ci (fast, high-signal) | full (comprehensive)."] = "ci",
    scenarios: Annotated[str | None, "Comma-separated scenario types to include."] = None,
    min_impact_score: Annotated[float, "Minimum pre-impact score [0–10] for scenario inclusion."] = 0.0,
    output: Annotated[str | None, "Write findings JSON to this file path."] = None,
    fail_on: Annotated[str, "Severity threshold for a non-zero exit: critical | high | medium | low."] = "high",
    guided: Annotated[bool | None, "Enable (True) or disable (False) guided multi-turn conversations."] = None,
    guided_max_turns: Annotated[int | None, "Maximum turns per guided adversarial conversation."] = None,
    guided_concurrency: Annotated[int | None, "Maximum parallel guided conversations."] = None,
    timeout_seconds: Annotated[int, "Maximum seconds to wait. Redteam scans can be long-running."] = 900,
) -> dict:
    """Run adversarial red-team testing against a live AI application.

    Generates exploit scenarios from the AI-SBOM and executes them against the
    running app to discover prompt injection, data exfiltration, privilege
    escalation, policy bypass, and MCP toxic-flow vulnerabilities. Requires
    the target app to be running and reachable, plus an LLM configured for
    attack payload generation (set NUGUARD_REDTEAM_LLM_MODEL env var).
    """
    config = _effective_config(config_path)
    if not config:
        return {"status": "error", "exit_code": 1, "message": "config_path is required for nuguard_redteam"}

    config_resolved = resolve_path(config)
    args = [
        "redteam",
        "--config", str(config_resolved),
        "--format", "json",
        "--profile", profile,
        "--fail-on", fail_on,
        "--min-impact-score", str(min_impact_score),
    ]
    if sbom:
        args += ["--sbom", str(resolve_path(sbom))]
    if target:
        args += ["--target", target]
    if policy:
        args += ["--policy", str(resolve_path(policy))]
    if scenarios:
        args += ["--scenarios", scenarios]
    if output:
        args += ["--output", str(resolve_path(output))]
    if guided is True:
        args.append("--guided")
    elif guided is False:
        args.append("--no-guided")
    if guided_max_turns is not None:
        args += ["--guided-max-turns", str(guided_max_turns)]
    if guided_concurrency is not None:
        args += ["--guided-concurrency", str(guided_concurrency)]

    result = await run_nuguard_command(
        args,
        cwd=str(config_resolved.parent),
        timeout=timeout_seconds,
        expect_json=True,
    )

    payload: dict = result.parsed_json if isinstance(result.parsed_json, dict) else {}
    if not payload:
        payload = {"raw_output": result.stdout_text}

    # Inject a findings summary if not already present
    if "findings" in payload and "summary" not in payload:
        findings = payload["findings"]
        if isinstance(findings, list):
            counts: dict[str, int] = {}
            for f in findings:
                sev = f.get("severity", "info")
                counts[sev] = counts.get(sev, 0) + 1
            payload["summary"] = {"total": len(findings), **counts}

    return _enrich(result, payload)


# ---------------------------------------------------------------------------
# Tool 7: nuguard_policy_check
# ---------------------------------------------------------------------------

@mcp.tool()
async def nuguard_policy_check(
    policy: Annotated[str | None, "Path to Cognitive Policy Markdown file."] = None,
    sbom: Annotated[str | None, "Path to AI-SBOM JSON to cross-check against the policy."] = None,
    config_path: Annotated[str | None, "Path to nuguard.yaml (fallback for policy/sbom paths)."] = None,
    framework: Annotated[str | None, "Compliance framework: owasp-llm-top10 | nist-ai-rmf | eu-ai-act."] = None,
    output: Annotated[str | None, "Write compliance report to this file path."] = None,
    verbose: Annotated[bool, "Include all controls (passed and gaps) with evidence."] = False,
    timeout_seconds: Annotated[int, "Maximum seconds to wait."] = 60,
) -> dict:
    """Cross-check a Cognitive Policy against an AI-SBOM and run compliance assessments.

    Without a framework, checks the policy document for gaps and lints it against
    the SBOM's detected components. With a framework (owasp-llm-top10, nist-ai-rmf,
    eu-ai-act), maps the SBOM and policy to the framework's controls and reports
    satisfied controls, partial coverage, and gaps.
    """
    config = _effective_config(config_path)
    args = ["policy", "check", "--format", "json"]

    if policy:
        args += ["--policy", str(resolve_path(policy))]
    if sbom:
        args += ["--sbom", str(resolve_path(sbom))]
    if config:
        args += ["--config", str(resolve_path(config))]
    if framework:
        args += ["--framework", framework]
    if output:
        args += ["--output", str(resolve_path(output))]
    if verbose:
        args.append("--verbose")

    result = await run_nuguard_command(
        args,
        cwd=cwd_for_config(config),
        timeout=timeout_seconds,
        expect_json=True,
    )

    payload: dict = result.parsed_json if isinstance(result.parsed_json, dict) else {}
    if not payload:
        payload = {"raw_output": result.stdout_text}
    return _enrich(result, payload)
