"""Benchmark evaluation helpers for NuGuard's current SBOM CLI and Python API.

This package mirrors the toolbox benchmark fixture set under ``tests/benchmark``
while using NuGuard's current ``AiSbomDocument`` schema and ``nuguard sbom``
entrypoints.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.parser import parse_sbom
from nuguard.sbom.tests.test_toolbox.evaluate import (
    _convert_xelo_ground_truth_to_legacy,
    evaluate_discovery,
    export_discovered_assets_csv,
)

from .schemas import (
    BenchmarkSuiteResult,
    DiscoveredAsset,
    GroundTruth,
    ScanEvaluationResult,
    TypeMetrics,
)

logger = logging.getLogger(__name__)

BENCHMARK_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = BENCHMARK_DIR / "fixtures"
TEST_RESULTS_DIR = BENCHMARK_DIR / "results"
DEFAULT_F1_THRESHOLD = 0.80

XEO_COMPONENT_TO_ASSET_TYPE = {
    "AGENT": "AGENT",
    "MODEL": "MODEL",
    "TOOL": "TOOL",
    "PROMPT": "PROMPT",
    "DATASTORE": "DATASTORE",
    "GUARDRAIL": "GUARDRAIL",
    "AUTH": "AUTH",
    "PRIVILEGE": "PRIVILEGE",
}


def load_ground_truth(repo_name: str) -> GroundTruth:
    """Load a benchmark ground-truth record from the local fixtures directory."""
    path = FIXTURES_DIR / repo_name / "ground_truth.json"
    if not path.exists():
        raise FileNotFoundError(f"Ground truth not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "schema_version" in data and "nodes" in data:
        data = _convert_xelo_ground_truth_to_legacy(repo_name, data)
    return GroundTruth.model_validate(data)


def list_available_benchmarks() -> list[str]:
    """Return all benchmark repo names that have a ground_truth.json fixture."""
    if not FIXTURES_DIR.exists():
        return []
    return sorted(
        item.name
        for item in FIXTURES_DIR.iterdir()
        if item.is_dir() and (item / "ground_truth.json").exists()
    )


def materialize_cached_fixture(repo_name: str) -> str:
    """Write cached benchmark files to a temporary directory and return its path."""
    cache_path = FIXTURES_DIR / repo_name / "cached_files.json"
    if not cache_path.exists():
        raise FileNotFoundError(f"Cached files fixture not found: {cache_path}")

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    files = payload.get("files", [])
    temp_dir = tempfile.mkdtemp(prefix=f"nuguard-benchmark-{repo_name}-")
    root = Path(temp_dir)
    for entry in files:
        rel_path = str(entry.get("path", "")).strip()
        if not rel_path:
            continue
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(entry.get("content", "")), encoding="utf-8")
    return temp_dir


def _normalize_name(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "").replace(" ", "")


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def convert_nodes_to_discovered_assets(nodes: list[Any], source_refs: list[str]) -> list[DiscoveredAsset]:
    """Convert current AiSbomDocument nodes into benchmark assets with evidence payloads."""
    discovered: list[DiscoveredAsset] = []
    seen: set[tuple[str, str, str]] = set()

    for node in nodes:
        ct = getattr(node, "component_type", None)
        node_type_raw = str(getattr(ct, "value", ct) or "").strip().upper()
        mapped_type = XEO_COMPONENT_TO_ASSET_TYPE.get(node_type_raw)
        if not mapped_type:
            continue

        name = str(getattr(node, "name", "") or "").strip()
        if not name:
            continue

        evidence_entries = []
        all_paths: list[str] = []
        first_line = None
        for ev in getattr(node, "evidence", []) or []:
            location = getattr(ev, "location", None)
            ev_path = str(getattr(location, "path", "") or "").strip()
            ev_line = getattr(location, "line", None)
            ev_detail = str(getattr(ev, "detail", "") or "").strip()
            ev_kind = str(getattr(ev, "kind", "") or "").strip()
            ev_confidence = getattr(ev, "confidence", None)
            if ev_path and ev_path not in all_paths:
                all_paths.append(ev_path)
            if first_line is None and isinstance(ev_line, int):
                first_line = ev_line
            evidence_entries.append(
                {
                    "kind": ev_kind,
                    "detail": ev_detail,
                    "confidence": float(ev_confidence) if isinstance(ev_confidence, (int, float)) else None,
                    "path": ev_path,
                    "line": ev_line,
                }
            )

        file_path = all_paths[0] if all_paths else ""
        alt_paths = all_paths[1:] if len(all_paths) > 1 else []
        key = (mapped_type, _normalize_name(name), _normalize_path(file_path))
        if key in seen:
            continue
        seen.add(key)

        metadata = getattr(node, "metadata", None)
        framework = getattr(metadata, "framework", None) if metadata else None
        extras = getattr(metadata, "extras", {}) if metadata else {}
        description = None
        for field_name in ("description", "summary", "purpose", "details", "content"):
            value = extras.get(field_name) if isinstance(extras, dict) else None
            if isinstance(value, str) and value.strip():
                description = value.strip()
                break

        matched_pattern = None
        for entry in evidence_entries:
            detail = entry.get("detail")
            if isinstance(detail, str) and detail:
                matched_pattern = detail
                break

        confidence = getattr(node, "confidence", None)
        confidence_value = float(confidence) if isinstance(confidence, (int, float)) else None
        discovered.append(
            DiscoveredAsset(
                asset_type=mapped_type,
                name=name,
                file_path=file_path,
                alt_file_paths=alt_paths,
                line_start=first_line,
                line_end=first_line,
                description=description,
                confidence=confidence_value,
                framework=framework if isinstance(framework, str) and framework.strip() else None,
                evidence_sources=source_refs,
                matched_pattern=matched_pattern,
                additional_evidence=evidence_entries,
            )
        )

    return discovered


def generate_sbom_python(source_dir: str, use_llm: bool = False):
    """Generate an SBOM directly through the Python extractor API."""
    extractor = AiSbomExtractor()
    config = AiSbomConfig(enable_llm=use_llm)
    return extractor.extract_from_path(source_dir, config, source_ref=source_dir)


def generate_sbom_cli(source_dir: str, use_llm: bool = False):
    """Generate an SBOM via ``python -m nuguard.cli.main sbom generate``."""
    with tempfile.NamedTemporaryFile(prefix="nuguard-benchmark-", suffix=".sbom.json", delete=False) as fh:
        output_path = Path(fh.name)

    cmd = [
        sys.executable,
        "-m",
        "nuguard.cli.main",
        "sbom",
        "generate",
        "--source",
        source_dir,
        "--output",
        str(output_path),
    ]
    if use_llm:
        cmd.append("--llm")

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(Path(__file__).resolve().parents[2]),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "NuGuard CLI SBOM generation failed:\n"
                f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}"
            )
        return parse_sbom(output_path)
    finally:
        output_path.unlink(missing_ok=True)


def run_discovery_for_repo(
    ground_truth: GroundTruth,
    mode: str = "cli",
    use_llm: bool = False,
) -> tuple[list, dict[str, Any], int]:
    """Run SBOM generation for a benchmark repo and return benchmark assets + SBOM dict."""
    source_dir = materialize_cached_fixture(ground_truth.repo_name)
    started = time.time()
    try:
        if mode == "cli":
            doc = generate_sbom_cli(source_dir, use_llm=use_llm)
        else:
            doc = generate_sbom_python(source_dir, use_llm=use_llm)
        discovered = convert_nodes_to_discovered_assets(doc.nodes, [ground_truth.repo_url, source_dir])
        return discovered, doc.model_dump(mode="json"), int((time.time() - started) * 1000)
    finally:
        shutil.rmtree(source_dir, ignore_errors=True)


async def evaluate_repo(
    repo_name: str,
    *,
    mode: str = "cli",
    use_llm: bool = False,
    verbose: bool = False,
) -> ScanEvaluationResult:
    """Evaluate a single benchmark repository."""
    gt = load_ground_truth(repo_name)
    discovered, _, elapsed_ms = run_discovery_for_repo(gt, mode=mode, use_llm=use_llm)
    result = evaluate_discovery(gt, discovered, fuzzy_paths=True)
    result.discovered_assets = discovered
    result.processing_time_ms = elapsed_ms

    if verbose:
        logger.info(
            "%s: precision=%.2f recall=%.2f f1=%.2f discovered=%d",
            repo_name,
            result.precision,
            result.recall,
            result.f1_score,
            len(discovered),
        )
    return result


async def evaluate_all(
    *,
    mode: str = "cli",
    use_llm: bool = False,
    verbose: bool = False,
) -> BenchmarkSuiteResult:
    """Evaluate all available benchmark repositories."""
    repo_names = list_available_benchmarks()
    if not repo_names:
        return BenchmarkSuiteResult(
            total_repos=0,
            overall_precision=0.0,
            overall_recall=0.0,
            overall_f1=0.0,
            total_true_positives=0,
            total_false_positives=0,
            total_false_negatives=0,
            by_repo={},
            by_type_aggregate={},
            evaluated_at=datetime.utcnow().isoformat(),
        )

    by_repo: dict[str, ScanEvaluationResult] = {}
    total_tp = total_fp = total_fn = 0
    aggregate_by_type: dict[str, dict[str, int]] = {}

    for repo_name in repo_names:
        result = await evaluate_repo(repo_name, mode=mode, use_llm=use_llm, verbose=verbose)
        by_repo[repo_name] = result
        total_tp += result.true_positives
        total_fp += result.false_positives
        total_fn += result.false_negatives
        for asset_type, metrics in result.by_type.items():
            bucket = aggregate_by_type.setdefault(
                asset_type,
                {"tp": 0, "fp": 0, "fn": 0},
            )
            bucket["tp"] += metrics.true_positives
            bucket["fp"] += metrics.false_positives
            bucket["fn"] += metrics.false_negatives

    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 0.0
    overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 0.0
    overall_f1 = (
        2 * overall_precision * overall_recall / (overall_precision + overall_recall)
        if (overall_precision + overall_recall)
        else 0.0
    )

    by_type_aggregate = {}
    for asset_type, counts in aggregate_by_type.items():
        tp = counts["tp"]
        fp = counts["fp"]
        fn = counts["fn"]
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        by_type_aggregate[asset_type] = TypeMetrics(
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
            precision=precision,
            recall=recall,
            f1_score=f1,
        )

    return BenchmarkSuiteResult(
        total_repos=len(repo_names),
        overall_precision=overall_precision,
        overall_recall=overall_recall,
        overall_f1=overall_f1,
        total_true_positives=total_tp,
        total_false_positives=total_fp,
        total_false_negatives=total_fn,
        by_repo=by_repo,
        by_type_aggregate=by_type_aggregate,
        evaluated_at=datetime.utcnow().isoformat(),
    )


def build_repo_payload(result: ScanEvaluationResult, sbom_dict: dict[str, Any]) -> dict[str, Any]:
    """Serialize a benchmark result with richer SBOM context."""
    payload = result.model_dump(mode="json")
    payload["sbom_summary"] = sbom_dict.get("summary")
    payload["sbom_relationships"] = sbom_dict.get("edges", [])
    payload["sbom_packages"] = sbom_dict.get("deps", [])
    payload["sbom_target"] = sbom_dict.get("target")
    payload["sbom_generator"] = sbom_dict.get("generator")
    payload["sbom_schema_version"] = sbom_dict.get("schema_version")
    return payload


def build_suite_payload(
    suite_result: BenchmarkSuiteResult,
    repo_payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Serialize a benchmark suite result with richer per-repo SBOM context."""
    payload = suite_result.model_dump(mode="json")
    payload["by_repo"] = repo_payloads
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="Evaluate one benchmark repo by name")
    parser.add_argument("--all", action="store_true", help="Evaluate every benchmark repo")
    parser.add_argument("--mode", choices=["python", "cli"], default="cli")
    parser.add_argument("--llm", action="store_true", help="Enable LLM enrichment during discovery")
    parser.add_argument("--output", type=Path, help="Write JSON results to this path")
    parser.add_argument("--csv-output", type=Path, help="Write discovered assets CSV to this path")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--threshold", type=float, default=DEFAULT_F1_THRESHOLD)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    if not args.repo and not args.all:
        parser.error("pass --repo <name> or --all")

    if args.repo:
        gt = load_ground_truth(args.repo)
        discovered, sbom_dict, elapsed_ms = run_discovery_for_repo(gt, mode=args.mode, use_llm=args.llm)
        result = evaluate_discovery(gt, discovered, fuzzy_paths=True)
        result.discovered_assets = discovered
        result.processing_time_ms = elapsed_ms
        payload = build_repo_payload(result, sbom_dict)
        score = result.f1_score
    else:
        repo_payloads: dict[str, dict[str, Any]] = {}
        repo_results: dict[str, ScanEvaluationResult] = {}
        total_tp = total_fp = total_fn = 0
        aggregate_by_type: dict[str, dict[str, int]] = {}
        for repo_name in list_available_benchmarks():
            gt = load_ground_truth(repo_name)
            discovered, sbom_dict, elapsed_ms = run_discovery_for_repo(gt, mode=args.mode, use_llm=args.llm)
            result = evaluate_discovery(gt, discovered, fuzzy_paths=True)
            result.discovered_assets = discovered
            result.processing_time_ms = elapsed_ms
            repo_results[repo_name] = result
            repo_payloads[repo_name] = build_repo_payload(result, sbom_dict)
            total_tp += result.true_positives
            total_fp += result.false_positives
            total_fn += result.false_negatives
            for asset_type, metrics in result.by_type.items():
                bucket = aggregate_by_type.setdefault(asset_type, {"tp": 0, "fp": 0, "fn": 0})
                bucket["tp"] += metrics.true_positives
                bucket["fp"] += metrics.false_positives
                bucket["fn"] += metrics.false_negatives

        overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 0.0
        overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 0.0
        overall_f1 = (
            2 * overall_precision * overall_recall / (overall_precision + overall_recall)
            if (overall_precision + overall_recall)
            else 0.0
        )
        by_type_aggregate = {}
        for asset_type, counts in aggregate_by_type.items():
            tp = counts["tp"]
            fp = counts["fp"]
            fn = counts["fn"]
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
            by_type_aggregate[asset_type] = TypeMetrics(
                true_positives=tp,
                false_positives=fp,
                false_negatives=fn,
                precision=precision,
                recall=recall,
                f1_score=f1,
            )

        result = BenchmarkSuiteResult(
            total_repos=len(repo_results),
            overall_precision=overall_precision,
            overall_recall=overall_recall,
            overall_f1=overall_f1,
            total_true_positives=total_tp,
            total_false_positives=total_fp,
            total_false_negatives=total_fn,
            by_repo=repo_results,
            by_type_aggregate=by_type_aggregate,
            evaluated_at=datetime.utcnow().isoformat(),
        )
        payload = build_suite_payload(result, repo_payloads)
        score = result.overall_f1
        if args.csv_output:
            export_discovered_assets_csv(result, args.csv_output)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, indent=2))

    return 0 if score >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
