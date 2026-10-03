"""Markdown/JSON projections of campaign coverage quality and efficiency.

Coverage is reported *separately from findings*: planned, applicable, attempted,
meaningfully completed, confirmed, blocked, redundant, disabled and
budget-deferred counts stay distinct, and zero findings with untested controls is
called out as inconclusive rather than a clean pass.
"""
from __future__ import annotations

from typing import Any

from .models import CoverageSummary, EfficiencySummary

_ORDER = (
    "planned", "applicable", "attempted", "meaningfully_completed", "confirmed",
    "blocked", "redundant", "disabled", "budget_deferred",
)
_DIMENSIONS = (
    ("by_control", "Control"), ("by_catalog_id", "Catalog ID"), ("by_technique", "Technique"),
    ("by_channel", "Channel"), ("by_identity_boundary", "Identity boundary"),
    ("by_level", "Complexity level"), ("by_owasp_version", "OWASP version"),
)

INCONCLUSIVE_NOTE = (
    "> **Note:** No findings were produced, but some applicable controls were not tested "
    "(blocked, budget-deferred or unattempted). Treat this run as **inconclusive**, not a "
    "clean pass — see the coverage tables below."
)


def inconclusive_note(coverage: CoverageSummary | dict[str, Any] | None) -> str:
    cov = _cov(coverage)
    return INCONCLUSIVE_NOTE if cov is not None and cov.inconclusive else ""


def _cov(c: CoverageSummary | dict[str, Any] | None) -> CoverageSummary | None:
    if c is None:
        return None
    return c if isinstance(c, CoverageSummary) else CoverageSummary.model_validate(c)


def _eff(e: EfficiencySummary | dict[str, Any] | None) -> EfficiencySummary | None:
    if e is None:
        return None
    return e if isinstance(e, EfficiencySummary) else EfficiencySummary.model_validate(e)


def coverage_markdown(coverage: CoverageSummary | dict[str, Any] | None) -> list[str]:
    cov = _cov(coverage)
    if cov is None:
        return []
    lines = ["## Campaign Coverage", ""]
    lines += ["| Status | Objectives |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {cov.counts.get(k, 0)} |" for k in _ORDER]
    lines.append("")
    if cov.blocked_reasons:
        lines += ["**Blocked:** " + ", ".join(
            f"`{r}` ×{n}" for r, n in sorted(cov.blocked_reasons.items())
        ), ""]
    if cov.framework_versions:
        lines += ["**Framework versions:** " + ", ".join(cov.framework_versions), ""]
    for attr, title in _DIMENSIONS:
        table: dict[str, dict[str, int]] = getattr(cov, attr)
        if not table:
            continue
        lines += [f"### Coverage by {title.lower()}", "", f"| {title} | completed | confirmed | blocked | deferred | other |", "|---|---|---|---|---|---|"]
        for key in sorted(table):
            c = table[key]
            done, conf = c.get("meaningfully_completed", 0), c.get("confirmed", 0)
            blocked, deferred = c.get("blocked", 0), c.get("budget_deferred", 0)
            other = sum(c.values()) - done - conf - blocked - deferred
            lines.append(f"| {key} | {done} | {conf} | {blocked} | {deferred} | {other} |")
        lines.append("")
    if cov.unresolved:
        lines += [f"**Unresolved objectives ({len(cov.unresolved)}):** " + ", ".join(cov.unresolved[:40])
                  + (" …" if len(cov.unresolved) > 40 else ""), ""]
    return lines


def efficiency_markdown(efficiency: EfficiencySummary | dict[str, Any] | None) -> list[str]:
    e = _eff(efficiency)
    if e is None:
        return []
    ttf = f"{e.time_to_first_finding_s:.1f}s" if e.time_to_first_finding_s is not None else "n/a"
    ttc = (
        f"{e.time_to_representative_coverage_s:.1f}s"
        if e.time_to_representative_coverage_s is not None else "n/a"
    )
    return [
        "## Campaign Efficiency", "",
        "| Metric | Value |", "|---|---|",
        f"| Target requests | {e.target_requests} |",
        f"| Meaningful objectives per 100 requests | {e.meaningful_objectives_per_100_requests} |",
        f"| LLM calls / est. tokens | {e.llm_calls} / {e.llm_est_tokens} |",
        f"| Branches created / baselines run | {e.branches_created} / {e.baselines_run} |",
        f"| Objectives on a reused branch | {e.objectives_on_reused_branch} |",
        f"| Avoided setup requests (estimate) | {e.avoided_setup_requests_est} |",
        f"| Retry/cooldown delay | {e.retry_delay_seconds:.1f}s |",
        f"| Time to first finding | {ttf} |",
        f"| Time to representative coverage | {ttc} |",
        "",
    ]


def to_json_fields(
    coverage: CoverageSummary | dict[str, Any] | None,
    efficiency: EfficiencySummary | dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cov, eff = _cov(coverage), _eff(efficiency)
    if cov is not None:
        out["campaign_coverage"] = cov.model_dump(mode="json")
    if eff is not None:
        out["campaign_efficiency"] = eff.model_dump(mode="json")
    return out
