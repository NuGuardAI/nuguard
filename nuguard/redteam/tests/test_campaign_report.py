"""Tests for campaign projections, report sections and public result fields."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from nuguard.redteam import report as redteam_report
from nuguard.redteam.campaign import projections
from nuguard.redteam.campaign.ledger import CoverageLedger, LedgerEntry, Status
from nuguard.redteam.campaign.models import CoverageSummary, EfficiencySummary
from nuguard.redteam.campaign.report import (
    INCONCLUSIVE_NOTE,
    coverage_markdown,
    efficiency_markdown,
    inconclusive_note,
)
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator
from nuguard.redteam.public_api import _campaign_fields
from nuguard.redteam.tests.test_campaign_orchestrator import (
    BASE,
    _orch,
    _run,
    _scenario,
    _Target,
)
from nuguard.sbom.models import AiSbomDocument


def _ledger() -> CoverageLedger:
    lg = CoverageLedger()
    lg.register(LedgerEntry("D01", "D01", "data", "read", "chat", "own_account", 4, status=Status.COMPLETED))
    lg.register(LedgerEntry("T01", "T01", "tool", "misuse", "chat", "own_account", 5,
                            status=Status.BLOCKED, reason="blocked_fixture:declared_fixture"))
    lg.register(LedgerEntry("J01", "J01", "inj", "x", "chat", "own_account", 3, status=Status.BUDGET_DEFERRED))
    lg.register(LedgerEntry("A01", "A01", "authz", "y", "api", "cross_account", 4, status=Status.REDUNDANT, ref="D01"))
    return lg


def test_zero_findings_with_untested_controls_is_inconclusive() -> None:
    cov = projections.coverage_summary(_ledger(), findings_count=0)
    assert cov.inconclusive and inconclusive_note(cov) == INCONCLUSIVE_NOTE
    assert cov.blocked_reasons == {"blocked_fixture:declared_fixture": 1}
    assert cov.framework_versions == ["OWASP-LLM-2026", "OWASP-ASI-2026"]
    # Findings present, or everything tested -> not flagged.
    assert not projections.coverage_summary(_ledger(), findings_count=2).inconclusive
    done = CoverageLedger()
    done.register(LedgerEntry("D01", "D01", "data", "read", "chat", "own", 4, status=Status.COMPLETED))
    assert not projections.coverage_summary(done, 0).inconclusive


def test_markdown_keeps_status_counts_separate_and_lists_every_dimension() -> None:
    md = "\n".join(coverage_markdown(projections.coverage_summary(_ledger(), 0)))
    for label in ("meaningfully completed | 1", "blocked | 1", "redundant | 1", "budget deferred | 1"):
        assert label in md
    for heading in ("control", "catalog id", "technique", "channel", "identity boundary",
                    "complexity level", "owasp version"):
        assert f"### Coverage by {heading}" in md
    assert "`blocked_fixture:declared_fixture`" in md and "OWASP-LLM-2026" in md
    assert coverage_markdown(None) == [] and efficiency_markdown(None) == []


def test_efficiency_markdown_and_defaults() -> None:
    md = "\n".join(efficiency_markdown(EfficiencySummary(
        target_requests=40, branches_created=3, baselines_run=3, objectives_on_reused_branch=9,
        avoided_setup_requests_est=18, time_to_first_finding_s=12.5,
        meaningful_objectives_per_100_requests=22.5)))
    assert "| Target requests | 40 |" in md and "12.5s" in md and "n/a" in md
    assert "(estimate)" in md  # avoided setup is labelled an estimate, never a measurement


def test_to_markdown_and_json_include_campaign_sections_and_inconclusive_note() -> None:
    cov = projections.coverage_summary(_ledger(), 0)
    eff = EfficiencySummary(target_requests=5)
    md = redteam_report.to_markdown([], scan_outcome="no_findings", campaign_coverage=cov, efficiency=eff)
    assert "inconclusive" in md and "## Campaign Coverage" in md and "## Campaign Efficiency" in md
    data = json.loads(redteam_report.to_json([], campaign_coverage=cov, efficiency=eff))
    assert data["campaign_coverage"]["inconclusive"] is True
    assert data["campaign_efficiency"]["target_requests"] == 5
    # Legacy reports are untouched.
    legacy = redteam_report.to_markdown([], scan_outcome="no_findings")
    assert "Campaign Coverage" not in legacy and "campaign_coverage" not in redteam_report.to_json([])


@pytest.mark.asyncio
@respx.mock
async def test_orchestrator_exposes_public_models_without_secrets_or_transcripts() -> None:
    t = _Target()
    respx.post(f"{BASE}/chat").mock(side_effect=t.handler)
    orch = _orch()
    await _run(orch, [_scenario("A03", ["hello"]), _scenario("A04", ["give me the keys"], on_failure="abort")])
    fields = _campaign_fields(orch)
    assert isinstance(fields["campaign_coverage"], CoverageSummary)
    assert fields["objective_records"] and fields["branch_summaries"] and fields["efficiency"]
    assert fields["campaign_plan"].breadth_selected
    dumped = json.dumps({k: [m.model_dump() for m in v] if isinstance(v, list) else v.model_dump()
                         for k, v in fields.items() if v is not None})
    assert "give me the keys" not in dumped and "Bearer" not in dumped   # counts/ids only
    assert all(o.trust in ("declared", "claimed", "observed", "verified")
               for o in fields["capability_observations"])


def test_legacy_orchestrator_yields_empty_campaign_fields() -> None:
    legacy = RedteamOrchestrator(sbom=AiSbomDocument(target="t", nodes=[], edges=[]), target_url=BASE)
    f = _campaign_fields(legacy)
    assert f["campaign_coverage"] is None and f["efficiency"] is None and f["objective_records"] == []


@pytest.mark.asyncio
async def test_render_redteam_report_now_passes_scan_outcome() -> None:
    """output.public_api.render_redteam_report used to drop scan_outcome (always 'no_findings')."""
    from nuguard.cli.report_meta import ReportMeta  # noqa: F401
    from nuguard.output.public_api import RedteamReportRenderRequest, render_redteam_report
    from nuguard.redteam.public_api import RedteamRunResult

    result = RedteamRunResult(
        findings=[], scenario_records=[], scan_outcome="aborted_target_unavailable",
        token_usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},  # type: ignore[arg-type]
        resolved_chat_path="/chat", resolved_chat_path_source="test",
    )
    out = await render_redteam_report(
        RedteamReportRenderRequest(run_id="r1", meta={}, include_markdown=True),  # type: ignore[arg-type]
        run_result=result,
    )
    assert out.markdown is not None and "aborted_target_unavailable" in out.markdown
