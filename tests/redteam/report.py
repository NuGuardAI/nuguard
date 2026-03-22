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
    llm_executive_summary: str | None = None,
    llm_remediations: dict[str, str] | None = None,
    llm_coding_brief: str | None = None,
    prompt_cache_path: "Path | None" = None,
    eval_llm_model: str | None = None,
    llm_enriched_scenarios: int = 0,
    llm_enriched_executed: int = 0,
    llm_variants_total: int = 0,
    prompt_cache_hit: bool = False,
    llm_scenario_variants: dict[str, int] | None = None,
    log_lines: list[str] | None = None,
    component_names: dict[str, list[str]] | None = None,
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

    # --- Executive Summary (LLM-generated) --------------------------------
    if llm_executive_summary:
        _h(f"## Executive Summary")
        _h(f"")
        _h(llm_executive_summary)
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
    if llm_enriched_scenarios:
        cache_label = "cache hit" if prompt_cache_hit else "cache miss"
        enriched_label = (
            f"{llm_enriched_executed} / {llm_enriched_scenarios} executed"
            if llm_enriched_executed and llm_enriched_executed != llm_enriched_scenarios
            else str(llm_enriched_scenarios)
        )
        _h(f"| LLM-enriched scenarios | {enriched_label} ({cache_label}) |")
        _h(f"| LLM payload variants | {llm_variants_total} |")
    _h(f"| Total findings | {len(findings)} |")
    for sev in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]:
        cnt = sev_counts.get(sev, 0)
        if cnt:
            _h(f"| {_SEV_BADGE[sev]} findings | {cnt} |")
    _h(f"| Scan duration | {scan_duration_s:.1f}s |")
    _h(f"")

    # --- Attack Goal Summary ---------------------------------------------
    if scenario_records:
        from collections import defaultdict
        # Group by goal_type
        by_goal: dict[str, list] = defaultdict(list)
        for rec in scenario_records:
            by_goal[rec.goal_type].append(rec)

        _h(f"## Attack Goal Summary")
        _h(f"")
        _h(f"| Goal Type | Scenarios | Findings | Clean | Aborted | Outcome |")
        _h(f"|-----------|-----------|----------|-------|---------|---------|")
        for goal_type in sorted(by_goal):
            recs = by_goal[goal_type]
            n_total = len(recs)
            n_finding = sum(1 for r in recs if r.had_finding)
            n_aborted = sum(1 for r in recs if r.chain_status == "aborted" and not r.had_finding)
            n_clean = n_total - n_finding - n_aborted
            goal_label = goal_type.replace("_", " ").title()
            if n_finding > 0:
                outcome = "⚠️ Vulnerable"
            elif n_aborted == n_total:
                outcome = "✅ Blocked by guardrails"
            elif n_clean == n_total:
                outcome = "✅ Payloads rejected"
            else:
                outcome = f"⚠️ Partial ({n_finding} findings)"
            _h(f"| {goal_label} | {n_total} | {n_finding} | {n_clean} | {n_aborted} | {outcome} |")
        _h(f"")

        # Per-goal narrative
        for goal_type in sorted(by_goal):
            recs = by_goal[goal_type]
            goal_label = goal_type.replace("_", " ").title()
            finding_recs = [r for r in recs if r.had_finding]
            aborted_recs = [r for r in recs if r.chain_status == "aborted" and not r.had_finding]
            _h(f"**{goal_label}**")
            _h(f"")
            if finding_recs:
                _h(f"Attack succeeded in {len(finding_recs)} of {len(recs)} scenarios:")
                for r in finding_recs:
                    _h(f"- **{r.title}** — {r.description[:200]}")
            elif aborted_recs:
                _h(
                    f"All {len(aborted_recs)} scenario(s) were aborted — the target's guardrails "
                    f"or error responses halted the attack chain before a finding could be confirmed."
                )
            else:
                _h(
                    f"All {len(recs)} scenario(s) ran to completion without triggering a finding — "
                    f"payloads were rejected or responses did not match success criteria."
                )
            _h(f"")

    # --- SBOM Component Coverage -----------------------------------------
    if scenario_records:
        _h(f"## SBOM Component Coverage")
        _h(f"")

        # Targeted components (from affected field: "Name (TYPE)")
        targeted: dict[str, set[str]] = {}
        for rec in scenario_records:
            if rec.affected:
                for part in rec.affected.split(","):
                    part = part.strip()
                    if "(" in part and part.endswith(")"):
                        name_part, type_part = part.rsplit("(", 1)
                        ctype = type_part.rstrip(")")
                        cname = name_part.strip()
                    else:
                        cname, ctype = part, "UNKNOWN"
                    targeted.setdefault(ctype, set()).add(cname)

        if targeted:
            _h(f"### Components Targeted by Scenarios")
            _h(f"")
            _h(f"| Component Type | Names |")
            _h(f"|----------------|-------|")
            for ctype in sorted(targeted):
                names = ", ".join(f"`{n}`" for n in sorted(targeted[ctype]))
                _h(f"| {ctype} | {names} |")
            _h(f"")

        # Tools actually invoked (from step tool_calls)
        invoked_tools: dict[str, int] = {}
        for rec in scenario_records:
            for step in rec.steps:
                for tool in step.get("tool_calls", []):
                    invoked_tools[tool] = invoked_tools.get(tool, 0) + 1

        if invoked_tools:
            _h(f"### Tools Invoked During Testing")
            _h(f"")
            _h(f"| Tool | Invocations |")
            _h(f"|------|-------------|")
            for tool, count in sorted(invoked_tools.items(), key=lambda x: -x[1]):
                _h(f"| `{tool}` | {count} |")
            _h(f"")

        # Log-based coverage: which SBOM component names appear in app logs
        if log_lines and component_names:
            all_component_names: list[str] = []
            for names in component_names.values():
                all_component_names.extend(names)

            if all_component_names:
                log_text = "\n".join(log_lines)
                seen: list[tuple[str, str]] = []
                not_seen: list[tuple[str, str]] = []
                for ctype, names in sorted(component_names.items()):
                    for name in sorted(names):
                        if name and name.lower() in log_text.lower():
                            seen.append((name, ctype))
                        else:
                            not_seen.append((name, ctype))

                _h(f"### App Log Coverage")
                _h(f"")
                _h(
                    f"> Based on {len(log_lines)} lines of app stderr captured during the scan."
                )
                _h(f"")
                if seen:
                    _h(f"**Exercised** ({len(seen)} components appeared in logs):")
                    _h(f"")
                    _h(f"| Component | Type |")
                    _h(f"|-----------|------|")
                    for name, ctype in seen:
                        _h(f"| `{name}` | {ctype} |")
                    _h(f"")
                if not_seen:
                    _h(f"**Not observed in logs** ({len(not_seen)} components):")
                    _h(f"")
                    _h(f"| Component | Type |")
                    _h(f"|-----------|------|")
                    for name, ctype in not_seen:
                        _h(f"| `{name}` | {ctype} |")
                    _h(f"")

    # --- Scenarios Executed ----------------------------------------------
    if scenarios_executed:
        _h(f"## Scenarios Executed ({len(scenarios_executed)})")
        _h(f"")
        has_variants = bool(llm_scenario_variants)
        if has_variants:
            _h(f"| # | Title | Goal Type | LLM Variants | Status |")
            _h(f"|---|-------|-----------|--------------|--------|")
        else:
            _h(f"| # | Title | Goal Type | Status |")
            _h(f"|---|-------|-----------|--------|")
        for i, (title, goal, had_finding) in enumerate(scenarios_executed, 1):
            goal_label = goal.replace("_", " ").title()
            status = "🔴 Finding" if had_finding else "✅ Clean"
            if has_variants:
                n_variants = (llm_scenario_variants or {}).get(title, 0)
                variant_cell = f"⚗️ +{n_variants}" if n_variants else "—"
                _h(f"| {i} | {title} | {goal_label} | {variant_cell} | {status} |")
            else:
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
            if finding.sbom_path_descriptions:
                path_str = " → ".join(finding.sbom_path_descriptions)
                _h(f"**SBOM path:** `{path_str}`")
                _h(f"")
            _h(f"{finding.description}")
            _h(f"")
            if finding.evidence:
                _h(f"**Evidence:** {finding.evidence}")
                _h(f"")
            # Use LLM remediation if available, else static
            finding_remediation = (
                (llm_remediations or {}).get(finding.finding_id)
                or finding.remediation
            )
            if finding_remediation:
                _h(f"**Remediation:** {finding_remediation}")
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
                    # LLM eval evidence (shown when use_llm_eval=True was used)
                    if step.get("llm_eval_evidence"):
                        confidence = step.get("llm_eval_confidence", "")
                        conf_label = f" ({confidence} confidence)" if confidence else ""
                        _h(f"*LLM Evaluation{conf_label}:* {step['llm_eval_evidence']}")
                        _h(f"")
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

    # --- Coding-Agent Remediation Brief ----------------------------------
    if llm_coding_brief:
        _h(f"## Coding-Agent Remediation Brief (AI-Generated)")
        _h(f"")
        model_note = f"Generated by {eval_llm_model}. " if eval_llm_model else ""
        _h(f"> {model_note}Review before applying.")
        _h(f"")
        _h(llm_coding_brief)
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
