"""Shared validation report structures and renderers for behavior and redteam reports.

Both :mod:`nuguard.behavior.report` and :mod:`nuguard.redteam.report` import from
here to produce consistent validation run summaries, coverage evidence sections,
and scenario detail transcripts.
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any

from nuguard.output.report_shared import _truncate_evidence


# ---------------------------------------------------------------------------
# Response display helper
# ---------------------------------------------------------------------------


def _clean_response_for_display(text: str) -> str:
    """Return a human-readable version of a raw HTTP response body for report rendering.

    Chat-endpoint responses often return structured JSON such as::

        {"conversation_id": "...", "messages": [{"content": "Hi!", "agent": "..."}], "events": [...]}

    For these, we extract the last message's ``content`` field so the report shows the
    actual agent reply rather than the raw JSON blob.

    For other JSON responses (e.g. REST API objects like ``{"account_number": "..."}``),
    we pretty-print them inside a fenced code block so they remain readable.

    Plain text responses are returned unchanged.
    """
    stripped = text.strip()
    if not stripped.startswith("{") and not stripped.startswith("["):
        return text

    try:
        data = _json.loads(stripped)
    except (ValueError, _json.JSONDecodeError):
        return text

    if not isinstance(data, dict):
        return text

    # Chat-style response: extract the last message content
    msgs = data.get("messages")
    if isinstance(msgs, list) and msgs:
        last_msg = msgs[-1]
        if isinstance(last_msg, dict):
            content = last_msg.get("content") or last_msg.get("text") or ""
            if content:
                return str(content)

    # Structured API response: render as a JSON code block for readability
    try:
        return "```json\n" + _json.dumps(data, indent=2) + "\n```"
    except Exception:
        return text


# ---------------------------------------------------------------------------
# Common data containers
# ---------------------------------------------------------------------------


@dataclass
class TurnDetail:
    turn_number: int
    request: str
    response: str
    passed: bool
    metadata: dict = field(default_factory=dict)


@dataclass
class ScenarioDetail:
    index: int
    title: str
    scenario_type: str
    goal_or_type: str
    status: str  # PASS | FAIL | PARTIAL | FINDING | ABORTED
    turns: list[TurnDetail]
    had_finding: bool


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def extract_redteam_scenario_details(scenario_records: list[Any]) -> list[ScenarioDetail]:
    """Convert a list of ScenarioRecord objects to ScenarioDetail for rendering."""
    details: list[ScenarioDetail] = []
    for idx, r in enumerate(scenario_records, start=1):
        chain_status = getattr(r, "chain_status", "completed") or "completed"
        had_finding = bool(getattr(r, "had_finding", False))
        if had_finding:
            status = "FINDING"
        elif chain_status not in ("completed",):
            status = "ABORTED"
        else:
            status = "PASS"

        turns: list[TurnDetail] = []
        for t_idx, step in enumerate(getattr(r, "steps", []) or [], start=1):
            target = step.get("target_path")
            if target:
                method = step.get("method", "POST")
                sc = step.get("status_code")
                req = f"{method} {target}" + (f" → HTTP {sc}" if sc else "")
            else:
                req = (step.get("payload") or "").strip()
            resp = (step.get("response") or "").strip()
            turns.append(TurnDetail(
                turn_number=t_idx,
                request=req,
                response=resp,
                passed=bool(step.get("succeeded", False)),
                metadata={
                    "step_type": step.get("step_type", ""),
                    "tool_calls": step.get("tool_calls", []),
                    "llm_eval_evidence": step.get("llm_eval_evidence"),
                    "llm_eval_confidence": step.get("llm_eval_confidence"),
                },
            ))

        goal_type = getattr(r, "goal_type", "") or getattr(r, "scenario_type", "") or ""
        details.append(ScenarioDetail(
            index=idx,
            title=getattr(r, "title", f"Scenario {idx}"),
            scenario_type=getattr(r, "scenario_type", ""),
            goal_or_type=goal_type,
            status=status,
            turns=turns,
            had_finding=had_finding,
        ))
    return details


def extract_behavior_scenario_details(scenario_results: list[Any]) -> list[ScenarioDetail]:
    """Convert a list of ScenarioResult objects to ScenarioDetail for rendering."""
    details: list[ScenarioDetail] = []
    for idx, sr in enumerate(scenario_results, start=1):
        score = getattr(sr, "overall_score", 0.0) or 0.0
        if score >= 3.5:
            status = "PASS"
        elif score >= 2.0:
            status = "PARTIAL"
        else:
            status = "FAIL"

        had_finding = bool(getattr(sr, "deviations", None)) or score < 2.0

        turns: list[TurnDetail] = []
        for v in getattr(sr, "verdicts", []) or []:
            req = v.get("user_message") or v.get("prompt") or ""
            resp = v.get("agent_response") or v.get("response") or ""
            verdict = v.get("verdict", "")
            turns.append(TurnDetail(
                turn_number=v.get("turn", len(turns) + 1),
                request=str(req),
                response=str(resp),
                passed=verdict == "PASS",
                metadata={
                    "verdict": verdict,
                    "scores": v.get("scores", {}),
                    "gaps": v.get("gaps", []),
                    "tool_calls": v.get("tool_calls", []),
                },
            ))

        details.append(ScenarioDetail(
            index=idx,
            title=getattr(sr, "scenario_name", f"Scenario {idx}"),
            scenario_type=getattr(sr, "scenario_type", ""),
            goal_or_type=getattr(sr, "scenario_type", ""),
            status=status,
            turns=turns,
            had_finding=had_finding,
        ))
    return details


# ---------------------------------------------------------------------------
# Shared renderers
# ---------------------------------------------------------------------------


def render_validation_summary_bullets(
    lines: list[str],
    *,
    total_scenarios: int,
    passed_scenarios: int,
    failed_scenarios: int,
    total_turns: int,
    type_breakdown: dict[str, int],
) -> None:
    """Append validation run metric bullets into an existing ## Summary section."""
    lines.append(f"- **Total Scenarios**: {total_scenarios}")
    if total_scenarios:
        pct = round(passed_scenarios / total_scenarios * 100)
        lines.append(
            f"- **Success Rate**: {pct}% ({passed_scenarios} passed"
            f" / {failed_scenarios} with finding(s)/failure(s))"
        )
    lines.append(f"- **Total Turns**: {total_turns}")
    if type_breakdown:
        _GOAL_LABEL = {
            "DATA_EXFILTRATION": "Data Exfil",
            "PRIVILEGE_ESCALATION": "Priv Esc",
            "PROMPT_DRIVEN_THREAT": "Prompt Threat",
            "POLICY_VIOLATION": "Policy Viol",
            "TOOL_ABUSE": "Tool Abuse",
            "API_ATTACK": "API Attack",
            "MCP_TOXIC_FLOW": "MCP Toxic",
            "AGENTIC_TRUST_ABUSE": "Agentic Trust",
            "RECON_INFERENCE": "Recon",
        }
        parts = []
        for k, count in sorted(type_breakdown.items(), key=lambda x: -x[1]):
            label = _GOAL_LABEL.get(k, k.replace("_", " ").title())
            parts.append(f"{label}: {count}")
        lines.append(f"- **Test Types**: {' | '.join(parts)}")
    lines.append("")


def render_scenario_details_section(
    lines: list[str],
    scenario_details: list[ScenarioDetail],
    *,
    truncate_limit: int = 1500,
) -> None:
    """Append a ## Scenario Details section with full turn transcripts."""
    if not scenario_details:
        return

    lines.append("## Scenario Details")
    lines.append("")
    lines.append(f"> {len(scenario_details)} scenario(s) — full turn traces.")
    lines.append("")

    for sd in scenario_details:
        status_badge = f"[{sd.status}]"
        lines.append(f"### Scenario {sd.index}: {status_badge} {sd.title}")
        lines.append("")
        type_label = sd.goal_or_type.replace("_", " ").title() if sd.goal_or_type else sd.scenario_type
        turns_count = len(sd.turns)
        lines.append(f"**Type:** {type_label} | **Status:** {sd.status} | **Turns:** {turns_count}")
        lines.append("")

        for td in sd.turns:
            step_type = td.metadata.get("step_type") or ""
            verdict = td.metadata.get("verdict") or ""
            turn_label = step_type or verdict or ""
            success_mark = " ✅" if td.passed else ""
            heading = f"#### Turn {td.turn_number}"
            if turn_label:
                heading += f" — {turn_label}{success_mark}"
            lines.append(heading)
            lines.append("")

            req = _truncate_evidence(td.request, limit=truncate_limit) if td.request else ""
            resp = _truncate_evidence(
                _clean_response_for_display(td.response), limit=truncate_limit
            ) if td.response else ""

            if req:
                lines.append("> **Request:**")
                for req_line in req.splitlines():
                    lines.append(f"> {req_line}")
                lines.append("")
            if resp:
                lines.append("> **Response:**")
                for resp_line in resp.splitlines():
                    lines.append(f"> {resp_line}")
                lines.append("")

            # Optional extras: gaps, LLM eval evidence
            gaps = td.metadata.get("gaps") or []
            if gaps:
                gap_str = "; ".join(str(g) for g in gaps[:3])
                lines.append(f"> **Gaps:** {gap_str}")
                lines.append("")
            llm_ev = td.metadata.get("llm_eval_evidence") or ""
            if llm_ev:
                conf = td.metadata.get("llm_eval_confidence") or ""
                conf_label = f" ({conf})" if conf else ""
                lines.append(f"> **LLM eval{conf_label}:** {_truncate_evidence(llm_ev, limit=400)}")
                lines.append("")

        lines.append("---")
        lines.append("")


def render_behavior_coverage_evidence(
    lines: list[str],
    coverage: list[Any],
    scenario_results: list[Any],
) -> None:
    """Append ## Coverage Evidence section for the behavior report.

    Shows which scenario turn first exercised each AI-SBOM component, and which
    scenarios exercised each Cognitive Policy topic (matched_topic).
    """
    lines.append("## Coverage Evidence")
    lines.append("")

    # Build component → first exercise mapping
    # Each verdict carries agents_mentioned and tools_mentioned lists
    component_first: dict[str, tuple[str, int, str, str]] = {}
    for sr in scenario_results:
        for v in getattr(sr, "verdicts", []) or []:
            mentioned = set(
                (v.get("agents_mentioned") or []) + (v.get("tools_mentioned") or [])
            )
            turn_num = v.get("turn", "?")
            req = v.get("user_message") or v.get("prompt") or ""
            resp = v.get("agent_response") or v.get("response") or ""
            scenario_name = getattr(sr, "scenario_name", "")
            for cov in coverage:
                name = getattr(cov, "component_name", "")
                if name in mentioned and name not in component_first:
                    component_first[name] = (scenario_name, turn_num, str(req), str(resp))

    # Build topic → first exercise mapping
    topic_first: dict[str, str] = {}
    for sr in scenario_results:
        topic = getattr(sr, "matched_topic", None)
        if topic and topic not in topic_first:
            topic_first[topic] = getattr(sr, "scenario_name", "")

    # AI-SBOM Component Coverage Evidence
    lines.append("### AI-SBOM Components")
    lines.append("")
    lines.append("| Component | Type | Status | First Exercised |")
    lines.append("|---|---|---|---|")
    for cov in coverage:
        name = getattr(cov, "component_name", "?")
        node_type = getattr(cov, "node_type", "?")
        exercised = getattr(cov, "exercised", False)
        within_policy = getattr(cov, "exercised_within_policy", False)
        if not exercised:
            status = "Not exercised"
            first = "—"
        elif within_policy:
            status = "Within policy"
            if name in component_first:
                sn, tn, _, _ = component_first[name]
                first = f'Scenario: "{sn}" → turn {tn}'
            else:
                first = "exercised"
        else:
            status = "Policy violation"
            if name in component_first:
                sn, tn, _, _ = component_first[name]
                first = f'Scenario: "{sn}" → turn {tn}'
            else:
                first = "exercised"
        lines.append(f"| {name} | {node_type} | {status} | {first} |")
    lines.append("")

    # Evidence excerpts for exercised components
    evidence_items = [
        (cov, component_first[getattr(cov, "component_name", "")])
        for cov in coverage
        if getattr(cov, "exercised", False)
        and getattr(cov, "component_name", "") in component_first
    ]
    if evidence_items:
        for cov, (sn, tn, req, resp) in evidence_items:
            name = getattr(cov, "component_name", "?")
            lines.append(f"#### Evidence: {name}")
            lines.append("")
            lines.append(f"**Scenario:** {sn} — Turn {tn}")
            lines.append("")
            if req:
                lines.append("> **Request:** " + _truncate_evidence(req, limit=300).replace("\n", " "))
            if resp:
                clean_resp = _clean_response_for_display(resp)
                lines.append("> **Response:** " + _truncate_evidence(clean_resp, limit=400).replace("\n", " "))
            lines.append("")

    # Cognitive Policy Topic Coverage
    topics_from_scenarios = sorted(topic_first.keys())
    untested_topics: list[str] = []

    lines.append("### Cognitive Policy Topics")
    lines.append("")
    if not topics_from_scenarios and not untested_topics:
        lines.append("_No matched topics recorded — ensure scenarios carry `matched_topic`._")
        lines.append("")
    else:
        lines.append("| Topic | Exercised | Scenario |")
        lines.append("|---|---|---|")
        for topic in topics_from_scenarios:
            sn = topic_first[topic]
            lines.append(f"| {topic} | Yes | {sn} |")
        for topic in untested_topics:
            lines.append(f"| {topic} | No | — |")
        lines.append("")
