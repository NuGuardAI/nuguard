"""Risk benchmark adapter for NuGuard's current SBOM + vulnerability workflow.

The legacy toolbox risk fixtures expect policy/control style findings. NuGuard's
current OSS surface produces SBOMs plus rule-based vulnerability findings, so
this script adapts those findings into the older evaluation contract as a best
effort benchmark harness.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from nuguard.analysis.plugins.nga_rules import NgaRulesPlugin as VulnerabilityScannerPlugin
from nuguard.sbom.tests.test_toolbox.evaluate_risk import evaluate_risk_assessment

from .evaluate import (
    FIXTURES_DIR,
    generate_sbom_cli,
    generate_sbom_python,
    materialize_cached_fixture,
)
from .schemas_risk import RiskBenchmarkSuiteResult, RiskGroundTruth


def load_risk_ground_truth(repo_name: str) -> RiskGroundTruth:
    path = FIXTURES_DIR / repo_name / "risk_ground_truth.json"
    if not path.exists():
        raise FileNotFoundError(f"Risk ground truth not found: {path}")
    return RiskGroundTruth.model_validate_json(path.read_text(encoding="utf-8"))


def list_risk_benchmarks() -> list[str]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(
        item.name
        for item in FIXTURES_DIR.iterdir()
        if item.is_dir() and (item / "risk_ground_truth.json").exists()
    )


def _score_findings(findings: list[dict[str, Any]]) -> int:
    if not findings:
        return 0
    severity_points = {"CRITICAL": 90, "HIGH": 70, "MEDIUM": 45, "LOW": 20, "INFO": 10}
    max_score = max(severity_points.get(str(item.get("severity", "")).upper(), 0) for item in findings)
    density_bonus = min(len(findings) * 5, 20)
    return min(100, max_score + density_bonus)


def _adapt_vulnerability_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    adapted = []
    for item in findings:
        affected = item.get("affected") or []
        first_affected = affected[0] if isinstance(affected, list) and affected else ""
        adapted.append(
            {
                "title": item.get("title", ""),
                "severity": str(item.get("severity", "")).upper(),
                "control_id": item.get("rule_id"),
                "policy_name": "NuGuard Vulnerability Rules",
                "affected_file": first_affected,
                "description": item.get("description", ""),
                "evidence": item.get("description", ""),
                "remediation": item.get("remediation", ""),
            }
        )
    return adapted


async def evaluate_repo(repo_name: str, *, mode: str = "python", use_llm: bool = False):
    gt = load_risk_ground_truth(repo_name)
    source_dir = materialize_cached_fixture(repo_name)
    try:
        doc = generate_sbom_cli(source_dir, use_llm=use_llm) if mode == "cli" else generate_sbom_python(source_dir, use_llm=use_llm)
    finally:
        import shutil

        shutil.rmtree(source_dir, ignore_errors=True)

    plugin = VulnerabilityScannerPlugin()
    plugin_result = plugin.run(doc)
    findings = plugin_result.details if isinstance(plugin_result.details, list) else []
    adapted_findings = _adapt_vulnerability_findings(findings)
    risk_score = _score_findings(findings)

    return await evaluate_risk_assessment(
        ground_truth=gt,
        discovered_findings=adapted_findings,
        discovered_covered_controls=[],
        discovered_risk_score=risk_score,
        discovered_red_team_attacks=[],
        discovery_time=0.0,
        risk_time=0.0,
    )


async def evaluate_all(*, mode: str = "python", use_llm: bool = False) -> RiskBenchmarkSuiteResult:
    repos = list_risk_benchmarks()
    results = []
    for repo_name in repos:
        results.append(await evaluate_repo(repo_name, mode=mode, use_llm=use_llm))

    successful = sum(1 for result in results if result.error is None)
    failed = len(results) - successful
    average_quality = sum(result.quality_score for result in results) / len(results) if results else 0.0

    return RiskBenchmarkSuiteResult(
        total_repos=len(results),
        successful_repos=successful,
        failed_repos=failed,
        average_quality_score=average_quality,
        by_repo={result.repo_name: result for result in results},
        evaluated_at=datetime.utcnow().isoformat(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="Evaluate one risk benchmark")
    parser.add_argument("--all", action="store_true", help="Evaluate all risk benchmarks")
    parser.add_argument("--mode", choices=["python", "cli"], default="python")
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if not args.repo and not args.all:
        parser.error("pass --repo <name> or --all")

    if args.repo:
        result = asyncio.run(evaluate_repo(args.repo, mode=args.mode, use_llm=args.llm))
        payload: Any = result.model_dump(mode="json")
    else:
        result = asyncio.run(evaluate_all(mode=args.mode, use_llm=args.llm))
        payload = result.model_dump(mode="json")

    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

