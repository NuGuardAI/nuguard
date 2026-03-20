#!/usr/bin/env python3
"""
Risk Assessment Benchmark Evaluation Script for NuGuard

This script evaluates the accuracy of AI risk assessment by comparing
discovered findings, covered controls, and risk scores against ground truth.

Usage:
    python -m benchmark.evaluate_risk --repo Healthcare-voice-agent
    python -m benchmark.evaluate_risk --all
    python -m benchmark.evaluate_risk --all --output risk_results.json
    python -m benchmark.evaluate_risk --all --verbose
    python -m benchmark.evaluate_risk --repo Healthcare-voice-agent --skip-discovery

Exit Codes:
    0 - Success (quality score >= threshold)
    1 - Failure (quality score < threshold)
    2 - Error (missing ground truth, API failure, etc.)

Environment Variables:
    GEMINI_API_KEY - Required for LLM-based risk assessment
    GITHUB_TOKEN - GitHub personal access token (also loaded from .env)
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

from .schemas_risk import (
    RiskGroundTruth,
    GroundTruthFinding,
    GroundTruthCoveredControl,
    MatchFlexibility,
    RiskBand,
    RiskEvaluationResult,
    RiskBenchmarkSuiteResult,
    RiskTypeMetrics,
    FindingMatchResult,
    CoveredControlMatchResult,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Default paths
BENCHMARK_DIR = Path(__file__).parent
REPOS_DIR = BENCHMARK_DIR / "fixtures"
POLICIES_DIR = BENCHMARK_DIR / "policies"

# Default threshold for CI
DEFAULT_QUALITY_THRESHOLD = 0.70

# Quality score weights (must sum to 1.0)
QUALITY_WEIGHTS = {
    "finding_f1": 0.35,
    "covered_control_f1": 0.25,
    "risk_score_accuracy": 0.15,
    "red_team_coverage": 0.10,
    "severity_distribution": 0.10,
    "mutual_exclusivity": 0.05,
}


def load_risk_ground_truth(repo_name: str) -> RiskGroundTruth:
    """
    Load risk ground truth from repos/{repo_name}/risk_ground_truth.json.

    Args:
        repo_name: Name of the benchmark repository

    Returns:
        Parsed RiskGroundTruth object

    Raises:
        FileNotFoundError: If ground truth file doesn't exist
        ValueError: If ground truth is invalid
    """
    gt_path = REPOS_DIR / repo_name / "risk_ground_truth.json"

    if not gt_path.exists():
        raise FileNotFoundError(f"Risk ground truth not found: {gt_path}")

    with open(gt_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    gt = RiskGroundTruth.model_validate(data)

    # Validate internal consistency
    errors = gt.validate_internal_consistency()
    if errors:
        logger.warning(f"Ground truth validation warnings for {repo_name}:")
        for error in errors:
            logger.warning(f"  - {error}")

    return gt


def list_risk_benchmarks() -> List[str]:
    """List all repositories with risk ground truth annotations."""
    if not REPOS_DIR.exists():
        return []

    repos = []
    for item in REPOS_DIR.iterdir():
        if item.is_dir() and (item / "risk_ground_truth.json").exists():
            repos.append(item.name)

    return sorted(repos)


def load_policy_fixture(policy_name: str) -> Optional[Dict]:
    """
    Load policy controls from fixture file.

    Args:
        policy_name: Name of the policy (e.g., "OWASP AI Top 10")

    Returns:
        Policy fixture dict or None if not found
    """
    # Normalize policy name to filename
    filename = policy_name.lower().replace(" ", "_").replace("-", "_") + ".json"
    policy_path = POLICIES_DIR / filename

    if not policy_path.exists():
        logger.warning(f"Policy fixture not found: {policy_path}")
        return None

    with open(policy_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================================
# Matching Logic
# ============================================================================


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy comparison."""
    return text.lower().strip().replace("-", " ").replace("_", " ")


def severity_adjacent(sev1: str, sev2: str) -> bool:
    """
    Check if two severities are adjacent (within ±1 step).

    CRITICAL ↔ HIGH (adjacent)
    HIGH ↔ MEDIUM (adjacent)
    MEDIUM ↔ LOW (adjacent)
    """
    order = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    try:
        idx1 = order.index(sev1.upper())
        idx2 = order.index(sev2.upper())
        return abs(idx1 - idx2) <= 1
    except ValueError:
        return False


def keyword_overlap(keywords: List[str], text: str) -> float:
    """
    Calculate keyword overlap ratio.

    Args:
        keywords: List of expected keywords
        text: Text to search in

    Returns:
        Ratio of keywords found (0.0 to 1.0)
    """
    if not keywords:
        return 1.0  # No keywords to match = automatic pass

    text_lower = text.lower()
    matched = sum(1 for kw in keywords if kw.lower() in text_lower)
    return matched / len(keywords)


def finding_matches(
    discovered: Dict,
    ground_truth: GroundTruthFinding,
) -> Tuple[bool, MatchFlexibility, int]:
    """
    Check if a discovered finding matches a ground truth finding.

    Args:
        discovered: Discovered finding dict
        ground_truth: Ground truth finding

    Returns:
        Tuple of (matched, match_level, confidence)
    """
    flexibility = ground_truth.match_flexibility

    # Extract discovered fields
    disc_title = discovered.get("title", "")
    disc_severity = discovered.get("severity", "")
    disc_control_id = discovered.get("control_id")
    disc_policy = discovered.get("policy_name", "")
    disc_file = discovered.get("affected_file", "")
    disc_description = discovered.get("description", "")
    disc_evidence = discovered.get("evidence", "")
    disc_remediation = discovered.get("remediation", "")

    # EXACT match: control_id + severity + file must all match
    if flexibility == MatchFlexibility.EXACT:
        if (
            ground_truth.control_id
            and disc_control_id == ground_truth.control_id
            and disc_severity.upper() == ground_truth.severity.value
            and ground_truth.affected_file
            and ground_truth.affected_file in disc_file
        ):
            return True, MatchFlexibility.EXACT, 100

    # EXACT_CONTROL match: control_id + policy match, severity within ±1
    if flexibility in [MatchFlexibility.EXACT, MatchFlexibility.EXACT_CONTROL]:
        if (
            ground_truth.control_id
            and disc_control_id == ground_truth.control_id
            and normalize_text(disc_policy) == normalize_text(ground_truth.policy_name)
            and severity_adjacent(disc_severity, ground_truth.severity.value)
        ):
            return True, MatchFlexibility.EXACT_CONTROL, 90

    # SEMANTIC match: same policy + gap_type + similar severity + keyword overlap
    if flexibility in [
        MatchFlexibility.EXACT,
        MatchFlexibility.EXACT_CONTROL,
        MatchFlexibility.SEMANTIC,
    ]:
        policy_match = normalize_text(disc_policy) == normalize_text(ground_truth.policy_name)
        severity_match = severity_adjacent(disc_severity, ground_truth.severity.value)

        # Check keyword overlap in title/description/evidence/remediation
        full_text = f"{disc_title} {disc_description} {disc_evidence} {disc_remediation}"
        evidence_overlap = keyword_overlap(ground_truth.evidence_keywords, full_text)
        remediation_overlap = keyword_overlap(ground_truth.remediation_keywords, full_text)

        if (
            policy_match
            and severity_match
            and (evidence_overlap >= 0.5 or remediation_overlap >= 0.5)
        ):
            confidence = int(70 + 30 * max(evidence_overlap, remediation_overlap))
            return True, MatchFlexibility.SEMANTIC, confidence

    # TYPE_ONLY match: same severity category
    severity_match = disc_severity.upper() == ground_truth.severity.value
    if severity_match:
        return True, MatchFlexibility.TYPE_ONLY, 50

    return False, MatchFlexibility.TYPE_ONLY, 0


def covered_control_matches(
    discovered: Dict,
    ground_truth: GroundTruthCoveredControl,
) -> Tuple[bool, str]:
    """
    Check if a discovered covered control matches ground truth.

    Args:
        discovered: Discovered covered control dict
        ground_truth: Ground truth covered control

    Returns:
        Tuple of (matched, evidence_quality: STRONG|WEAK|NONE)
    """
    disc_control_id = discovered.get("control_id", "")
    disc_policy = discovered.get("policy_name", "")
    disc_evidence_summary = discovered.get("evidence_summary", "")

    flexibility = ground_truth.match_flexibility

    # EXACT_CONTROL: control_id + policy must match
    if disc_control_id == ground_truth.control_id:
        if normalize_text(disc_policy) == normalize_text(ground_truth.policy_name):
            # Check evidence quality
            evidence_overlap = keyword_overlap(
                ground_truth.evidence_keywords, disc_evidence_summary
            )
            if evidence_overlap >= 0.7:
                return True, "STRONG"
            elif evidence_overlap >= 0.3:
                return True, "WEAK"
            else:
                return True, "NONE"

    # FUZZY: control_name substring match + policy match
    if flexibility == MatchFlexibility.SEMANTIC:
        disc_control_name = discovered.get("control_name", "")
        if ground_truth.control_name.lower() in disc_control_name.lower() and normalize_text(
            disc_policy
        ) == normalize_text(ground_truth.policy_name):
            return True, "WEAK"

    return False, "NONE"


def get_risk_band(score: int) -> str:
    """Convert risk score to risk band."""
    if score <= 25:
        return RiskBand.LOW.value
    elif score <= 50:
        return RiskBand.MEDIUM.value
    elif score <= 75:
        return RiskBand.HIGH.value
    else:
        return RiskBand.CRITICAL.value


# ============================================================================
# Metrics Calculation
# ============================================================================


def calculate_metrics(tp: int, fp: int, fn: int) -> RiskTypeMetrics:
    """Calculate precision, recall, F1 from counts."""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return RiskTypeMetrics(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=precision,
        recall=recall,
        f1_score=f1,
    )


def calculate_quality_score(result: RiskEvaluationResult) -> float:
    """
    Calculate composite quality score from individual metrics.

    Uses weighted combination defined in QUALITY_WEIGHTS.
    """
    # Finding F1 (0-1)
    finding_f1 = result.finding_metrics.f1_score

    # Covered control F1 (0-1)
    covered_f1 = result.covered_metrics.f1_score

    # Risk score accuracy (0-1, based on whether within tolerance)
    risk_accuracy = (
        1.0
        if result.risk_score_within_tolerance
        else max(0.0, 1.0 - (result.risk_score_error / 50))
    )

    # Red team coverage (already 0-1)
    red_team = result.red_team_type_coverage

    # Severity distribution (already 0-1)
    severity_dist = result.severity_distribution_accuracy

    # Mutual exclusivity (1.0 if no violations, decreases with violations)
    mx_score = max(0.0, 1.0 - (result.mutual_exclusivity_violations * 0.1))

    # Weighted sum
    quality = (
        QUALITY_WEIGHTS["finding_f1"] * finding_f1
        + QUALITY_WEIGHTS["covered_control_f1"] * covered_f1
        + QUALITY_WEIGHTS["risk_score_accuracy"] * risk_accuracy
        + QUALITY_WEIGHTS["red_team_coverage"] * red_team
        + QUALITY_WEIGHTS["severity_distribution"] * severity_dist
        + QUALITY_WEIGHTS["mutual_exclusivity"] * mx_score
    )

    return quality


# ============================================================================
# Main Evaluation Functions
# ============================================================================


async def evaluate_risk_assessment(
    ground_truth: RiskGroundTruth,
    discovered_findings: List[Dict],
    discovered_covered_controls: List[Dict],
    discovered_risk_score: int,
    discovered_red_team_attacks: List[Dict],
    discovery_time: float = 0.0,
    risk_time: float = 0.0,
) -> RiskEvaluationResult:
    """
    Evaluate risk assessment results against ground truth.

    Args:
        ground_truth: Expected results from ground truth
        discovered_findings: Findings from AI service
        discovered_covered_controls: Covered controls from AI service
        discovered_risk_score: Risk score from AI service
        discovered_red_team_attacks: Red team attacks from AI service
        discovery_time: Time spent on discovery phase
        risk_time: Time spent on risk assessment phase

    Returns:
        RiskEvaluationResult with all metrics
    """
    result = RiskEvaluationResult(
        repo_name=ground_truth.repo_name,
        policies_evaluated=ground_truth.policies_evaluated,
        discovery_time_seconds=discovery_time,
        risk_assessment_time_seconds=risk_time,
        total_time_seconds=discovery_time + risk_time,
    )

    # ---- Finding Matching ----
    gt_findings = ground_truth.expected_findings
    matched_gt_indices = set()
    matched_disc_indices = set()
    finding_matches_list = []

    for gt_idx, gt_finding in enumerate(gt_findings):
        best_match_idx = None
        best_match_level = None
        best_confidence = 0

        for disc_idx, disc_finding in enumerate(discovered_findings):
            if disc_idx in matched_disc_indices:
                continue

            matched, level, confidence = finding_matches(disc_finding, gt_finding)
            if matched and confidence > best_confidence:
                best_match_idx = disc_idx
                best_match_level = level
                best_confidence = confidence

        match_result = FindingMatchResult(
            ground_truth_title=gt_finding.title,
            ground_truth_control_id=gt_finding.control_id,
            ground_truth_severity=gt_finding.severity.value,
            ground_truth_policy=gt_finding.policy_name,
        )

        if best_match_idx is not None:
            matched_gt_indices.add(gt_idx)
            matched_disc_indices.add(best_match_idx)
            disc = discovered_findings[best_match_idx]
            match_result.matched = True
            match_result.match_level = best_match_level
            match_result.confidence = best_confidence
            match_result.discovered_title = disc.get("title")
            match_result.discovered_control_id = disc.get("control_id")
            match_result.discovered_severity = disc.get("severity")

        finding_matches_list.append(match_result)

    # Calculate finding metrics
    finding_tp = len(matched_gt_indices)
    finding_fn = len(gt_findings) - finding_tp
    finding_fp = len(discovered_findings) - len(matched_disc_indices)

    result.finding_metrics = calculate_metrics(finding_tp, finding_fp, finding_fn)
    result.finding_matches = finding_matches_list

    # False positive details
    result.finding_false_positive_details = [
        discovered_findings[i]
        for i in range(len(discovered_findings))
        if i not in matched_disc_indices
    ]

    # ---- Covered Control Matching ----
    gt_controls = ground_truth.expected_covered_controls
    matched_gt_ctrl_indices = set()
    matched_disc_ctrl_indices = set()
    control_matches_list = []

    for gt_idx, gt_control in enumerate(gt_controls):
        for disc_idx, disc_control in enumerate(discovered_covered_controls):
            if disc_idx in matched_disc_ctrl_indices:
                continue

            matched, evidence_quality = covered_control_matches(disc_control, gt_control)
            if matched:
                matched_gt_ctrl_indices.add(gt_idx)
                matched_disc_ctrl_indices.add(disc_idx)
                control_matches_list.append(
                    CoveredControlMatchResult(
                        ground_truth_control_id=gt_control.control_id,
                        ground_truth_policy=gt_control.policy_name,
                        discovered_control_id=disc_control.get("control_id"),
                        matched=True,
                        evidence_quality=evidence_quality,
                    )
                )
                break
        else:
            # No match found
            control_matches_list.append(
                CoveredControlMatchResult(
                    ground_truth_control_id=gt_control.control_id,
                    ground_truth_policy=gt_control.policy_name,
                    matched=False,
                )
            )

    # Calculate covered control metrics
    covered_tp = len(matched_gt_ctrl_indices)
    covered_fn = len(gt_controls) - covered_tp
    covered_fp = len(discovered_covered_controls) - len(matched_disc_ctrl_indices)

    result.covered_metrics = calculate_metrics(covered_tp, covered_fp, covered_fn)
    result.covered_matches = control_matches_list

    # ---- Risk Score Evaluation ----
    expected = ground_truth.expected_risk_score
    result.expected_risk_score = expected.score
    result.actual_risk_score = discovered_risk_score
    result.risk_score_error = abs(discovered_risk_score - expected.score)
    result.risk_score_within_tolerance = result.risk_score_error <= expected.tolerance
    result.expected_band = expected.band.value
    result.actual_band = get_risk_band(discovered_risk_score)
    result.band_match = result.expected_band == result.actual_band

    # ---- Severity Distribution Accuracy ----
    if ground_truth.expected_risk_summary:
        summary = ground_truth.expected_risk_summary
        tolerance = summary.count_tolerance

        # Count actual findings by severity
        actual_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in discovered_findings:
            sev = f.get("severity", "").upper()
            if sev in actual_counts:
                actual_counts[sev] += 1

        # Calculate per-severity accuracy
        expected_counts = {
            "CRITICAL": summary.critical_count,
            "HIGH": summary.high_count,
            "MEDIUM": summary.medium_count,
            "LOW": summary.low_count,
        }

        accuracies = []
        for sev, expected_count in expected_counts.items():
            actual_count = actual_counts[sev]
            if expected_count == 0 and actual_count == 0:
                accuracies.append(1.0)
            elif expected_count == 0:
                accuracies.append(0.0)
            else:
                diff = abs(actual_count - expected_count)
                acc = max(0.0, 1.0 - (diff / max(expected_count, tolerance)))
                accuracies.append(acc)

        result.severity_distribution_accuracy = sum(accuracies) / len(accuracies)
    else:
        result.severity_distribution_accuracy = 1.0  # No expectation = pass

    # ---- Red Team Attack Evaluation ----
    if ground_truth.expected_red_team_attacks:
        expected_attacks = ground_truth.expected_red_team_attacks

        # Check count
        result.red_team_count_sufficient = (
            len(discovered_red_team_attacks) >= expected_attacks.min_count
        )

        # Check type coverage
        if expected_attacks.expected_types:
            discovered_types = {a.get("type", "").upper() for a in discovered_red_team_attacks}
            expected_types = {t.upper() for t in expected_attacks.expected_types}
            matched_types = discovered_types & expected_types
            result.red_team_type_coverage = len(matched_types) / len(expected_types)
        else:
            result.red_team_type_coverage = 1.0
    else:
        result.red_team_count_sufficient = True
        result.red_team_type_coverage = 1.0

    # ---- Mutual Exclusivity Check ----
    # A control_id appearing in BOTH findings AND covered_controls is a violation
    finding_controls = {
        (f.get("control_id"), f.get("policy_name"))
        for f in discovered_findings
        if f.get("control_id")
    }
    covered_control_ids = {
        (c.get("control_id"), c.get("policy_name")) for c in discovered_covered_controls
    }

    violations = finding_controls & covered_control_ids
    result.mutual_exclusivity_violations = len(violations)

    # ---- Calculate Composite Quality Score ----
    result.quality_score = calculate_quality_score(result)

    return result


async def evaluate_repo(
    repo_name: str,
    verbose: bool = False,
    skip_discovery: bool = False,
    use_cache: bool = True,
) -> RiskEvaluationResult:
    """
    Evaluate risk assessment for a single repository.

    Args:
        repo_name: Name of the benchmark repository
        verbose: Print detailed output
        skip_discovery: Use cached assets from asset discovery benchmark
        use_cache: Use cached repo files if available

    Returns:
        RiskEvaluationResult
    """
    logger.info(f"Evaluating risk assessment: {repo_name}")
    start_time = time.time()

    # Load ground truth
    gt = load_risk_ground_truth(repo_name)
    logger.info(
        f"  Ground truth: {len(gt.expected_findings)} findings, "
        f"{len(gt.expected_covered_controls)} covered controls"
    )
    logger.info(f"  Policies: {gt.policies_evaluated}")

    # TODO: Implement actual risk assessment call
    # For now, return a placeholder result
    logger.warning("  ⚠️ Risk assessment API call not yet implemented - using placeholder data")

    # Placeholder data (will be replaced with actual API call)
    discovered_findings: List[Dict] = []
    discovered_covered_controls: List[Dict] = []
    discovered_risk_score = 50
    discovered_red_team_attacks: List[Dict] = []
    discovery_time = 0.0
    risk_time = time.time() - start_time

    # Evaluate
    result = await evaluate_risk_assessment(
        ground_truth=gt,
        discovered_findings=discovered_findings,
        discovered_covered_controls=discovered_covered_controls,
        discovered_risk_score=discovered_risk_score,
        discovered_red_team_attacks=discovered_red_team_attacks,
        discovery_time=discovery_time,
        risk_time=risk_time,
    )

    # Log results
    logger.info(result.to_summary())

    return result


async def evaluate_all(
    verbose: bool = False,
    skip_discovery: bool = False,
    use_cache: bool = True,
) -> RiskBenchmarkSuiteResult:
    """
    Evaluate risk assessment for all repositories with ground truth.

    Args:
        verbose: Print detailed output
        skip_discovery: Use cached assets from asset discovery benchmark
        use_cache: Use cached repo files if available

    Returns:
        RiskBenchmarkSuiteResult with aggregated metrics
    """
    repos = list_risk_benchmarks()

    if not repos:
        logger.warning("No repositories with risk ground truth found")
        return RiskBenchmarkSuiteResult(
            total_repos=0,
            successful_repos=0,
            failed_repos=0,
            evaluated_at=datetime.now().isoformat(),
        )

    logger.info(f"Found {len(repos)} repositories with risk ground truth")

    results: List[RiskEvaluationResult] = []
    failed_count = 0

    for repo_name in repos:
        try:
            result = await evaluate_repo(repo_name, verbose, skip_discovery, use_cache)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to evaluate {repo_name}: {e}")
            failed_count += 1
            results.append(
                RiskEvaluationResult(
                    repo_name=repo_name,
                    policies_evaluated=[],
                    error=str(e),
                )
            )

    # Aggregate metrics
    successful_results = [r for r in results if r.error is None]

    if successful_results:
        aggregate_finding_f1 = sum(r.finding_metrics.f1_score for r in successful_results) / len(
            successful_results
        )
        aggregate_covered_f1 = sum(r.covered_metrics.f1_score for r in successful_results) / len(
            successful_results
        )
        aggregate_risk_score_mae = sum(r.risk_score_error for r in successful_results) / len(
            successful_results
        )
        aggregate_band_accuracy = sum(1 for r in successful_results if r.band_match) / len(
            successful_results
        )
        aggregate_quality_score = sum(r.quality_score for r in successful_results) / len(
            successful_results
        )
    else:
        aggregate_finding_f1 = 0.0
        aggregate_covered_f1 = 0.0
        aggregate_risk_score_mae = 0.0
        aggregate_band_accuracy = 0.0
        aggregate_quality_score = 0.0

    total_time = sum(r.total_time_seconds for r in results)

    suite_result = RiskBenchmarkSuiteResult(
        total_repos=len(repos),
        successful_repos=len(successful_results),
        failed_repos=failed_count,
        aggregate_finding_f1=aggregate_finding_f1,
        aggregate_covered_f1=aggregate_covered_f1,
        aggregate_risk_score_mae=aggregate_risk_score_mae,
        aggregate_band_accuracy=aggregate_band_accuracy,
        aggregate_quality_score=aggregate_quality_score,
        results=results,
        total_time_seconds=total_time,
        evaluated_at=datetime.now().isoformat(),
    )

    logger.info(suite_result.to_summary())

    return suite_result


def main():
    """Main entry point for CLI."""
    # Load .env file from project root
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(env_path)

    parser = argparse.ArgumentParser(description="Evaluate NuGuard AI risk assessment accuracy")
    parser.add_argument("--repo", type=str, help="Evaluate a specific benchmark repository")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Evaluate all benchmark repositories with risk ground truth",
    )
    parser.add_argument(
        "--list", action="store_true", help="List available risk benchmark repositories"
    )
    parser.add_argument("--output", "-o", type=str, help="Output JSON results to file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed output")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_QUALITY_THRESHOLD,
        help=f"Quality score threshold for CI (default: {DEFAULT_QUALITY_THRESHOLD})",
    )
    parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Skip discovery phase, use cached assets from asset benchmark",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Don't use cached files, always fetch from GitHub"
    )
    parser.add_argument(
        "--policies",
        type=str,
        help="Comma-separated policy names to evaluate (overrides ground truth)",
    )

    args = parser.parse_args()

    # List mode
    if args.list:
        repos = list_risk_benchmarks()
        if repos:
            print("Available risk benchmark repositories:")
            for repo in repos:
                print(f"  - {repo}")
        else:
            print("No risk benchmark repositories found.")
            print("Create risk_ground_truth.json files in benchmark/repos/<repo>/")
        return 0

    # Validation
    if not args.repo and not args.all:
        parser.print_help()
        print("\nError: Specify --repo <name> or --all")
        return 2

    # Run evaluation
    try:
        if args.all:
            suite_result = asyncio.run(
                evaluate_all(
                    verbose=args.verbose,
                    skip_discovery=args.skip_discovery,
                    use_cache=not args.no_cache,
                )
            )

            # Output JSON if requested
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump(suite_result.model_dump(), f, indent=2, default=str)
                logger.info(f"Results written to {args.output}")

            # CI exit code based on threshold
            if suite_result.aggregate_quality_score >= args.threshold:
                logger.info(
                    f"✓ Quality score {suite_result.aggregate_quality_score:.2f} >= threshold {args.threshold}"
                )
                return 0
            else:
                logger.error(
                    f"✗ Quality score {suite_result.aggregate_quality_score:.2f} < threshold {args.threshold}"
                )
                return 1

        else:
            result = asyncio.run(
                evaluate_repo(
                    repo_name=args.repo,
                    verbose=args.verbose,
                    skip_discovery=args.skip_discovery,
                    use_cache=not args.no_cache,
                )
            )

            # Output JSON if requested
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump(result.model_dump(), f, indent=2, default=str)
                logger.info(f"Results written to {args.output}")

            # CI exit code based on threshold
            if result.quality_score >= args.threshold:
                logger.info(
                    f"✓ Quality score {result.quality_score:.2f} >= threshold {args.threshold}"
                )
                return 0
            else:
                logger.error(
                    f"✗ Quality score {result.quality_score:.2f} < threshold {args.threshold}"
                )
                return 1

    except FileNotFoundError as e:
        logger.error(str(e))
        return 2
    except Exception as e:
        logger.exception(f"Evaluation failed: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
