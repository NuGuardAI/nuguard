#!/usr/bin/env python3
"""
Benchmark Evaluation Script for NuGuard AI Asset Discovery

This script evaluates the accuracy of asset discovery by comparing
discovered assets against ground truth annotations.

Usage:
    python -m benchmark.evaluate --repo langchain-examples
    python -m benchmark.evaluate --all
    python -m benchmark.evaluate --all --output results.json
    python -m benchmark.evaluate --all --verbose
    python -m benchmark.evaluate --repo crewai-examples --enable-llm  # Enable LLM enrichment

Exit Codes:
    0 - Success (F1 >= threshold)
    1 - Failure (F1 < threshold)
    2 - Error (missing ground truth, fetch failed, etc.)

Environment Variables:
    GEMINI_API_KEY - Required when using --enable-llm (or set AISBOM_ENABLE_LLM=true)
    GITHUB_TOKEN - GitHub personal access token (also loaded from .env)
    NUGUARD_PER_TYPE_DISCOVERY - Enable per-type LLM discovery (default: true)
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

from dotenv import load_dotenv

from .schemas import (
    GroundTruth,
    GroundTruthAsset,
    ExpectedCounts,
    DiscoveredAsset,
    ScanEvaluationResult,
    TypeMetrics,
    BenchmarkSuiteResult,
)
from .fetcher import fetch_repo_for_benchmark

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Default paths
BENCHMARK_DIR = Path(__file__).parent
REPOS_DIR = BENCHMARK_DIR / "fixtures"
TEST_RESULTS_DIR = BENCHMARK_DIR.parent / "test-results"

# Default threshold for CI
DEFAULT_F1_THRESHOLD = 0.80

NODE_TYPE_TO_BENCHMARK_TYPE: Dict[str, str] = {
    "agent": "AGENT",
    "agentgraph": "AGENT",
    "model": "MODEL",
    "embeddingmodel": "MODEL",
    "tool": "TOOL",
    "prompt": "PROMPT",
    "prompttemplate": "PROMPT",
    "datastore": "DATASTORE",
    "retriever": "DATASTORE",
    "reranker": "DATASTORE",
    "chunkingstrategy": "DATASTORE",
    "semanticcache": "DATASTORE",
    "guardrail": "GUARDRAIL",
    "auth": "AUTH",
    "privilege": "PRIVILEGE",
}

XeloComponentToAssetType: Dict[str, str] = {
    "AGENT": "AGENT",
    "MODEL": "MODEL",
    "TOOL": "TOOL",
    "PROMPT": "PROMPT",
    "DATASTORE": "DATASTORE",
    "GUARDRAIL": "GUARDRAIL",
    "AUTH": "AUTH",
    "PRIVILEGE": "PRIVILEGE",
}


def _convert_xelo_ground_truth_to_legacy(repo_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Xelo-native ground truth JSON into legacy benchmark GroundTruth shape."""
    nodes = payload.get("nodes", []) if isinstance(payload.get("nodes"), list) else []
    edges = payload.get("edges", []) if isinstance(payload.get("edges"), list) else []

    node_by_id: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        node_id = str(node.get("id", "")).strip()
        if node_id:
            node_by_id[node_id] = node

    rel_by_source: Dict[str, Dict[str, List[str]]] = {}
    for edge in edges:
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        rel_type = str(edge.get("relationship_type") or edge.get("type") or "").strip().lower()
        if not source or not target or not rel_type:
            continue
        target_name = str(node_by_id.get(target, {}).get("name", "")).strip()
        if not target_name:
            continue
        rel_by_source.setdefault(source, {}).setdefault(rel_type, []).append(target_name)

    assets: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for node in nodes:
        component_type = str(node.get("component_type") or node.get("type") or "").upper()
        mapped_type = XeloComponentToAssetType.get(component_type)
        if not mapped_type:
            continue

        evidence = node.get("evidence", [])
        first_ev = evidence[0] if isinstance(evidence, list) and evidence else {}
        location = first_ev.get("location", {}) if isinstance(first_ev, dict) else {}
        file_path = str(location.get("path", "")).strip()
        line = location.get("line")
        line_start = int(line) if isinstance(line, int) else None

        metadata = node.get("metadata", {}) if isinstance(node.get("metadata"), dict) else {}
        extras = metadata.get("extras", {}) if isinstance(metadata.get("extras"), dict) else {}
        description = None
        for key in ("description", "summary", "purpose", "details"):
            value = extras.get(key) or metadata.get(key)
            if isinstance(value, str) and value.strip():
                description = value.strip()
                break

        node_id = str(node.get("id", "")).strip()
        relationships = rel_by_source.get(node_id, {})
        relationship_value: Dict[str, str | List[str]] = {}
        for rel, targets in relationships.items():
            relationship_value[rel] = targets[0] if len(targets) == 1 else targets

        # Collect all file paths from every evidence entry (for multi-path matching)
        all_ev_paths: List[str] = []
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            ev_loc = ev.get("location", {})
            if not isinstance(ev_loc, dict):
                continue
            ev_path = str(ev_loc.get("path", "")).strip()
            if ev_path and ev_path not in all_ev_paths:
                all_ev_paths.append(ev_path)
        # Primary path is first entry; alt paths are the remainder
        alt_paths = all_ev_paths[1:] if len(all_ev_paths) > 1 else []

        asset = {
            "asset_type": mapped_type,
            "name": str(node.get("name", "")).strip(),
            "file_path": file_path,
            "line_start": line_start,
            "line_end": line_start,
            "description": description or "",
            "framework": metadata.get("framework"),
            "evidence": [
                str(ev.get("detail", "")).strip()
                for ev in evidence
                if isinstance(ev, dict) and str(ev.get("detail", "")).strip()
            ],
            "synonyms": extras.get("synonyms", [])
            if isinstance(extras.get("synonyms"), list)
            else [],
            "alt_file_paths": alt_paths,
            "relationships": relationship_value or None,
        }
        assets.append(asset)
        counts[mapped_type] = counts.get(mapped_type, 0) + 1

    generated_at = str(payload.get("generated_at", "")).strip()
    annotated_at = generated_at[:10] if len(generated_at) >= 10 else "1970-01-01"
    frameworks = []
    summary = payload.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("frameworks"), list):
        frameworks = [str(f) for f in summary.get("frameworks", [])]

    expected_counts = {k: 0 for k in ExpectedCounts.model_fields.keys()}
    for key, value in counts.items():
        if key in expected_counts:
            expected_counts[key] = value

    return {
        "repo_name": repo_name,
        "repo_url": payload.get("target") or f"local://{repo_name}",
        "branch": "main",
        "subfolder": None,
        "commit_sha": None,
        "annotated_at": annotated_at,
        "annotator": "xelo-ground-truth",
        "frameworks": frameworks,
        "assets": assets,
        "expected_counts": expected_counts,
        "notes": "Converted from Xelo-native ground truth JSON",
        "skip": False,
        "skip_reason": None,
    }


def export_discovered_assets_csv(suite_result: BenchmarkSuiteResult, output_path: Path) -> None:
    """
    Export all discovered assets to a CSV file.

    Args:
        suite_result: The benchmark suite result containing all repo results
        output_path: Path to write the CSV file
    """
    import csv

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # CSV columns
    fieldnames = [
        "repo_name",
        "asset_type",
        "name",
        "file_path",
        "line_start",
        "line_end",
        "confidence",
        "regex_confidence",
        "llm_confidence",
        "framework",
        "matched_pattern",
        "description",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for repo_name, result in suite_result.by_repo.items():
            if result.skipped:
                continue

            for asset in result.discovered_assets:
                row = {
                    "repo_name": repo_name,
                    "asset_type": asset.asset_type,
                    "name": asset.name,
                    "file_path": asset.file_path,
                    "line_start": asset.line_start,
                    "line_end": asset.line_end,
                    "confidence": asset.confidence,
                    "regex_confidence": asset.regex_confidence,
                    "llm_confidence": asset.llm_confidence,
                    "framework": asset.framework,
                    "matched_pattern": asset.matched_pattern,
                    "description": asset.description or "",
                }
                writer.writerow(row)

    print(f"Discovered assets CSV saved to: {output_path}")


def load_ground_truth(repo_name: str) -> GroundTruth:
    """
    Load ground truth from repos/{repo_name}/ground_truth.json.

    Args:
        repo_name: Name of the benchmark repository

    Returns:
        Parsed GroundTruth object

    Raises:
        FileNotFoundError: If ground truth file doesn't exist
        ValueError: If ground truth is invalid
    """
    gt_path = REPOS_DIR / repo_name / "ground_truth.json"

    if not gt_path.exists():
        raise FileNotFoundError(f"Ground truth not found: {gt_path}")

    with open(gt_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "schema_version" in data and "nodes" in data:
        data = _convert_xelo_ground_truth_to_legacy(repo_name, data)

    return GroundTruth.model_validate(data)


def list_available_benchmarks() -> List[str]:
    """List all available benchmark repositories."""
    if not REPOS_DIR.exists():
        return []

    repos = []
    for item in REPOS_DIR.iterdir():
        if item.is_dir() and (item / "ground_truth.json").exists():
            repos.append(item.name)

    return sorted(repos)


def normalize_path(path: str) -> str:
    """Normalize a file path for comparison."""
    return path.replace("\\", "/").strip("/")


def path_matches_fuzzy(disc_path: str, gt_path: str) -> bool:
    """
    Check if paths match using fuzzy matching strategies.

    Strategies:
    1. Exact match after normalization
    2. Filename + parent directory match (handles moved files)
    3. Suffix match (handles different prefixes like 'crews/' vs 'starter_template/')

    Args:
        disc_path: Discovered asset path
        gt_path: Ground truth path

    Returns:
        True if paths are considered a match
    """
    disc_norm = normalize_path(disc_path)
    gt_norm = normalize_path(gt_path)

    # Strategy 1: Exact match
    if disc_norm == gt_norm:
        return True

    # Strategy 2: Same filename in same-named parent directory
    disc_parts = disc_norm.split("/")
    gt_parts = gt_norm.split("/")

    if len(disc_parts) >= 2 and len(gt_parts) >= 2:
        # Check if filename and immediate parent match
        disc_file_parent = "/".join(disc_parts[-2:])
        gt_file_parent = "/".join(gt_parts[-2:])
        if disc_file_parent == gt_file_parent:
            return True

    # Strategy 3: Suffix match - discovered path ends with ground truth path
    # e.g., "crews/starter_template/agents.py" matches "starter_template/agents.py"
    if disc_norm.endswith(gt_norm):
        return True

    # Strategy 4: Ground truth ends with discovered (reversed suffix match)
    if gt_norm.endswith(disc_norm):
        return True

    # Strategy 5: Just filename match with same asset type (fallback)
    disc_filename = disc_parts[-1] if disc_parts else ""
    gt_filename = gt_parts[-1] if gt_parts else ""
    if disc_filename == gt_filename and disc_filename:
        # Only match if it's a reasonably unique filename
        unique_filenames = {
            "main.py",
            "app.py",
            "agent.py",
            "tools.py",
            "crew.py",
            "agents.py",
            "prompts.py",
            "config.py",
            "server.py",
        }
        if disc_filename not in unique_filenames:
            return True

    return False


def normalize_name(n: str) -> str:
    """Normalize an asset name for comparison.

    Strips underscores, hyphens, and spaces, then lowercases.
    Handles snake_case vs PascalCase vs kebab-case mismatches, e.g.
    "property_search_agent" → "propertysearchagent" == "PropertySearchAgent" → "propertysearchagent"
    """
    return n.lower().replace("_", "").replace("-", "").replace(" ", "")


def names_match(disc_name: str, gt_name: str, gt_synonyms: List[str] | None = None) -> bool:
    """Two-phase name matching.

    Phase 1 (exact normalized): Strip _/- and case-insensitive compare.
    Phase 2 (synonym check): Check if discovered name matches any GT synonym.
    Phase 3 (substring fallback): Check if one name is a meaningful substring of the other.

    Args:
        disc_name: Discovered asset name
        gt_name: Ground truth asset name
        gt_synonyms: Optional list of alternate accepted names

    Returns:
        True if names are considered a match
    """
    disc_norm = normalize_name(disc_name)
    gt_norm = normalize_name(gt_name)

    # Phase 1: Exact normalized match
    if disc_norm == gt_norm:
        return True

    # Phase 2: Synonym match — check if discovered name matches any synonym
    if gt_synonyms:
        for synonym in gt_synonyms:
            if normalize_name(synonym) == disc_norm:
                return True

    # Phase 3: Substring containment for meaningful names (>= 4 chars)
    # e.g. discovered "research_team" contains GT substring "research"
    # Only if both are substantial names — avoids matching short generics
    if len(disc_norm) >= 4 and len(gt_norm) >= 4:
        # Check if the shorter name is a substantial portion of the longer
        shorter, longer = sorted([disc_norm, gt_norm], key=len)
        if len(shorter) >= 4 and shorter in longer:
            # Only match if the shorter is at least 60% of the longer to avoid
            # overly loose matches like "agent" matching "triageagent"
            if len(shorter) / len(longer) >= 0.6:
                return True

    return False


def assets_match(
    discovered: DiscoveredAsset, ground_truth: GroundTruthAsset, fuzzy_paths: bool = True
) -> bool:
    """
    Check if a discovered asset matches a ground truth asset.

    Two-phase matching strategy:
    Phase 1: type + name (with synonyms + substring) + fuzzy path
    Phase 2: type + path match → relaxed name check (any overlap)

    Line numbers are NOT used for matching — they are informational only.

    Args:
        discovered: Asset found by discovery pipeline
        ground_truth: Expected asset from ground truth
        fuzzy_paths: Enable fuzzy path matching (default: True)

    Returns:
        True if assets match
    """
    # Must have same type
    if discovered.asset_type != ground_truth.asset_type.value:
        return False

    # Check file path match
    disc_path = discovered.file_path
    gt_path = ground_truth.file_path

    if fuzzy_paths:
        paths_match = path_matches_fuzzy(disc_path, gt_path)
    else:
        paths_match = normalize_path(disc_path) == normalize_path(gt_path)

    # Get synonyms from ground truth
    gt_synonyms = ground_truth.synonyms if hasattr(ground_truth, "synonyms") else []

    # Also check alt file paths (secondary evidence locations) from GT
    gt_alt_paths = ground_truth.alt_file_paths if hasattr(ground_truth, "alt_file_paths") else []
    if not paths_match and gt_alt_paths:
        for alt_path in gt_alt_paths:
            if fuzzy_paths:
                if path_matches_fuzzy(disc_path, alt_path):
                    paths_match = True
                    break
            else:
                if normalize_path(disc_path) == normalize_path(alt_path):
                    paths_match = True
                    break

    # Also check discovered asset's alt evidence paths against GT path
    disc_alt_paths = getattr(discovered, "alt_file_paths", [])
    if not paths_match and disc_alt_paths:
        for alt_path in disc_alt_paths:
            if fuzzy_paths:
                if path_matches_fuzzy(alt_path, gt_path):
                    paths_match = True
                    break
                if not paths_match and gt_alt_paths:
                    for gt_alt in gt_alt_paths:
                        if path_matches_fuzzy(alt_path, gt_alt):
                            paths_match = True
                            break
                if paths_match:
                    break
            else:
                if normalize_path(alt_path) == normalize_path(gt_path):
                    paths_match = True
                    break

    # Phase 1: Name match (normalized + synonyms + substring) + path match
    if names_match(discovered.name, ground_truth.name, gt_synonyms):
        if paths_match:
            return True

    # Phase 2: Path match + relaxed name check
    # If paths clearly match, check if discovered name appears in GT evidence or description
    if paths_match:
        disc_norm = normalize_name(discovered.name)
        # Check if discovered name is mentioned in the GT evidence strings
        for evidence_str in ground_truth.evidence:
            if disc_norm and normalize_name(evidence_str) == disc_norm:
                return True
        # Check if discovered name is a class mentioned in evidence
        for evidence_str in ground_truth.evidence:
            ev_norm = normalize_name(evidence_str)
            if len(disc_norm) >= 3 and len(ev_norm) >= 3:
                if disc_norm in ev_norm or ev_norm in disc_norm:
                    if (
                        len(min(disc_norm, ev_norm, key=len))
                        / len(max(disc_norm, ev_norm, key=len))
                        >= 0.5
                    ):
                        return True

    return False


def evaluate_discovery(
    ground_truth: GroundTruth, discovered: List[DiscoveredAsset], fuzzy_paths: bool = True
) -> ScanEvaluationResult:
    """
    Evaluate discovered assets against ground truth.

    Matching uses type + name + file_path only. Line numbers are not
    used as match criteria — a file-level match is sufficient.

    Args:
        ground_truth: Ground truth annotations
        discovered: List of discovered assets
        fuzzy_paths: Enable fuzzy path matching

    Returns:
        ScanEvaluationResult with precision, recall, F1, and details
    """
    gt_assets = ground_truth.assets

    # Track matches
    matched_gt_indices: Set[int] = set()
    matched_disc_indices: Set[int] = set()

    # Find all matches (greedy matching)
    for disc_idx, disc in enumerate(discovered):
        for gt_idx, gt in enumerate(gt_assets):
            if gt_idx in matched_gt_indices:
                continue

            if assets_match(disc, gt, fuzzy_paths):
                matched_gt_indices.add(gt_idx)
                matched_disc_indices.add(disc_idx)
                break

    # Calculate metrics
    true_positives = len(matched_gt_indices)
    false_positives = len(discovered) - len(matched_disc_indices)
    false_negatives = len(gt_assets) - len(matched_gt_indices)

    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives) > 0
        else 0.0
    )
    recall = true_positives / len(gt_assets) if len(gt_assets) > 0 else 0.0
    f1_score = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # Calculate by-type metrics
    by_type: Dict[str, TypeMetrics] = {}
    asset_types = set([a.asset_type.value for a in gt_assets] + [a.asset_type for a in discovered])

    for asset_type in asset_types:
        gt_of_type = [i for i, a in enumerate(gt_assets) if a.asset_type.value == asset_type]
        disc_of_type = [i for i, a in enumerate(discovered) if a.asset_type == asset_type]

        type_tp = len([i for i in gt_of_type if i in matched_gt_indices])
        type_fp = len([i for i in disc_of_type if i not in matched_disc_indices])
        type_fn = len([i for i in gt_of_type if i not in matched_gt_indices])

        type_precision = type_tp / (type_tp + type_fp) if (type_tp + type_fp) > 0 else 0.0
        type_recall = type_tp / len(gt_of_type) if len(gt_of_type) > 0 else 0.0
        type_f1 = (
            2 * type_precision * type_recall / (type_precision + type_recall)
            if (type_precision + type_recall) > 0
            else 0.0
        )

        by_type[asset_type] = TypeMetrics(
            true_positives=type_tp,
            false_positives=type_fp,
            false_negatives=type_fn,
            precision=type_precision,
            recall=type_recall,
            f1_score=type_f1,
        )

    # Collect false positive/negative details
    false_positive_details = [
        discovered[i].model_dump() for i in range(len(discovered)) if i not in matched_disc_indices
    ]
    false_negative_details = [
        gt_assets[i].model_dump() for i in range(len(gt_assets)) if i not in matched_gt_indices
    ]

    return ScanEvaluationResult(
        repo_name=ground_truth.repo_name,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        by_type=by_type,
        false_positive_details=false_positive_details,
        false_negative_details=false_negative_details,
    )


def _convert_aibom_nodes_to_discovered_assets(
    aibom_nodes: List[Any],
    evidence_source: str,
) -> List[DiscoveredAsset]:
    """Convert AIBOM nodes to benchmark DiscoveredAsset entries."""
    discovered: List[DiscoveredAsset] = []
    seen: Set[Tuple[str, str, str]] = set()

    for node in aibom_nodes:
        node_type_raw = str(
            getattr(getattr(node, "type", None), "value", getattr(node, "type", "")) or ""
        ).strip()
        mapped_type = NODE_TYPE_TO_BENCHMARK_TYPE.get(node_type_raw.lower())
        if not mapped_type:
            continue

        properties = node.properties if isinstance(getattr(node, "properties", None), dict) else {}
        if bool(properties.get("is_agent_graph")):
            continue

        name = str(getattr(node, "name", "") or "").strip()
        if not name:
            continue

        file_path = str(getattr(node, "file_path", "") or properties.get("file_path") or "").strip()
        key = (mapped_type, normalize_name(name), normalize_path(file_path))
        if key in seen:
            continue
        seen.add(key)

        description = ""
        for field_name in (
            "summary",
            "description",
            "purpose",
            "details",
            "asset_summary",
            "content",
        ):
            value = properties.get(field_name)
            if isinstance(value, str) and value.strip():
                description = value.strip()
                break

        framework = properties.get("framework") or properties.get("framework_name")
        framework_str = (
            framework.strip() if isinstance(framework, str) and framework.strip() else None
        )

        confidence = getattr(node, "confidence", None)
        confidence_value = float(confidence) if isinstance(confidence, (int, float)) else None

        discovered.append(
            DiscoveredAsset(
                asset_type=mapped_type,
                name=name,
                file_path=file_path or "",
                line_start=getattr(node, "line_start", None),
                line_end=getattr(node, "line_end", None),
                description=description or None,
                confidence=confidence_value,
                regex_confidence=None,
                llm_confidence=None,
                framework=framework_str,
                evidence_sources=[evidence_source],
                matched_pattern=None,
            )
        )

    return discovered


async def run_discovery_pipeline(
    files: List[Tuple[str, str]], detected_frameworks: List[str], use_llm: bool = False
) -> List[DiscoveredAsset]:
    """
    Run local benchmark discovery using the Xelo AiSbomExtractor.

    Args:
        files: List of (path, content) tuples
        detected_frameworks: Retained for compatibility; unused by Xelo extractor
        use_llm: When True, enables LLM enrichment (reads model/key config from env).
    """
    del detected_frameworks
    from xelo.extractor import AiSbomExtractor
    from xelo.config import AiSbomConfig

    if use_llm:
        logger.info("  LLM enrichment enabled for this scan")

    temp_dir = tempfile.mkdtemp(prefix="benchmark_pipeline_")
    try:
        root = Path(temp_dir)
        for path, content in files:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        extractor = AiSbomExtractor()
        config = AiSbomConfig(enable_llm=use_llm)
        doc = extractor.extract_from_path(temp_dir, config, source_ref="benchmark-local")
        return _convert_xelo_nodes_to_discovered_assets(doc.nodes, evidence_source="xelo_local")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _extract_file_path(
    properties: Dict[str, Any], node_id: str, evidence_index: Dict[str, List[Dict[str, Any]]]
) -> str:
    """Extract best-effort file path from node properties or evidence."""
    path_candidates = [
        properties.get("file_path"),
        properties.get("path"),
        properties.get("source_file"),
        properties.get("source_path"),
    ]
    for value in path_candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    node_evidence = evidence_index.get(node_id, [])
    for ev in node_evidence:
        file_path = ev.get("file_path")
        if isinstance(file_path, str) and file_path.strip():
            return file_path.strip()

    return ""


def _extract_line_range(
    properties: Dict[str, Any], node_id: str, evidence_index: Dict[str, List[Dict[str, Any]]]
) -> Tuple[Optional[int], Optional[int]]:
    """Extract line range from properties/evidence with fallback ordering."""
    line_start = properties.get("line_start")
    line_end = properties.get("line_end")
    line_number = properties.get("line_number")

    if isinstance(line_start, int) and isinstance(line_end, int):
        return line_start, line_end
    if isinstance(line_number, int):
        return line_number, line_number

    node_evidence = evidence_index.get(node_id, [])
    for ev in node_evidence:
        ev_start = ev.get("line_start")
        ev_end = ev.get("line_end")
        if isinstance(ev_start, int) and isinstance(ev_end, int):
            return ev_start, ev_end
        if isinstance(ev_start, int):
            return ev_start, ev_start

    return None, None


def convert_aibom_export_to_discovered_assets(
    export_payload: Dict[str, Any],
) -> List[DiscoveredAsset]:
    """
    Convert AIBOM API export payload into benchmark DiscoveredAsset list.
    """
    nodes = export_payload.get("nodes", []) if isinstance(export_payload, dict) else []
    evidence = export_payload.get("evidence", []) if isinstance(export_payload, dict) else []

    evidence_index: Dict[str, List[Dict[str, Any]]] = {}
    for ev in evidence:
        node_id = ev.get("node_id")
        if isinstance(node_id, str):
            evidence_index.setdefault(node_id, []).append(ev)

    discovered: List[DiscoveredAsset] = []
    seen: Set[Tuple[str, str, str]] = set()

    for node in nodes:
        node_type_raw = str(node.get("node_type", "")).strip()
        mapped_type = NODE_TYPE_TO_BENCHMARK_TYPE.get(node_type_raw.lower())
        if not mapped_type:
            continue

        node_id = str(node.get("id", "")).strip()
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        if bool(props.get("is_agent_graph")):
            continue
        file_path = _extract_file_path(props, node_id, evidence_index)
        line_start, line_end = _extract_line_range(props, node_id, evidence_index)

        name = str(node.get("name", "")).strip()
        if not name:
            continue

        key = (mapped_type, normalize_name(name), normalize_path(file_path or ""))
        if key in seen:
            continue
        seen.add(key)

        confidence = node.get("confidence")
        confidence_value = None
        if isinstance(confidence, (int, float)):
            confidence_value = float(confidence)
        elif isinstance(confidence, str):
            try:
                confidence_value = float(confidence)
            except ValueError:
                confidence_value = None

        description = ""
        for field_name in ("summary", "description", "purpose", "details"):
            value = props.get(field_name)
            if isinstance(value, str) and value.strip():
                description = value.strip()
                break
        if not description:
            for fallback_field in ("asset_summary", "content"):
                value = props.get(fallback_field)
                if isinstance(value, str) and value.strip():
                    description = value.strip()
                    break

        framework = props.get("framework") or props.get("framework_name")
        framework_str = (
            framework.strip() if isinstance(framework, str) and framework.strip() else None
        )

        discovered.append(
            DiscoveredAsset(
                asset_type=mapped_type,
                name=name,
                file_path=file_path or "",
                line_start=line_start,
                line_end=line_end,
                description=description or None,
                confidence=confidence_value,
                regex_confidence=None,
                llm_confidence=None,
                framework=framework_str,
                evidence_sources=["aibom_api"],
                matched_pattern=None,
            )
        )

    return discovered


def _write_cached_files_to_temp_dir(cached_files_path: Path) -> str:
    """Materialize cached benchmark files into a temporary local directory."""
    with open(cached_files_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    files = payload.get("files", [])

    temp_dir = tempfile.mkdtemp(prefix="benchmark_local_")
    root = Path(temp_dir)
    for entry in files:
        rel_path = str(entry.get("path", "")).strip()
        if not rel_path:
            continue
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(entry.get("content", "")), encoding="utf-8")
    return temp_dir


def _run_local_folder_discovery(
    folder_path: str, use_llm: bool = False
) -> Tuple[List[DiscoveredAsset], Dict[str, Any]]:
    """
    Run local folder extraction using the Xelo AiSbomExtractor.

    Args:
        folder_path: Path to the folder to scan.
        use_llm: When True, enables LLM enrichment (reads model/key config from env).

    Returns:
        Tuple of (discovered assets, full SBOM dict for toolbox plugins).
    """
    from xelo.extractor import AiSbomExtractor
    from xelo.config import AiSbomConfig

    extractor = AiSbomExtractor()
    config = AiSbomConfig(enable_llm=use_llm)
    if use_llm:
        logger.info("  LLM enrichment enabled for this scan")
    doc = extractor.extract_from_path(folder_path, config, source_ref=folder_path)
    discovered = _convert_xelo_nodes_to_discovered_assets(
        doc.nodes, evidence_source="xelo_local_folder"
    )
    sbom_dict: Dict[str, Any] = doc.model_dump(mode="json")
    return discovered, sbom_dict


def _convert_xelo_nodes_to_discovered_assets(
    nodes: List[Any],
    evidence_source: str,
) -> List[DiscoveredAsset]:
    """Convert Xelo AiSbomDocument Node objects to benchmark DiscoveredAsset entries."""
    discovered: List[DiscoveredAsset] = []
    seen: Set[Tuple[str, str, str]] = set()

    for node in nodes:
        # component_type is a ComponentType enum; .value gives uppercase string e.g. "AGENT"
        ct = getattr(node, "component_type", None)
        node_type_raw = str(getattr(ct, "value", ct) or "").strip()
        mapped_type = XeloComponentToAssetType.get(node_type_raw.upper())
        if not mapped_type:
            continue

        name = str(getattr(node, "name", "") or "").strip()
        if not name:
            continue

        # Extract file path and line from first evidence entry; collect all paths
        evidence = getattr(node, "evidence", []) or []
        file_path = ""
        line_start = None
        all_ev_paths: List[str] = []
        for ev in evidence:
            location = getattr(ev, "location", None)
            if location:
                ev_path = str(getattr(location, "path", "") or "").strip()
                if ev_path and ev_path not in all_ev_paths:
                    all_ev_paths.append(ev_path)
        if all_ev_paths:
            file_path = all_ev_paths[0]
            line_start = getattr(getattr(evidence[0], "location", None), "line", None)
        alt_paths = all_ev_paths[1:] if len(all_ev_paths) > 1 else []

        key = (mapped_type, normalize_name(name), normalize_path(file_path))
        if key in seen:
            continue
        seen.add(key)

        metadata = getattr(node, "metadata", None)
        framework = None
        if metadata:
            framework = getattr(metadata, "framework", None)
        framework_str = (
            framework.strip() if isinstance(framework, str) and framework.strip() else None
        )

        confidence = getattr(node, "confidence", None)
        confidence_value = float(confidence) if isinstance(confidence, (int, float)) else None

        discovered.append(
            DiscoveredAsset(
                asset_type=mapped_type,
                name=name,
                file_path=file_path or "",
                alt_file_paths=alt_paths,
                line_start=line_start,
                line_end=line_start,
                description=None,
                confidence=confidence_value,
                regex_confidence=None,
                llm_confidence=None,
                framework=framework_str,
                evidence_sources=[evidence_source],
                matched_pattern=None,
            )
        )

    return discovered


async def evaluate_repo(
    repo_name: str,
    verbose: bool = False,
    use_cache: bool = True,
    fuzzy_paths: bool = True,
    use_llm: bool = False,
    mode: str = "api",
    data_service_url: str = "http://localhost:8000",
    asset_service_url: str = "http://localhost:8004",
    auth_token: Optional[str] = None,
    auth_email: Optional[str] = None,
    auth_password: Optional[str] = None,
    github_token: Optional[str] = None,
    timeout_seconds: float = 300.0,
    plugin_llm_model: str = "",
    plugin_llm_api_key: Optional[str] = None,
    plugin_llm_api_base: Optional[str] = None,
) -> ScanEvaluationResult:
    """
    Evaluate a single benchmark repository.

    Args:
        repo_name: Name of the benchmark repo
        verbose: Print detailed output
        use_cache: Use cached files if available
        fuzzy_paths: Enable fuzzy path matching
        use_llm: Enable LLM passes (Stage 2.5) for deeper discovery

    Returns:
        ScanEvaluationResult
    """
    mode_normalized = (mode or "api").strip().lower()
    mode_str = (
        "aibom-api" if mode_normalized == "api" else ("regex+LLM" if use_llm else "regex-only")
    )
    logger.info(f"Evaluating: {repo_name} ({mode_str})")
    start_time = time.time()

    # Load ground truth
    gt = load_ground_truth(repo_name)

    # Check if this benchmark should be skipped
    if gt.skip:
        logger.info(f"  SKIPPED: {gt.skip_reason or 'No reason provided'}")
        return ScanEvaluationResult(
            repo_name=repo_name,
            precision=0.0,
            recall=0.0,
            f1_score=0.0,
            true_positives=0,
            false_positives=0,
            false_negatives=0,
            by_type={},
            skipped=True,
            skip_reason=gt.skip_reason,
            processing_time_ms=int((time.time() - start_time) * 1000),
        )

    logger.info(f"  Ground truth: {len(gt.assets)} assets, frameworks: {gt.frameworks}")

    discovered: List[DiscoveredAsset] = []
    sbom_dict: Dict[str, Any] = {}

    if mode_normalized == "api":
        if gt.repo_url.startswith("local://"):
            logger.info(f"  Running local folder discovery fallback for: {gt.repo_name}")
            cache_path = REPOS_DIR / repo_name / "cached_files.json"
            if not cache_path.exists():
                raise FileNotFoundError(f"Missing cached files for local benchmark: {cache_path}")
            temp_dir = _write_cached_files_to_temp_dir(cache_path)
            try:
                discovered, sbom_dict = _run_local_folder_discovery(temp_dir, use_llm=use_llm)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            logger.info(f"  Running local discovery via cached files for: {gt.repo_url}")
            cache_path = REPOS_DIR / repo_name / "cached_files.json"
            if not cache_path.exists():
                raise FileNotFoundError(
                    f"No cached files for '{repo_name}'. "
                    f"Run the fetcher first or provide a cached_files.json at {cache_path}"
                )
            temp_dir = _write_cached_files_to_temp_dir(cache_path)
            try:
                discovered, sbom_dict = _run_local_folder_discovery(temp_dir, use_llm=use_llm)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        # Local mode: use _run_local_folder_discovery (via temp dir) so sbom_dict is populated.
        cache_path = REPOS_DIR / repo_name / "cached_files.json"
        files: List[Tuple[str, str]] = []

        if use_cache and cache_path.exists():
            logger.info("  Using cached files")
            temp_dir = _write_cached_files_to_temp_dir(cache_path)
            try:
                discovered, sbom_dict = _run_local_folder_discovery(temp_dir, use_llm=use_llm)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            logger.info(f"  Fetching from GitHub: {gt.repo_url}")
            token = os.getenv("GITHUB_TOKEN")
            fetch_result = await fetch_repo_for_benchmark(gt.model_dump(), token)

            if fetch_result.errors:
                logger.warning(f"  Fetch errors: {fetch_result.errors[:3]}")

            files = fetch_result.files
            logger.info(f"  Fetched {len(files)} files")

            if files:
                cache_data = {"files": [{"path": p, "content": c} for p, c in files]}
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(cache_data, f)
                logger.info(f"  Cached {len(files)} files to {cache_path.name}")

                # Write live-fetched files to temp dir for unified pipeline
                temp_dir = _write_cached_files_to_temp_dir(cache_path)
                try:
                    discovered, sbom_dict = _run_local_folder_discovery(temp_dir, use_llm=use_llm)
                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                discovered = await run_discovery_pipeline(files, gt.frameworks, use_llm=use_llm)
    logger.info(f"  Discovered: {len(discovered)} assets")

    # Debug: Log discovered MODELs for troubleshooting
    if verbose:
        model_assets = [a for a in discovered if a.asset_type == "MODEL"]
        if model_assets:
            logger.info(f"  Discovered MODELs ({len(model_assets)}):")
            for m in model_assets:
                logger.info(f"    - {m.name} @ {m.file_path}:{m.line_start}")

    # Evaluate
    evaluation_result = evaluate_discovery(gt, discovered, fuzzy_paths=fuzzy_paths)
    evaluation_result.discovered_assets = discovered  # Store all discovered assets for CSV export
    evaluation_result.processing_time_ms = int((time.time() - start_time) * 1000)

    # Always run toolbox plugins
    if sbom_dict:
        run_bench_plugins(
            sbom_dict,
            repo_name,
            TEST_RESULTS_DIR,
            plugin_llm_model=plugin_llm_model,
            plugin_llm_api_key=plugin_llm_api_key,
            plugin_llm_api_base=plugin_llm_api_base,
        )

    # Log results
    logger.info(f"  Precision: {evaluation_result.precision:.2%}")
    logger.info(f"  Recall: {evaluation_result.recall:.2%}")
    logger.info(f"  F1 Score: {evaluation_result.f1_score:.2%}")

    if verbose:
        if evaluation_result.false_positive_details:
            logger.info(f"  False Positives ({len(evaluation_result.false_positive_details)}):")
            for fp in evaluation_result.false_positive_details[:5]:
                logger.info(f"    - {fp['asset_type']}: {fp['name']} @ {fp['file_path']}")

        if evaluation_result.false_negative_details:
            logger.info(f"  False Negatives ({len(evaluation_result.false_negative_details)}):")
            for fn in evaluation_result.false_negative_details[:5]:
                logger.info(f"    - {fn['asset_type']}: {fn['name']} @ {fn['file_path']}")

    return evaluation_result


async def evaluate_all(
    verbose: bool = False,
    use_cache: bool = True,
    fuzzy_paths: bool = True,
    use_llm: bool = False,
    mode: str = "api",
    data_service_url: str = "http://localhost:8000",
    asset_service_url: str = "http://localhost:8004",
    auth_token: Optional[str] = None,
    auth_email: Optional[str] = None,
    auth_password: Optional[str] = None,
    github_token: Optional[str] = None,
    timeout_seconds: float = 300.0,
    plugin_llm_model: str = "",
    plugin_llm_api_key: Optional[str] = None,
    plugin_llm_api_base: Optional[str] = None,
) -> BenchmarkSuiteResult:
    """
    Evaluate all available benchmark repositories.

    Args:
        verbose: Print detailed output
        use_cache: Use cached files if available
        fuzzy_paths: Enable fuzzy path matching
        use_llm: Enable LLM passes (Stage 2.5) for deeper discovery

    Returns:
        BenchmarkSuiteResult with aggregated metrics
    """
    repos = list_available_benchmarks()

    if not repos:
        logger.warning("No benchmark repositories found")
        return BenchmarkSuiteResult(
            total_repos=0,
            overall_precision=0.0,
            overall_recall=0.0,
            overall_f1=0.0,
            total_true_positives=0,
            total_false_positives=0,
            total_false_negatives=0,
            evaluated_at=datetime.now().isoformat(),
        )

    logger.info(f"Found {len(repos)} benchmark repositories")
    mode_str = (
        "aibom-api" if mode.strip().lower() == "api" else ("regex+LLM" if use_llm else "regex-only")
    )
    logger.info(f"Discovery mode: {mode_str}")

    # Evaluate each repo
    results: Dict[str, ScanEvaluationResult] = {}
    skipped_repos: List[str] = []
    for repo_name in repos:
        try:
            result = await evaluate_repo(
                repo_name,
                verbose=verbose,
                use_cache=use_cache,
                fuzzy_paths=fuzzy_paths,
                use_llm=use_llm,
                mode=mode,
                data_service_url=data_service_url,
                asset_service_url=asset_service_url,
                auth_token=auth_token,
                auth_email=auth_email,
                auth_password=auth_password,
                github_token=github_token,
                timeout_seconds=timeout_seconds,
                plugin_llm_model=plugin_llm_model,
                plugin_llm_api_key=plugin_llm_api_key,
                plugin_llm_api_base=plugin_llm_api_base,
            )
            results[repo_name] = result
            if result.skipped:
                skipped_repos.append(repo_name)
        except Exception as e:
            logger.error(f"Failed to evaluate {repo_name}: {e}")
            continue

    # Filter out skipped repos for aggregation
    active_results = {k: v for k, v in results.items() if not v.skipped}

    # Aggregate metrics (only from active repos)
    total_tp = sum(r.true_positives for r in active_results.values())
    total_fp = sum(r.false_positives for r in active_results.values())
    total_fn = sum(r.false_negatives for r in active_results.values())

    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    overall_f1 = (
        2 * overall_precision * overall_recall / (overall_precision + overall_recall)
        if (overall_precision + overall_recall) > 0
        else 0.0
    )

    # Aggregate by type (only from active repos)
    by_type_aggregate: Dict[str, TypeMetrics] = {}
    all_types: Set[str] = set()
    for r in active_results.values():
        all_types.update(r.by_type.keys())

    for asset_type in all_types:
        type_tp = sum(
            r.by_type.get(asset_type, TypeMetrics()).true_positives for r in active_results.values()
        )
        type_fp = sum(
            r.by_type.get(asset_type, TypeMetrics()).false_positives
            for r in active_results.values()
        )
        type_fn = sum(
            r.by_type.get(asset_type, TypeMetrics()).false_negatives
            for r in active_results.values()
        )

        type_precision = type_tp / (type_tp + type_fp) if (type_tp + type_fp) > 0 else 0.0
        type_recall = type_tp / (type_tp + type_fn) if (type_tp + type_fn) > 0 else 0.0
        type_f1 = (
            2 * type_precision * type_recall / (type_precision + type_recall)
            if (type_precision + type_recall) > 0
            else 0.0
        )

        by_type_aggregate[asset_type] = TypeMetrics(
            true_positives=type_tp,
            false_positives=type_fp,
            false_negatives=type_fn,
            precision=type_precision,
            recall=type_recall,
            f1_score=type_f1,
        )

    return BenchmarkSuiteResult(
        total_repos=len(results),
        overall_precision=overall_precision,
        overall_recall=overall_recall,
        overall_f1=overall_f1,
        total_true_positives=total_tp,
        total_false_positives=total_fp,
        total_false_negatives=total_fn,
        by_repo=results,
        by_type_aggregate=by_type_aggregate,
        evaluated_at=datetime.now().isoformat(),
    )


# ============================================================================
# BENCHMARK PLUGIN RUNNER
# ============================================================================


def run_bench_plugins(
    sbom: Dict[str, Any],
    repo_name: str,
    output_dir: Path,
    *,
    plugin_llm_model: str = "",
    plugin_llm_api_key: Optional[str] = None,
    plugin_llm_api_base: Optional[str] = None,
) -> Dict[str, Any]:
    """Run toolbox plugins against *sbom* and write artefacts to *output_dir*.

    Always runs the Markdown exporter.

    Returns a dict with keys:
      - ``markdown_saved``  (bool)
      - ``issues``          (list of issue dicts)
    """
    from xelo.toolbox.plugins.markdown_exporter import MarkdownExporterPlugin

    issues: List[Dict[str, Any]] = []

    # ── Markdown report ──────────────────────────────────────────────────────
    repo_out = output_dir / repo_name
    repo_out.mkdir(parents=True, exist_ok=True)
    md_plugin = MarkdownExporterPlugin()
    md_result = md_plugin.run(sbom, {})
    markdown_text: str = md_result.details.get("markdown", "")
    md_path = repo_out / "report.md"
    md_path.write_text(markdown_text, encoding="utf-8")
    markdown_saved = md_path.exists()

    return {
        "markdown_saved": markdown_saved,
        "issues": issues,
    }


def main():
    """Main entry point for CLI."""
    # Load .env file from project root (supports GITHUB_TOKEN, GEMINI_API_KEY, etc.)
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(env_path)

    parser = argparse.ArgumentParser(description="Evaluate NuGuard AI asset discovery accuracy")
    parser.add_argument("--repo", type=str, help="Evaluate a specific benchmark repository")
    parser.add_argument("--all", action="store_true", help="Evaluate all benchmark repositories")
    parser.add_argument("--list", action="store_true", help="List available benchmark repositories")
    parser.add_argument("--output", "-o", type=str, help="Output JSON results to file")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print detailed output (false positives/negatives)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_F1_THRESHOLD,
        help=f"F1 threshold for CI (default: {DEFAULT_F1_THRESHOLD})",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Don't use cached files, always fetch from GitHub"
    )
    parser.add_argument(
        "--strict-paths",
        action="store_true",
        help="Disable fuzzy path matching (require exact path match)",
    )
    parser.add_argument(
        "--enable-llm",
        dest="enable_llm",
        action="store_true",
        help="Enable LLM enrichment (mirrors the xelo CLI --enable-llm flag). Requires AISBOM_LLM_MODEL and a matching API key.",
    )
    parser.add_argument(
        "--mode",
        choices=["api", "local"],
        default="api",
        help="Discovery mode: api (uses cached files + local Xelo extractor) or local (same pipeline). Default: api.",
    )
    parser.add_argument(
        "--data-service-url",
        type=str,
        default=os.getenv("DATA_SERVICE_URL", "http://localhost:8000"),
        help="Data service base URL for auth/application ensure.",
    )
    parser.add_argument(
        "--asset-service-url",
        type=str,
        default=os.getenv("XELO_SERVICE_URL", "http://localhost:8004"),
        help="Xelo service base URL (reserved for future remote mode).",
    )
    parser.add_argument(
        "--auth-token",
        type=str,
        default=os.getenv("NUGUARD_AUTH_TOKEN"),
        help="JWT auth token for API mode (optional).",
    )
    parser.add_argument(
        "--auth-email",
        type=str,
        default=os.getenv("NUGUARD_EMAIL"),
        help="Login email for API mode when --auth-token is not provided.",
    )
    parser.add_argument(
        "--auth-password",
        type=str,
        default=os.getenv("NUGUARD_PASSWORD"),
        help="Login password for API mode when --auth-token is not provided.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="API scan timeout in seconds for API mode.",
    )
    parser.add_argument(
        "--token", "-t", type=str, help="GitHub token for API access (or set GITHUB_TOKEN in .env)"
    )
    parser.add_argument(
        "--plugin-llm-model",
        type=str,
        default=os.getenv("XELO_LLM_MODEL", ""),
        help=(
            "litellm model string for PolicyAssessmentPlugin (e.g. gpt-4o-mini). "
            "When set, policy assessment runs automatically for each NuGuard Standard "
            "policy in llm-runs/. Markdown reports are always generated."
        ),
    )
    parser.add_argument(
        "--plugin-llm-api-key",
        type=str,
        default=os.getenv("XELO_LLM_API_KEY") or None,
        help="API key for the LLM provider used by policy assessment.",
    )
    parser.add_argument(
        "--plugin-llm-api-base",
        type=str,
        default=os.getenv("XELO_LLM_API_BASE") or None,
        help="Base URL override for the LLM provider used by policy assessment.",
    )

    args = parser.parse_args()

    # CLI --token overrides env var
    if args.token:
        os.environ["GITHUB_TOKEN"] = args.token

    # Set fuzzy_paths based on strict-paths flag
    fuzzy_paths = not args.strict_paths

    # Log GitHub token status
    gh_token = os.getenv("GITHUB_TOKEN")
    if gh_token:
        logger.info(f"GitHub token loaded ({len(gh_token)} chars) - authenticated API access")
    else:
        logger.warning("No GITHUB_TOKEN found - using unauthenticated GitHub API (60 req/hr limit)")

    # List available repos
    if args.list:
        repos = list_available_benchmarks()
        if repos:
            print("Available benchmark repositories:")
            for repo in repos:
                print(f"  - {repo}")
        else:
            print("No benchmark repositories found")
            print(f"Create ground_truth.json in: {REPOS_DIR}/<repo_name>/")
        return 0

    # Check for GEMINI_API_KEY if --enable-llm is requested and model is Vertex/Gemini
    llm_model = os.getenv("AISBOM_LLM_MODEL", "")
    needs_gemini_key = "gemini" in llm_model or "vertex" in llm_model
    if args.enable_llm and needs_gemini_key and not os.getenv("GEMINI_API_KEY"):
        print("Error: --enable-llm with a Gemini/Vertex AI model requires GEMINI_API_KEY")
        print("Set it with: export GEMINI_API_KEY=your-api-key")
        return 2

    if (
        args.mode == "api"
        and not args.auth_token
        and (not args.auth_email or not args.auth_password)
    ):
        print("Error: API mode requires auth.")
        print("Provide --auth-token, or both --auth-email and --auth-password.")
        return 2

    # Evaluate single repo
    if args.repo:
        try:
            result = asyncio.run(
                evaluate_repo(
                    args.repo,
                    verbose=args.verbose,
                    use_cache=not args.no_cache,
                    fuzzy_paths=fuzzy_paths,
                    use_llm=args.enable_llm,
                    mode=args.mode,
                    data_service_url=args.data_service_url,
                    asset_service_url=args.asset_service_url,
                    auth_token=args.auth_token,
                    auth_email=args.auth_email,
                    auth_password=args.auth_password,
                    github_token=os.getenv("GITHUB_TOKEN"),
                    timeout_seconds=args.timeout_seconds,
                    plugin_llm_model=args.plugin_llm_model or "",
                    plugin_llm_api_key=args.plugin_llm_api_key,
                    plugin_llm_api_base=args.plugin_llm_api_base,
                )
            )

            mode_str = (
                "(aibom-api)"
                if args.mode == "api"
                else ("(regex+LLM)" if args.enable_llm else "(regex-only)")
            )
            print(f"\n{mode_str}")
            print(result.to_summary())

            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump(result.model_dump(), f, indent=2, default=str)
                print(f"\nResults saved to: {args.output}")

            # Check threshold
            if result.f1_score < args.threshold:
                print(f"\n[FAIL] F1 {result.f1_score:.2%} < threshold {args.threshold:.2%}")
                return 1
            else:
                print(f"\n[PASS] F1 {result.f1_score:.2%} >= threshold {args.threshold:.2%}")
                return 0

        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 2
        except Exception as e:
            print(f"Error evaluating {args.repo}: {e}")
            import traceback

            traceback.print_exc()
            return 2

    # Evaluate all repos
    if args.all:
        result = asyncio.run(
            evaluate_all(
                verbose=args.verbose,
                use_cache=not args.no_cache,
                fuzzy_paths=fuzzy_paths,
                use_llm=args.enable_llm,
                mode=args.mode,
                data_service_url=args.data_service_url,
                asset_service_url=args.asset_service_url,
                auth_token=args.auth_token,
                auth_email=args.auth_email,
                auth_password=args.auth_password,
                github_token=os.getenv("GITHUB_TOKEN"),
                timeout_seconds=args.timeout_seconds,
                plugin_llm_model=args.plugin_llm_model or "",
                plugin_llm_api_key=args.plugin_llm_api_key,
                plugin_llm_api_base=args.plugin_llm_api_base,
            )
        )

        mode_str = (
            "(aibom-api)"
            if args.mode == "api"
            else ("(regex+LLM)" if args.enable_llm else "(regex-only)")
        )
        print("\n" + "=" * 60)
        print(f"BENCHMARK SUITE RESULTS {mode_str}")
        print("=" * 60)
        print(f"Repositories evaluated: {result.total_repos}")
        print(f"Overall Precision: {result.overall_precision:.2%}")
        print(f"Overall Recall: {result.overall_recall:.2%}")
        print(f"Overall F1 Score: {result.overall_f1:.2%}")
        print(
            f"Total TP: {result.total_true_positives}, FP: {result.total_false_positives}, FN: {result.total_false_negatives}"
        )

        if result.by_type_aggregate:
            print("\nBy Asset Type:")
            for asset_type, metrics in sorted(result.by_type_aggregate.items()):
                print(
                    f"  {asset_type}: P={metrics.precision:.2%} R={metrics.recall:.2%} F1={metrics.f1_score:.2%}"
                )

        if result.by_repo:
            print("\nBy Repository:")
            for repo_name, repo_result in sorted(result.by_repo.items()):
                print(f"  {repo_name}: F1={repo_result.f1_score:.2%}")

        # Generate output paths with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        TEST_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        # Determine JSON output path
        if args.output:
            json_output = Path(args.output)
        else:
            json_output = TEST_RESULTS_DIR / f"evaluation_results_{timestamp}.json"

        # Save JSON results
        with open(json_output, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(), f, indent=2, default=str)
        print(f"\nResults saved to: {json_output}")

        # Export discovered assets CSV
        csv_output = TEST_RESULTS_DIR / f"discovered_assets_{timestamp}.csv"
        export_discovered_assets_csv(result, csv_output)

        # Check threshold
        if result.overall_f1 < args.threshold:
            print(f"\n[FAIL] Overall F1 {result.overall_f1:.2%} < threshold {args.threshold:.2%}")
            return 1
        else:
            print(f"\n[PASS] Overall F1 {result.overall_f1:.2%} >= threshold {args.threshold:.2%}")
            return 0

    # No action specified
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
