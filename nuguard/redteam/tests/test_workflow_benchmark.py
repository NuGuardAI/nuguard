from __future__ import annotations

from pathlib import Path

import yaml

from nuguard.redteam.workflows.benchmark import score, solved_workflows
from nuguard.redteam.workflows.library import load_template

_BENCH = Path(__file__).resolve().parents[3] / "tests/apps/owasp-finbot/ctf-benchmark.yaml"


def test_every_benchmark_workflow_exists_in_template() -> None:
    ids = {s.id for s in load_template("owasp-finbot")}
    for ch in yaml.safe_load(_BENCH.read_text())["challenges"]:
        if ch.get("workflow"):
            assert ch["workflow"] in ids, ch["id"]


def test_benchmark_covers_all_19_challenges() -> None:
    assert len(yaml.safe_load(_BENCH.read_text())["challenges"]) == 19


def test_score_counts_solved_attempted_gap_and_na() -> None:
    report = "found wf-finbot-invoice-over-threshold-1a2b3c4d and wf-finbot-poisoned-send-email-deadbeef"
    assert solved_workflows(report) == {"finbot-invoice-over-threshold", "finbot-poisoned-send-email"}
    result = score(_BENCH, report)
    by_id = {r.id: r.status for r in result.results}
    assert by_id["policy-bypass-invoice-threshold"] == "solved"
    assert by_id["policy-bypass-invoice-trust-override"] == "solved"
    assert by_id["data-exfil-carte-blanche"] == "solved"
    assert by_id["rce-shell-shock"] == "attempted"
    assert by_id["recon-invoice"] == "gap"
    assert by_id["labs-guardrail-101"] == "n/a"
    assert (result.solved, result.attemptable) == (3, 17)
    assert "3/17" in result.to_markdown()


def test_markdown_marker_identifies_workflow_without_finding_id() -> None:
    assert solved_workflows("... changed state. [workflow:finbot-status-flip] ...") == {"finbot-status-flip"}
