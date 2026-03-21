"""Writes E2E redteam scan results as a Markdown report to tests/output/."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nuguard.models.finding import Finding, Severity

if TYPE_CHECKING:
    from nuguard.redteam.executor.orchestrator import ScenarioRecord

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "tests" / "output"

def _format_response(raw: str) -> str:
    """Try to pretty-print JSON; return the original string if it is not JSON."""
    stripped = raw.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.dumps(json.loads(stripped), indent=2)
        except (json.JSONDecodeError, ValueError):
            pass
    return raw


_SEV_BADGE = {
    Severity.CRITICAL: "🔴 CRITICAL",
    Severity.HIGH:     "🟠 HIGH",
    Severity.MEDIUM:   "🟡 MEDIUM",
    Severity.LOW:      "🔵 LOW",
    Severity.INFO:     "⚪ INFO",
}


def write_redteam_report(
    app_name: str,
    app_url: str,
    chat_path: str,
    sbom_summary: dict[str, Any],
    scenarios_generated: int,
    findings: list[Finding],
    scan_duration_s: float,
    app_started: bool,
    app_start_error: str | None,
    notes: str = "",
    policy_file: str | None = None,
    scenarios_executed: list[tuple[str, str, bool]] | None = None,
    verbose: bool = False,
    scenario_records: list["ScenarioRecord"] | None = None,
) -> Path:
    """Render a Markdown report and write it to tests/output/; return the path."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _now_local = datetime.now(timezone.utc).astimezone()
    ts = _now_local.strftime("%Y%m%dT%H%M%SZ")
    slug = app_name.replace(" ", "_").replace("/", "-")
    out_path = OUTPUT_DIR / f"redteam_{slug}_{ts}.md"

    lines: list[str] = []
    _h = lines.append

    # --- Header ----------------------------------------------------------
    _h(f"# NuGuard Redteam E2E Report: {app_name}")
    _h(f"")
    _h(f"**Generated:** {_now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}  ")
    _h(f"**Target URL:** `{app_url}`  ")
    _h(f"**Chat / Primary Endpoint:** `{chat_path}`  ")
    _h(f"")

    if notes:
        _h(f"> **Note:** {notes}")
        _h(f"")

    # --- App Status ------------------------------------------------------
    _h(f"## App Status")
    _h(f"")
    if app_started:
        _h(f"✅ **Started successfully** — app responded to health check")
    else:
        _h(f"❌ **Failed to start**")
        if app_start_error:
            _h(f"")
            _h(f"```")
            _h(app_start_error[:1500])
            _h(f"```")
    _h(f"")

    # --- SBOM Summary ----------------------------------------------------
    _h(f"## SBOM Summary")
    _h(f"")
    node_counts: dict[str, int] = sbom_summary.get("node_counts", {})
    if node_counts:
        _h(f"| Component Type | Count |")
        _h(f"|----------------|-------|")
        for ctype, count in sorted(node_counts.items()):
            _h(f"| {ctype} | {count} |")
    else:
        _h(f"_No nodes discovered (SBOM generation failed or source was empty)_")
    _h(f"")
    if frameworks := sbom_summary.get("frameworks", []):
        _h(f"**Frameworks detected:** {', '.join(frameworks)}")
        _h(f"")
    if use_case := sbom_summary.get("use_case"):
        _h(f"**Use case:** {use_case}")
        _h(f"")
    if policy_file:
        _h(f"**Policy file:** `{policy_file}`")
        _h(f"")

    # --- Scan Statistics -------------------------------------------------
    _h(f"## Scan Statistics")
    _h(f"")
    sev_counts: dict[str, int] = {}
    for f in findings:
        sev_counts[f.severity] = sev_counts.get(f.severity, 0) + 1

    _h(f"| Metric | Value |")
    _h(f"|--------|-------|")
    _h(f"| Scenarios generated | {scenarios_generated} |")
    _h(f"| Total findings | {len(findings)} |")
    for sev in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]:
        cnt = sev_counts.get(sev, 0)
        if cnt:
            _h(f"| {_SEV_BADGE[sev]} findings | {cnt} |")
    _h(f"| Scan duration | {scan_duration_s:.1f}s |")
    _h(f"")

    # --- Scenarios Executed ----------------------------------------------
    if scenarios_executed:
        _h(f"## Scenarios Executed ({len(scenarios_executed)})")
        _h(f"")
        _h(f"| # | Title | Goal Type | Status |")
        _h(f"|---|-------|-----------|--------|")
        for i, (title, goal, had_finding) in enumerate(scenarios_executed, 1):
            goal_label = goal.replace("_", " ").title()
            status = "🔴 Finding" if had_finding else "✅ Clean"
            _h(f"| {i} | {title} | {goal_label} | {status} |")
        _h(f"")

    # --- Findings --------------------------------------------------------
    if findings:
        sorted_findings = sorted(
            findings,
            key=lambda x: list(Severity).index(x.severity),
        )
        _h(f"## Findings ({len(findings)})")
        _h(f"")
        for i, finding in enumerate(sorted_findings, 1):
            badge = _SEV_BADGE.get(finding.severity, str(finding.severity))
            _h(f"### {i}. {badge} — {finding.title}")
            _h(f"")
            if finding.affected_component:
                _h(f"**Affected component:** `{finding.affected_component}`")
                _h(f"")
            _h(f"{finding.description}")
            _h(f"")
            if finding.evidence:
                _h(f"**Evidence:** {finding.evidence}")
                _h(f"")
            if finding.remediation:
                _h(f"**Remediation:** {finding.remediation}")
                _h(f"")
            refs: list[str] = []
            if finding.owasp_asi_ref:
                refs.append(f"OWASP ASI: {finding.owasp_asi_ref}")
            if finding.owasp_llm_ref:
                refs.append(f"OWASP LLM: {finding.owasp_llm_ref}")
            if refs:
                _h(f"**References:** {' | '.join(refs)}")
                _h(f"")
            # --- Attack steps detail ---
            if finding.attack_steps:
                _h(f"**Attack Steps ({len(finding.attack_steps)})**")
                _h(f"")
                for j, step in enumerate(finding.attack_steps, 1):
                    step_type = step.get("step_type", "?")
                    description = step.get("description", "")
                    succeeded = step.get("succeeded", False)
                    status_icon = "✅" if succeeded else "❌"
                    _h(f"**Step {j} — {status_icon} `{step_type}`** {description}")
                    _h(f"")
                    # Request
                    if step.get("target_path"):
                        method = step.get("method", "POST")
                        path = step["target_path"]
                        _h(f"*Request:* `{method} {path}`")
                        if step.get("params"):
                            _h(f"```json")
                            _h(json.dumps(step["params"], indent=2))
                            _h(f"```")
                        if step.get("request_body"):
                            _h(f"```json")
                            _h(json.dumps(step["request_body"], indent=2))
                            _h(f"```")
                        if step.get("status_code") is not None:
                            _h(f"*HTTP status:* `{step['status_code']}`")
                    else:
                        payload = step.get("payload", "")
                        if payload:
                            _h(f"*Payload:*")
                            _h(f"```")
                            _h(payload)
                            _h(f"```")
                    # Tool calls
                    if step.get("tool_calls"):
                        _h(f"*Tool calls:* `{', '.join(step['tool_calls'])}`")
                    # Response
                    response = step.get("response", "")
                    if response:
                        formatted = _format_response(response)
                        lang = "json" if formatted != response else ""
                        _h(f"*Response:*")
                        _h(f"```{lang}")
                        _h(formatted)
                        _h(f"```")
                    _h(f"")
            _h("---")
            _h(f"")
    else:
        _h(f"## Findings")
        _h(f"")
        if not app_started:
            _h(f"_No findings — app did not start successfully._")
        else:
            _h(f"_No findings detected in this scan run._")
        _h(f"")

    # --- Verbose Scenario Details ----------------------------------------
    if verbose and scenario_records:
        _h(f"## Scenario Details (Verbose)")
        _h(f"")
        _h(
            f"> Full input/output traces for all {len(scenario_records)} executed scenarios. "
            f"Use this section to troubleshoot scenario selection, payloads, and responses."
        )
        _h(f"")
        for i, rec in enumerate(scenario_records, 1):
            rec_icon = (
                "🔴" if rec.had_finding
                else ("⚠️" if rec.chain_status == "aborted" else "✅")
            )
            _h(f"### {i}. {rec_icon} {rec.title}")
            _h(f"")
            _h(f"| Field | Value |")
            _h(f"|-------|-------|")
            _h(f"| **Goal type** | {rec.goal_type.replace('_', ' ').title()} |")
            _h(f"| **Scenario type** | {rec.scenario_type.replace('_', ' ').title()} |")
            _h(f"| **Impact score** | {rec.impact_score:.1f} / 10 |")
            _h(f"| **Affected** | {rec.affected or '—'} |")
            _h(f"| **Chain status** | {rec.chain_status} |")
            _h(f"| **Finding raised** | {'Yes' if rec.had_finding else 'No'} |")
            _h(f"")
            _h(f"**Why this scenario was selected:** {rec.description}")
            _h(f"")
            if rec.steps:
                _h(f"**Steps ({len(rec.steps)})**")
                _h(f"")
                for j, step in enumerate(rec.steps, 1):
                    step_type = step.get("step_type", "?")
                    description = step.get("description", "")
                    succeeded = step.get("succeeded", False)
                    status_icon_s = "✅" if succeeded else "❌"
                    _h(f"**Step {j} — {status_icon_s} `{step_type}`** {description}")
                    _h(f"")
                    # Input
                    if step.get("target_path"):
                        method = step.get("method", "POST")
                        path = step["target_path"]
                        _h(f"*Input:* `{method} {path}`")
                        if step.get("params"):
                            _h(f"```json")
                            _h(json.dumps(step["params"], indent=2))
                            _h(f"```")
                        if step.get("request_body"):
                            _h(f"```json")
                            _h(json.dumps(step["request_body"], indent=2))
                            _h(f"```")
                        if step.get("status_code") is not None:
                            _h(f"*HTTP status:* `{step['status_code']}`")
                    else:
                        payload = step.get("payload", "")
                        if payload:
                            _h(f"*Input payload:*")
                            _h(f"```")
                            _h(payload)
                            _h(f"```")
                    # Tool calls
                    if step.get("tool_calls"):
                        _h(f"*Tool calls:* `{', '.join(step['tool_calls'])}`")
                    # Output
                    response = step.get("response", "")
                    if response:
                        formatted = _format_response(response)
                        lang = "json" if formatted != response else ""
                        _h(f"*Output:*")
                        _h(f"```{lang}")
                        _h(formatted)
                        _h(f"```")
                    _h(f"")
            else:
                _h(f"_No steps executed (scenario errored before execution)._")
                _h(f"")
            _h(f"---")
            _h(f"")

    # --- Footer ----------------------------------------------------------
    _h(f"---")
    _h(f"")
    _h(f"*Report generated by NuGuard redteam E2E test harness.*")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
