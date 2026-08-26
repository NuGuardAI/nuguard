"""Behavior analysis report generation — Markdown, JSON, and Rich text."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from nuguard.behavior._utils import normalise_name
from nuguard.common.logging import get_logger
from nuguard.output.report_shared import (
    _norm_sev,
    _truncate_evidence,
    render_finding_block,
    render_remediation_plan_section,
)
from nuguard.output.validation_report import (
    extract_behavior_scenario_details,
    render_behavior_coverage_evidence,
    render_scenario_details_section,
    render_validation_summary_bullets,
)

if TYPE_CHECKING:
    from nuguard.behavior.models import BehaviorAnalysisResult
    from nuguard.cli.report_meta import ReportMeta

_log = get_logger(__name__)

_MAX_DIAG_SCENARIOS = 20
_MAX_DIAG_TURNS_PER_SCENARIO = 4
_MAX_DIAG_SNIPPET_CHARS = 800

# Finding types produced by gap aggregation — rendered in the Gap Summary section,
# excluded from the Dynamic Analysis Findings section.
_GAP_FINDING_TYPES = frozenset({"CAPABILITY_GAP", "INTENT_MISALIGNMENT", "TOOL_CHAIN_BROKEN"})


def to_json(result: "BehaviorAnalysisResult", meta: "ReportMeta | None" = None) -> str:
    """Generate JSON report string.

    Args:
        result: Complete BehaviorAnalysisResult.
        meta: Optional report metadata.

    Returns:
        JSON string.
    """
    data: dict[str, Any] = {
        "run_id": result.run_id,
        "created_at": result.created_at.isoformat(),
        "scan_outcome": result.scan_outcome,
        "overall_risk_score": result.overall_risk_score,
        "coverage_percentage": result.coverage_percentage,
        "intent_alignment_score": result.intent_alignment_score,
        "llm_executive_summary": result.llm_executive_summary,
        "token_usage": result.token_usage.model_dump(),
        "input_tokens_used": result.token_usage.input_tokens,
        "output_tokens_used": result.token_usage.output_tokens,
        "intent": result.intent.model_dump(),
        "static_findings": result.static_findings,
        "dynamic_findings": result.dynamic_findings,
        "scenario_results": [s.model_dump() for s in result.scenario_results],
        "coverage": [c.model_dump() for c in result.coverage],
        "recommendations": [r.model_dump() for r in result.recommendations],
        "remediation_plan": [a.model_dump() for a in result.remediation_plan],
        "counts": result.counts.model_dump(),
        "gap_aggregation_stats": result.gap_aggregation_stats,
        "coverage_mapping_diagnostics": result.coverage_mapping_diagnostics,
        "effective_endpoint": result.effective_endpoint,
        "target_endpoint_source": result.target_endpoint_source,
        "run_profile": result.run_profile,
        "config_notes": result.config_notes,
    }
    if meta is not None:
        if hasattr(meta, "to_dict"):
            data["meta"] = meta.to_dict()
        else:
            data["meta"] = {
                "config_path": str(getattr(meta, "config_path", "") or ""),
                "sbom_path": str(getattr(meta, "sbom_path", "") or ""),
                "policy_path": str(getattr(meta, "policy_path", "") or ""),
            }
        if getattr(meta, "verbose", False):
            data["diagnostics"] = _build_behavior_diagnostics(result)
    return json.dumps(data, indent=2, default=str)



def to_markdown(result: "BehaviorAnalysisResult", meta: "ReportMeta | None" = None) -> str:
    """Generate Markdown behavior analysis report.

    Args:
        result: Complete BehaviorAnalysisResult.
        meta: Optional report metadata.

    Returns:
        Markdown string.
    """
    lines: list[str] = []
    lines.append("# Behavior Analysis Report")
    lines.append("")

    if meta is not None:
        lines += meta.to_markdown_lines()

    if result.config_notes:
        lines.append("## ⚠ Configuration Notes")
        lines.append("")
        for note in result.config_notes:
            lines.append(f"> **⚠** {note}")
        lines.append("")

    # Determine analysis mode
    has_static = bool(result.static_findings)
    has_dynamic = bool(result.dynamic_findings or result.scenario_results)
    if has_static and has_dynamic:
        mode = "static + dynamic"
    elif has_static:
        mode = "static"
    elif has_dynamic:
        mode = "dynamic"
    else:
        mode = "static + dynamic"

    lines.append("## Summary")
    lines.append("")
    if result.llm_executive_summary:
        lines.append(result.llm_executive_summary)
        lines.append("")
    lines.append(f"- **Intent**: {result.intent.app_purpose or 'not determined'}")
    lines.append(f"- **Analysis Mode**: {mode}")
    lines.append(f"- **Scan Outcome**: `{result.scan_outcome}`")
    # Internal correlation IDs stay out of default user-facing reports.
    if getattr(meta, "verbose", False):
        lines.append(f"- **Run ID**: `{result.run_id}`")
    _dyn_outcome = getattr(result, "dynamic_scan_outcome", None)
    if result.static_findings and _dyn_outcome in (
        "aborted_target_unavailable",
        "inconclusive_target_errors",
        "aborted_endpoint_unreachable",
    ):
        if _dyn_outcome == "aborted_target_unavailable":
            lines.append(
                "> **Note:** Dynamic scenario testing was aborted — the target was unreachable. "
                "All scenario probes returned HTTP errors. Findings below are from static analysis only."
            )
        elif _dyn_outcome == "aborted_endpoint_unreachable":
            lines.append(
                "> **Note:** Dynamic scenario testing was aborted before any scenario ran — "
                "the configured chat endpoint returned HTTP 404/405. Fix `target_endpoint` in "
                "nuguard.yaml or remove it to allow fallback discovery. Findings below are from "
                "static analysis only."
            )
        else:
            lines.append(
                "> **Note:** Dynamic scenario testing encountered significant target errors "
                "and did not complete. Findings below are from static analysis only."
            )
    lines.append(f"- **Overall Risk Score**: {result.overall_risk_score:.1f} / 100")
    total_comp = len(result.coverage)
    exercised = sum(1 for c in result.coverage if c.exercised)
    lines.append(f"- **Coverage**: {result.coverage_percentage * 100:.0f}% ({exercised}/{total_comp} components exercised)")

    # Unexercised components — annotate with the classified refusal reason
    # where escalation classified one (behavior.escalate_on_refusal); falls
    # back to the flat, undifferentiated list when no classification was
    # attempted, so existing report snapshots are unaffected by default.
    not_exercised = [c for c in result.coverage if not c.exercised]
    if not_exercised:
        if any(getattr(c, "refusal_reason", None) for c in not_exercised):
            ne_names = ", ".join(
                f"`{c.component_name}` — {c.refusal_reason}" if c.refusal_reason else f"`{c.component_name}`"
                for c in not_exercised
            )
        else:
            ne_names = ", ".join(f"`{c.component_name}`" for c in not_exercised)
        lines.append(f"- **Not Exercised** ({len(not_exercised)} components): {ne_names}")

    # Scenarios skipped due to max_scenarios cap
    if result.scenarios_skipped:
        deprioritized = set(getattr(result, "scenarios_deprioritized", None) or [])
        plain_skipped = [n for n in result.scenarios_skipped if n not in deprioritized]
        if plain_skipped:
            skipped_names = ", ".join(f"`{n}`" for n in plain_skipped)
            lines.append(
                f"- **Scenarios Not Run** ({len(plain_skipped)} skipped by `max_scenarios` cap): {skipped_names}"
            )
        if deprioritized:
            deprio_names = ", ".join(f"`{n}`" for n in sorted(deprioritized))
            lines.append(
                f"- **Scenarios Deprioritized** ({len(deprioritized)} cut by `max_scenarios` cap "
                f"after their tool family probed as blocked): {deprio_names}"
            )

    lines.append(f"- **Intent Alignment Score**: {result.intent_alignment_score:.2f} / 5.0")
    counts = result.counts
    lines.append(f"- **Total Findings**: {counts.total_unique_findings}")
    # Severity breakdown
    sev_counts: dict[str, int] = {}
    for f in list(result.static_findings) + list(result.dynamic_findings):
        sev = _norm_sev(f.get("severity", "unknown"))
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
    sev_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    sev_parts = [f"{s}: {sev_counts[s]}" for s in sev_order if s in sev_counts]
    if sev_parts:
        lines.append(f"- **By Severity**: {' | '.join(sev_parts)}")
    lines.append("")
    lines.append("| Count Bucket | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Unique findings (summary) | {counts.total_unique_findings} |")
    lines.append(f"| Static findings | {counts.static_findings} |")
    lines.append(f"| Dynamic policy/canary findings | {counts.policy_dynamic_findings} |")
    lines.append(f"| Aggregated gap findings | {counts.gap_findings} |")
    lines.append(f"| Deviation evidence items (per-turn) | {counts.deviation_evidence_items} |")
    _gap_stats = result.gap_aggregation_stats or {}
    _unique_turns = int(_gap_stats.get("unique_evidence_turns", 0))
    _raw_rows = int(_gap_stats.get("raw_evidence_rows", 0))
    _turns_dedup_note = f"; {_raw_rows} evidence rows → {_unique_turns} unique turns" if _raw_rows and _unique_turns < _raw_rows else ""
    lines.append(
        f"| Raw gap observations | {counts.raw_gap_observations} "
        f"(text-deduped to {counts.unique_gap_observations}{_turns_dedup_note}) |"
    )

    _scenario_details = None
    if result.scenario_results:
        _scenario_details = extract_behavior_scenario_details(result.scenario_results)
        _passed = sum(1 for d in _scenario_details if d.status == "PASS")
        _type_breakdown: dict[str, int] = {}
        for d in _scenario_details:
            _type_breakdown[d.scenario_type] = _type_breakdown.get(d.scenario_type, 0) + 1
        render_validation_summary_bullets(
            lines,
            total_scenarios=len(_scenario_details),
            passed_scenarios=_passed,
            failed_scenarios=len(_scenario_details) - _passed,
            total_turns=sum(len(d.turns) for d in _scenario_details),
            type_breakdown=_type_breakdown,
        )
    else:
        lines.append("")

    _prev_profile = getattr(meta, "previous_run_profile", {}) if meta is not None else {}
    _curr_profile = result.run_profile or {}
    if _prev_profile and _curr_profile:
        prev_exec = int(_prev_profile.get("scenarios_executed", 0) or 0)
        curr_exec = int(_curr_profile.get("scenarios_executed", 0) or 0)
        prev_ver = str(_prev_profile.get("behavior_engine_version", "") or "")
        curr_ver = str(_curr_profile.get("behavior_engine_version", "") or "")
        scenario_delta = abs(curr_exec - prev_exec)
        scenario_delta_pct = (scenario_delta / prev_exec) if prev_exec else 0.0
        if (prev_exec and scenario_delta_pct > 0.20) or (prev_ver and curr_ver and prev_ver != curr_ver):
            lines.append(
                f"> **Comparability warning:** scenario set changed from {prev_exec} to {curr_exec} "
                f"(engine {prev_ver or 'unknown'} -> {curr_ver or 'unknown'}). "
                "Trend comparisons may be unreliable."
            )
            lines.append("")

    if _curr_profile:
        lines.append("## Run Profile")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        lines.append(f"| NuGuard Version | {_curr_profile.get('nuguard_version', 'n/a')} |")
        lines.append(f"| Behavior Engine Version | {_curr_profile.get('behavior_engine_version', 'n/a')} |")
        lines.append(f"| Scenarios Planned | {_curr_profile.get('scenarios_planned', 'n/a')} |")
        lines.append(f"| Scenarios Executed | {_curr_profile.get('scenarios_executed', 'n/a')} |")
        lines.append(f"| Scenarios Skipped | {_curr_profile.get('scenarios_skipped', 'n/a')} |")
        lines.append(f"| Total Turns | {_curr_profile.get('total_turns', 'n/a')} |")
        lines.append(f"| Coverage Turns | {_curr_profile.get('coverage_turns', 'n/a')} |")
        lines.append(f"| LLM Used | {_curr_profile.get('llm_used', 'n/a')} |")
        _llm_model_display = (
            _curr_profile.get("llm_model") or
            (meta.llm_models[0] if meta is not None and getattr(meta, "llm_models", []) else None) or
            "n/a"
        )
        lines.append(f"| LLM Model | {_llm_model_display} |")
        lines.append(f"| Target Fingerprint | {_curr_profile.get('target_fingerprint', 'n/a')} |")
        scenario_types = _curr_profile.get("scenario_types", {}) or {}
        if scenario_types:
            lines.append(f"| Scenario Types | {', '.join(f'{k}:{v}' for k, v in sorted(scenario_types.items()))} |")
        lines.append("")

    # Scenario Coverage table — placed right after Summary so it's the first thing readers see
    if result.scenario_results:
        lines.extend(_scenario_coverage_table(result.scenario_results))

    # Static Analysis Findings — grouped by policy rule to avoid per-tool noise
    if result.static_findings:
        lines.append("## Static Analysis Findings")
        lines.append("")
        # Group by (title_prefix, policy_rule) so all tools violating the same rule collapse
        grouped_static: dict[str, list[dict]] = {}
        ungrouped_static: list[dict] = []
        for finding in result.static_findings:
            desc = finding.get("description", "")
            # BA-003: "Policy restricts action 'X', but agent 'Y' has a CALLS edge to tool 'Z'"
            m = re.search(r"Policy restricts action '([^']+)'", desc)
            if m:
                rule_key = m.group(1)
                grouped_static.setdefault(rule_key, []).append(finding)
            else:
                ungrouped_static.append(finding)

        # Render grouped (restricted action) findings — one heading per policy rule
        for rule_key, findings_group in grouped_static.items():
            sev = _norm_sev(findings_group[0].get("severity", ""))
            fid = findings_group[0].get("finding_id", "")
            tool_names = [f.get("affected_component", "?") for f in findings_group]
            owasp_llm = findings_group[0].get("owasp_llm_ref") or ""
            owasp_asi = findings_group[0].get("owasp_asi_ref") or ""
            mitre_atlas = findings_group[0].get("mitre_atlas_technique") or ""
            heading = f"### [{sev}] Restricted Action Reachable — '{rule_key}'"
            if fid:
                heading += f" — {fid}"
            lines.append(heading)
            lines.append("")
            lines.append(f"Policy restricts action '{rule_key}', but {len(tool_names)} tool(s) implementing this action are reachable via CALLS edges:")
            lines.append("")
            for tn in sorted(tool_names):
                # Find remediation for this specific tool
                rem_finding = next((f for f in findings_group if f.get("affected_component") == tn), findings_group[0])
                rem = rem_finding.get("remediation", "")
                lines.append(f"- `{tn}` — {rem}")
            lines.append("")
            if owasp_llm:
                lines.append(f"**OWASP LLM:** {owasp_llm}")
                lines.append("")
            if owasp_asi:
                lines.append(f"**OWASP ASI:** {owasp_asi}")
                lines.append("")
            if mitre_atlas:
                lines.append(f"**MITRE ATLAS:** {mitre_atlas}")
                lines.append("")

        # Render ungrouped findings individually (BA-001, BA-004, BA-005, etc.)
        for finding in ungrouped_static:
            title = finding.get("title", "")
            fid = finding.get("finding_id", "")
            sev = _norm_sev(finding.get("severity", ""))
            comp = finding.get("affected_component", "")
            desc = finding.get("description", "")
            remediation = finding.get("remediation", "")
            owasp_asi_ref = finding.get("owasp_asi_ref") or ""
            owasp_llm_ref = finding.get("owasp_llm_ref") or ""
            mitre_atlas_ref = finding.get("mitre_atlas_technique") or ""
            heading = f"### [{sev}] {title}"
            if fid:
                heading += f" — {fid}"
            lines.append(heading)
            if comp:
                lines.append(f"**Affected Component:** {comp}")
            lines.append("")
            lines.append(desc)
            lines.append("")
            if remediation:
                lines.append(f"**Remediation:** {remediation}")
                lines.append("")
            if owasp_llm_ref:
                lines.append(f"**OWASP LLM:** {owasp_llm_ref}")
                lines.append("")
            if owasp_asi_ref:
                lines.append(f"**OWASP ASI:** {owasp_asi_ref}")
                lines.append("")
            if mitre_atlas_ref:
                lines.append(f"**MITRE ATLAS:** {mitre_atlas_ref}")
                lines.append("")

    # Dynamic Analysis Results
    if result.scenario_results:
        lines.append("## Dynamic Analysis Results")
        lines.append("")
        for sr in result.scenario_results:
            lines.append(f"### Scenario: {sr.scenario_name}")
            lines.append(f"- **Type**: {sr.scenario_type}")
            lines.append(f"- **Overall Score**: {sr.overall_score:.2f}")
            lines.append(f"- **Coverage**: {sr.coverage_pct * 100:.0f}%")
            if sr.coverage_turns:
                lines.append(f"- **Turns**: {sr.total_turns} ({sr.coverage_turns} adaptive)")
            else:
                lines.append(f"- **Turns**: {sr.total_turns}")
            _eps = sorted({str(v.get("effective_endpoint", "")).strip() for v in sr.verdicts if str(v.get("effective_endpoint", "")).strip()})
            if _eps:
                if len(_eps) == 1:
                    lines.append(f"- **Effective Endpoint**: `{_eps[0]}`")
                else:
                    lines.append(f"- **Effective Endpoints**: {', '.join(f'`{ep}`' for ep in _eps)}")
            lines.append("")

            if sr.verdicts:
                # Only show FAIL/PARTIAL turns (missed or partial coverage)
                non_pass = [v for v in sr.verdicts if v.get("verdict") != "PASS"]
                if not non_pass:
                    lines.append("_All turns passed._")
                    lines.append("")
                else:
                    passed_count = len(sr.verdicts) - len(non_pass)
                    if passed_count > 0:
                        lines.append(
                            f"_Showing {len(non_pass)} missed/partial turn(s) — "
                            f"{passed_count} passing turn(s) omitted._"
                        )
                        lines.append("")
                    lines.append("| Turn | Verdict | Comp | Validity | Alignment | Score | Gaps |")
                    lines.append("|------|---------|------|----------|-----------|-------|------|")
                    for v in non_pass:
                        t = v.get("turn", "?")
                        verdict = v.get("verdict", "?")
                        scores = v.get("scores", {})
                        ci = f"{scores.get('component_invoked'):.1f}" if isinstance(scores.get("component_invoked"), (int, float)) else "-"
                        rv = f"{scores.get('response_validity'):.1f}" if isinstance(scores.get("response_validity"), (int, float)) else "-"
                        ta = f"{scores.get('topic_alignment'):.1f}" if isinstance(scores.get("topic_alignment"), (int, float)) else "-"
                        os_ = f"{v.get('overall_score'):.2f}" if isinstance(v.get("overall_score"), (int, float)) else "-"
                        gaps_raw = v.get("gaps") or []
                        gaps_str = ("; ".join(str(g) for g in gaps_raw))[:300] or "-"
                        lines.append(f"| {t} | {verdict} | {ci} | {rv} | {ta} | {os_} | {gaps_str} |")
                    lines.append("")

                    # Evidence excerpts for FAIL turns — always show the
                    # highest-severity FAIL turn (it may be beyond turn 3) plus
                    # up to 2 additional earlier failures.
                    fail_turns = [v for v in non_pass if v.get("verdict") == "FAIL"]
                    if fail_turns:
                        # Put worst turn (highest position) first, then keep
                        # up to 2 earlier turns for context.
                        last_fail = fail_turns[-1]
                        earlier_fails = [t for t in fail_turns[:-1]][:2]
                        fail_turns = earlier_fails + [last_fail]
                    evidence_lines: list[str] = []
                    for v in fail_turns[:3]:  # cap at 3
                        t = v.get("turn", "?")
                        agent_resp = v.get("agent_response") or v.get("response") or ""
                        user_msg = v.get("user_message") or v.get("prompt") or ""
                        gaps_raw = v.get("gaps") or []
                        if agent_resp or user_msg:
                            evidence_lines.append(f"_Turn {t}:_")
                            if user_msg:
                                u = _truncate_evidence(str(user_msg), limit=500).replace("\n", "\n> ")
                                evidence_lines.append(f"> **User:** {u}")
                            if agent_resp:
                                a = _truncate_evidence(str(agent_resp), limit=1000).replace("\n", "\n> ")
                                evidence_lines.append(f"> **Agent:** {a}")
                            endpoint = str(v.get("effective_endpoint", "")).strip()
                            if endpoint:
                                evidence_lines.append(f"> **Endpoint:** `{endpoint}`")
                            for gap in gaps_raw:
                                evidence_lines.append(f"> **Gap:** {str(gap)[:400]}")
                            evidence_lines.append("")
                    if evidence_lines:
                        lines.append("**Evidence (FAIL turns):**")
                        lines.append("")
                        lines.extend(evidence_lines)

            covered = sorted(set(
                name
                for v in sr.verdicts
                for name in (v.get("agents_mentioned") or []) + (v.get("tools_mentioned") or [])
            ))
            if covered:
                # Match against both canonical component names and successfully-resolved
                # mentions (evidence_mentions/aliases_seen) so a mention that matched via
                # the descriptive-name/fuzzy/config-alias/sole-agent tiers in
                # _build_coverage_map (e.g. a persona name entirely unlike the SBOM's
                # structural name) is correctly tagged "matched" here too, instead of
                # this independently re-deriving a stricter, canonical-name-only check.
                coverage_norm = {normalise_name(c.component_name) for c in result.coverage}
                for c in result.coverage:
                    coverage_norm.update(normalise_name(m) for m in c.evidence_mentions)
                    coverage_norm.update(normalise_name(m) for m in c.aliases_seen)
                tagged = [f"{name} ({'matched' if normalise_name(name) in coverage_norm else 'unmatched'})" for name in covered]
                lines.append(f"**Covered components**: {', '.join(tagged)}")
                lines.append("")

    # Coverage Map — only components actually exercised during the scan, to
    # keep the report focused (the full SBOM catalog is available separately).
    if result.coverage:
        matched_coverage = [cov for cov in result.coverage if cov.exercised]
        lines.append("## Coverage Map")
        lines.append("")
        if matched_coverage:
            lines.append("| Component | Type | Exercised | Within Policy | Deviations | Aliases Seen |")
            lines.append("|-----------|------|-----------|---------------|------------|--------------|")
            for cov in matched_coverage:
                ex = "Yes" if cov.exercised else "No"
                wp = "Yes" if cov.exercised_within_policy else ("No" if cov.exercised else "-")
                dev_count = len(cov.deviations)
                aliases = ", ".join(cov.aliases_seen[:3]) if cov.aliases_seen else "-"
                lines.append(f"| {cov.component_name} | {cov.node_type} | {ex} | {wp} | {dev_count} | {aliases} |")
            lines.append("")
        else:
            lines.append("_No components were exercised during this scan._")
            lines.append("")
        unmatched_mentions = sorted({m for cov in result.coverage for m in (cov.unmatched_mentions or [])})
        if unmatched_mentions:
            lines.append("**Unmatched Mentions:**")
            lines.append("")
            for mention in unmatched_mentions:
                lines.append(f"- {mention}")
            lines.append("")
        if result.scenario_results:
            render_behavior_coverage_evidence(lines, matched_coverage, result.scenario_results)

        # Coverage mapping diagnostics — surfaces hallucinated/unmapped entity mentions
        _cov_diag = result.coverage_mapping_diagnostics or {}
        _unmapped = [str(e) for e in (_cov_diag.get("mentioned_entities_unmapped") or [])]
        _rt_unmapped = int(_cov_diag.get("runtime_only_unmapped_endpoint_count") or 0)
        _type_mismatch = int(_cov_diag.get("component_type_mismatch_count") or 0)
        _descriptive_matches = int(_cov_diag.get("descriptive_name_match_count") or 0)
        _fuzzy_matches = int(_cov_diag.get("fuzzy_match_count") or 0)
        _sole_agent_matches = int(_cov_diag.get("sole_agent_fallback_count") or 0)
        _config_alias_matches = int(_cov_diag.get("config_alias_match_count") or 0)
        _alias_tier_total = _descriptive_matches + _fuzzy_matches + _sole_agent_matches + _config_alias_matches
        if _unmapped or _rt_unmapped or _type_mismatch or _alias_tier_total:
            lines.append("### Coverage Diagnostics")
            lines.append("")
            if _alias_tier_total:
                lines.append(
                    f"**Alias-resolved mentions** ({_alias_tier_total}) — mentions phrased "
                    "differently than the SBOM's component name but resolved with confidence, "
                    "so they count as exercised rather than unmapped:"
                )
                lines.append("")
                if _descriptive_matches:
                    lines.append(f"- {_descriptive_matches} via SBOM `descriptive_name` match")
                if _fuzzy_matches:
                    lines.append(f"- {_fuzzy_matches} via fuzzy text-similarity match")
                if _sole_agent_matches:
                    lines.append(f"- {_sole_agent_matches} via sole-agent self-reference fallback")
                if _config_alias_matches:
                    lines.append(f"- {_config_alias_matches} via configured `component_aliases`")
                lines.append("")
            if _unmapped:
                lines.append(
                    f"**Unmapped entity mentions** ({len(_unmapped)}) — "
                    "names mentioned by the app at runtime that are not in the SBOM. "
                    "May indicate hallucination or undeclared components:"
                )
                lines.append("")
                for entity in _unmapped:
                    lines.append(f"- `{entity}`")
                lines.append("")
            if _rt_unmapped:
                lines.append(
                    f"**Runtime-only endpoints**: {_rt_unmapped} endpoint(s) observed at runtime "
                    "but absent from the SBOM — added as unmatched coverage entries."
                )
                lines.append("")
            if _type_mismatch:
                lines.append(
                    f"**Component type mismatches**: {_type_mismatch} mention(s) where the runtime "
                    "component type did not match the SBOM node type."
                )
                lines.append("")

    # Behavior SBOM Coverage (expanded objective tracking)
    if result.coverage_objectives:
        lines.append("## Behavior SBOM Coverage")
        lines.append("")
        lines.append(
            f"Agent/tool coverage: **{result.coverage_percentage * 100:.0f}%** | "
            f"Endpoint coverage: **{result.endpoint_coverage_pct * 100:.0f}%** | "
            f"Guardrail coverage: **{result.guardrail_coverage_pct * 100:.0f}%**"
        )
        lines.append("")
        mode_groups: dict[str, list] = {}
        for obj in result.coverage_objectives:
            mode_groups.setdefault(obj.behavior_mode, []).append(obj)

        mode_order = ["dynamic", "static", "metadata_only", "not_behavior_exercisable"]
        mode_labels = {
            "dynamic": "Dynamic (chat-exercisable)",
            "static": "Static (alignment check)",
            "metadata_only": "Metadata-only (risk context)",
            "not_behavior_exercisable": "Not behavior-exercisable (infrastructure)",
        }
        for mode in mode_order:
            objs = mode_groups.get(mode, [])
            if not objs:
                continue
            lines.append(f"### {mode_labels.get(mode, mode)} ({len(objs)})")
            lines.append("")
            lines.append("| Surface | Node/Edge | Status | Notes |")
            lines.append("|---------|-----------|--------|-------|")
            for obj in objs[:30]:  # cap per section to keep report readable
                surface = obj.surface_type
                name = (obj.node_name or obj.relationship_type or "—")[:50]
                status = obj.status
                reason = obj.reason[:60] if obj.reason else "—"
                lines.append(f"| {surface} | {name} | {status} | {reason} |")
            if len(objs) > 30:
                lines.append(f"| … | _{len(objs) - 30} more_ | | |")
            lines.append("")

    # Deviations — grouped by scenario → turn, with full turn evidence
    dev_turns: list[tuple[str, dict]] = []  # (scenario_name, verdict_dict)
    for sr in result.scenario_results:
        for v in sr.verdicts:
            if v.get("deviations"):
                dev_turns.append((sr.scenario_name, v))

    if dev_turns:
        lines.append("## Deviation Evidence (per-turn)")
        lines.append("")
        lines.append(
            "Each entry below is per-turn evidence. Multiple entries can map to a single unique finding in the summary."
        )
        lines.append("")
        shown = 0
        for scenario_name, v in dev_turns:
            if shown >= 20:
                remaining = len(dev_turns) - shown
                lines.append(f"_… {remaining} more deviation turn(s) omitted._")
                lines.append("")
                break
            turn = v.get("turn", "?")
            verdict_label = v.get("verdict", "FAIL")
            score = v.get("overall_score")
            score_str = f" — Score: {score:.2f}" if isinstance(score, (int, float)) else ""
            user_msg = v.get("user_message") or ""
            agent_resp = v.get("agent_response") or ""
            gaps = v.get("gaps") or []
            devs = v.get("deviations") or []

            for dev in devs:
                dtype = dev.get("deviation_type", "unknown")
                desc = dev.get("description", "")
                sev = _norm_sev(dev.get("severity", ""))
                lines.append(f"**[{sev}] {dtype}**")
                lines.append("")
                lines.append(desc)
                lines.append("")
                lines.append(f"*Scenario*: {scenario_name} — Turn {turn} ({verdict_label}{score_str})")
                lines.append("")
                if user_msg or agent_resp:
                    lines.append(f"**Evidence — Turn {turn} ({verdict_label}):**")
                    lines.append("")
                    if user_msg:
                        lines.append("> **User:** " + _truncate_evidence(user_msg, limit=400).replace("\n", " "))
                    if agent_resp:
                        lines.append("> **Agent:** " + _truncate_evidence(agent_resp, limit=800).replace("\n", " "))
                    lines.append("")
                if gaps:
                    lines.append("**Gaps:**")
                    for gap in gaps:
                        lines.append(f"- {gap}")
                    lines.append("")
                hint = dev.get("remediation") or _deviation_remediation_hint(dtype, desc)
                if hint:
                    lines.append(f"**Remediation:** {hint}")
                    lines.append("")
            shown += 1

    # Behavioral Gap Summary — findings promoted from LLM-generated gap strings
    gap_findings = [f for f in result.dynamic_findings if f.get("finding_type") in _GAP_FINDING_TYPES]
    if gap_findings:
        stats = result.gap_aggregation_stats or {}
        threshold = int(stats.get("min_occurrences_threshold", 0))
        lines.append("## Behavioral Gap Summary")
        lines.append("")
        lines.append(
            "Buckets are keyed by `(finding_type, affected_component)` and promoted "
            f"to findings at threshold >= {threshold}."
        )
        lines.append("")
        lines.append("| Stage | Count |")
        lines.append("|---|---:|")
        lines.append(f"| Raw gap observations | {int(stats.get('raw_gap_observations', 0))} |")
        lines.append(f"| Deduplicated unique gaps | {int(stats.get('unique_gap_observations', 0))} |")
        lines.append(f"| Buckets formed | {int(stats.get('buckets_formed', 0))} |")
        lines.append(f"| Buckets emitted as findings (>= {threshold}) | {int(stats.get('buckets_emitted', len(gap_findings)))} |")
        lines.append(f"| Buckets dropped (below threshold) | {int(stats.get('buckets_dropped', 0))} |")
        lines.append("")
        for ftype in ("CAPABILITY_GAP", "INTENT_MISALIGNMENT", "TOOL_CHAIN_BROKEN", "POLICY_VIOLATION"):
            type_findings = [f for f in gap_findings if f.get("finding_type") == ftype]
            if not type_findings:
                continue
            label = ftype.replace("_", " ").title()
            lines.append(f"### {label}")
            lines.append("")
            lines.append("| Component | Occurrences | Sample Gaps |")
            lines.append("|---|---|---|")
            for gf in type_findings:
                comp = gf.get("affected_component", "unknown")
                fid = gf.get("finding_id", "")
                count = gf.get("occurrence_count", 1)
                sample = "; ".join(str(g)[:120] for g in (gf.get("gap_texts") or [gf.get("description", "")])[:3])
                comp_display = f"{comp} ({fid})" if fid else comp
                lines.append(f"| {comp_display} | {count} | {sample} |")
            lines.append("")

    # Dynamic Analysis Findings (policy violations, canary hits — not gap aggregates)
    policy_findings = [f for f in result.dynamic_findings if f.get("finding_type") not in _GAP_FINDING_TYPES]
    if policy_findings:
        lines.append("## Dynamic Analysis Findings")
        lines.append("")
        for finding in policy_findings:
            render_finding_block(lines, finding, heading_level="###")
            _render_behavior_attack_steps(lines, finding)

    # Recommendations — behavior-specific, no redteam equivalent
    if result.recommendations:
        lines.append("## Recommendations")
        lines.append("")
        for rec in result.recommendations:
            lines.append(f"### [{rec.priority.upper()}] {rec.recommendation_type}: {rec.description}")
            if rec.component and rec.component != "unknown":
                lines.append(f"*Component*: {rec.component}")
            lines.append("")
            lines.append(f"*Rationale*: {rec.rationale}")
            lines.append("")

    # Remediation Plan — same shared renderer redteam uses, for a consistent format
    if result.remediation_plan:
        render_remediation_plan_section(lines, result.remediation_plan)

    if getattr(meta, "verbose", False) and _scenario_details:
        lines.append("## Diagnostics")
        lines.append("")
        lines.append(
            f"_Scenario traces capped at {_MAX_DIAG_TURNS_PER_SCENARIO} turn(s) per scenario, "
            f"{_MAX_DIAG_SNIPPET_CHARS} chars per request/response snippet._"
        )
        lines.append("")
        render_scenario_details_section(
            lines,
            _truncate_scenario_details(_scenario_details),
            truncate_limit=_MAX_DIAG_SNIPPET_CHARS,
        )

    return "\n".join(lines)


def _truncate_scenario_details(scenario_details: list) -> list:
    truncated = []
    for sd in scenario_details[:_MAX_DIAG_SCENARIOS]:
        turns = list(sd.turns[:_MAX_DIAG_TURNS_PER_SCENARIO])
        truncated.append(
            type(sd)(
                index=sd.index,
                title=sd.title,
                scenario_type=sd.scenario_type,
                goal_or_type=sd.goal_or_type,
                status=sd.status,
                turns=turns,
                had_finding=sd.had_finding,
            )
        )
    return truncated


def _build_behavior_diagnostics(result: "BehaviorAnalysisResult") -> dict[str, Any]:
    traces: list[dict[str, Any]] = []
    for sr in result.scenario_results[:_MAX_DIAG_SCENARIOS]:
        turns: list[dict[str, Any]] = []
        for verdict in (sr.verdicts or [])[:_MAX_DIAG_TURNS_PER_SCENARIO]:
            turns.append(
                {
                    "turn": verdict.get("turn", 0),
                    "verdict": verdict.get("verdict", ""),
                    "request": _truncate_evidence(
                        str(verdict.get("user_message") or verdict.get("prompt") or ""),
                        limit=_MAX_DIAG_SNIPPET_CHARS,
                    ),
                    "response": _truncate_evidence(
                        str(verdict.get("agent_response") or verdict.get("response") or ""),
                        limit=_MAX_DIAG_SNIPPET_CHARS,
                    ),
                    "gaps": [
                        _truncate_evidence(str(gap), limit=200)
                        for gap in (verdict.get("gaps") or [])[:4]
                    ],
                }
            )

        traces.append(
            {
                "scenario_name": sr.scenario_name,
                "scenario_type": sr.scenario_type,
                "turns": turns,
                "turns_truncated": max(
                    0,
                    len(sr.verdicts or []) - _MAX_DIAG_TURNS_PER_SCENARIO,
                ),
            }
        )

    return {
        "execution_notes": {
            "scenarios_cap": _MAX_DIAG_SCENARIOS,
            "turns_per_scenario_cap": _MAX_DIAG_TURNS_PER_SCENARIO,
            "snippet_char_cap": _MAX_DIAG_SNIPPET_CHARS,
        },
        "scenario_traces": traces,
    }


def _render_behavior_attack_steps(lines: list[str], finding: dict) -> None:
    """Append evidence lines for behavior findings that carry attack_steps."""
    steps = finding.get("attack_steps") or []
    if not steps:
        return
    hit_steps = [s for s in steps if s.get("succeeded") and s.get("step_type") == "BEHAVIOR_TURN"]
    if not hit_steps:
        return
    lines.append("**Evidence — triggering turn(s):**")
    lines.append("")
    for step in hit_steps:
        t = step.get("turn", "?")
        verdict = step.get("verdict", "")
        lines.append(f"_Turn {t} ({verdict}):_")
        lines.append("")
        payload = (step.get("payload") or "").strip()
        if payload:
            lines.append(f"> **User:** {payload[:400]}")
        response = (step.get("response") or "").strip()
        if response:
            from nuguard.output.validation_report import _clean_response_for_display
            lines.append(f"> **Agent:** {_clean_response_for_display(response)[:800]}")
        for v in step.get("violations") or []:
            lines.append(f"> **Violation:** {v.get('type', '')} — {v.get('evidence', '')[:200]}")
        canary_hits = step.get("canary_hits") or []
        if canary_hits:
            lines.append(f"> **Canary hit:** {', '.join(str(h) for h in canary_hits)}")
        lines.append("")


def _deviation_remediation_hint(deviation_type: str, description: str) -> str:
    """Fallback, template-based remediation hint for a deviation.

    Used only when LLM-authored text (``deviation["remediation"]``, set by
    :func:`nuguard.remediation.deviation.enrich_deviation_remediations_async`)
    is unavailable, e.g. no remediation LLM client configured.
    """
    desc_lower = description.lower()
    if deviation_type == "intent_misalignment":
        return (
            "Tighten the system prompt's allowed-topic definition so the agent does not "
            "refuse or deflect requests that fall within the declared scope."
        )
    if deviation_type == "capability_gap":
        if "not exercised" in desc_lower or "component not exercised" in desc_lower:
            return (
                "Update the agent's routing or system prompt to ensure the required component "
                "is invoked for this request type."
            )
        if "invalid response" in desc_lower or "refusal" in desc_lower or "refused" in desc_lower:
            return (
                "Fix error-handling logic or system prompt instructions so the agent "
                "provides a substantive response rather than refusing a valid request."
            )
        return (
            "Review the agent's system prompt and tool configuration to close this "
            "capability gap."
        )
    if deviation_type == "policy_violation":
        return (
            "Align the agent's system prompt with the cognitive policy, or update the "
            "policy to reflect the intended allowed topics."
        )
    if deviation_type == "no_response":
        return (
            "Investigate why the target returned an empty response and add appropriate "
            "error handling or retry logic."
        )
    return "Review the agent's system prompt and configuration to address this deviation."


def _scenario_coverage_table(scenario_results: list[Any]) -> list[str]:
    """Build a Scenario Coverage summary table (mirrors redteam report format).

    Args:
        scenario_results: List of ScenarioResult objects.

    Returns:
        List of Markdown lines including the table and a summary footer.
    """
    lines: list[str] = []
    lines.append("## Scenario Coverage")
    lines.append("")
    lines.append("| # | Scenario | Type | Score | Verdict | Finding | Turns | Duration | Avg/Turn |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    total_duration_s = 0.0
    total_turns = 0
    findings_count = 0

    for i, sr in enumerate(scenario_results):
        # Duration: sum latency_ms across all verdict dicts
        duration_ms = sum(v.get("latency_ms", 0) for v in (sr.verdicts or []))
        duration_s = duration_ms / 1000.0
        total_duration_s += duration_s
        total_turns += sr.total_turns or 0

        avg_per_turn = duration_s / sr.total_turns if sr.total_turns else 0.0

        # Verdict from score thresholds (mirrors judge.py constants)
        score = sr.overall_score or 0.0
        if score >= 3.5:
            verdict = "PASS"
        elif score >= 2.0:
            verdict = "PARTIAL"
        else:
            verdict = "FAIL"

        has_finding = bool(sr.deviations) or score < 2.0
        if has_finding:
            findings_count += 1
        finding_cell = "**YES**" if has_finding else "no"

        name = sr.scenario_name or ""
        if len(name) > 45:
            name = name[:45] + "…"
        sc_type = (sr.scenario_type or "").replace("_", " ")

        lines.append(
            f"| {i + 1} | {name} | {sc_type} | {score:.2f} | {verdict} | {finding_cell} | "
            f"{sr.total_turns} | {duration_s:.1f}s | {avg_per_turn:.1f}s |"
        )

    n = len(scenario_results)
    avg_scenario = total_duration_s / n if n else 0.0
    avg_turn = total_duration_s / total_turns if total_turns else 0.0
    lines.append("")
    lines.append(
        f"_{n} scenario(s) executed — {findings_count} with finding(s). "
        f"Total: {total_duration_s:.1f}s | Avg per scenario: {avg_scenario:.1f}s | "
        f"Avg per turn: {avg_turn:.1f}s_"
    )
    lines.append("")
    return lines


def to_text(result: "BehaviorAnalysisResult", meta: "ReportMeta | None" = None) -> None:
    """Print Rich-formatted text report to console.

    Args:
        result: Complete BehaviorAnalysisResult.
        meta: Optional report metadata.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    # Summary panel
    summary_lines = [
        f"Intent:        {result.intent.app_purpose or 'not determined'}",
        f"Risk Score:    {result.overall_risk_score:.1f} / 100",
        f"Coverage:      {result.coverage_percentage * 100:.0f}%",
        f"Alignment:     {result.intent_alignment_score:.2f} / 5.0",
        f"Findings:      {len(result.static_findings) + len(result.dynamic_findings)}",
        f"Outcome:       {result.scan_outcome}",
    ]
    console.print(Panel("\n".join(summary_lines), title="Behavior Analysis Summary", border_style="blue"))

    if result.config_notes:
        console.print(
            Panel(
                "\n".join(result.config_notes),
                title="⚠ Configuration Notes",
                border_style="yellow",
            )
        )

    # Coverage table
    if result.coverage:
        table = Table(title="Component Coverage")
        table.add_column("Component")
        table.add_column("Type")
        table.add_column("Exercised")
        table.add_column("Within Policy")
        table.add_column("Deviations")
        for cov in result.coverage:
            ex = "[green]Yes[/green]" if cov.exercised else "[red]No[/red]"
            wp = "[green]Yes[/green]" if cov.exercised_within_policy else ("[red]No[/red]" if cov.exercised else "-")
            console.print_row = table.add_row  # type: ignore[attr-defined]
            table.add_row(cov.component_name, cov.node_type, ex, wp, str(len(cov.deviations)))
        console.print(table)

    # Findings
    all_findings = list(result.static_findings) + list(result.dynamic_findings)
    if all_findings:
        console.print("\n[bold]Findings[/bold]")
        for f in all_findings:
            sev = str(f.get("severity", "")).upper()
            color = {"CRITICAL": "red", "HIGH": "red", "MEDIUM": "yellow", "LOW": "blue"}.get(sev, "white")
            console.print(f"  [{color}][{sev}][/{color}] {f.get('title', '')}")
