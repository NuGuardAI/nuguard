"""
Evaluate AI Streaming Service against Benchmark Ground Truth.

This script:
1. Runs the AI streaming service against benchmark repos
2. Compares discovered assets to ground truth
3. Calculates precision, recall, and F1 scores
4. Generates a detailed report

Usage:
    python benchmark/evaluate_streaming.py --repo openai-swarm
    python benchmark/evaluate_streaming.py --all
    python benchmark/evaluate_streaming.py --all --output results.json
"""
import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
import httpx

# Load .env file from project root (supports GITHUB_TOKEN, etc.)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

BENCHMARK_DIR = Path(__file__).parent / "repos"
SERVICE_URL = "http://localhost:8003"


def _convert_xelo_ground_truth_to_legacy(repo_name: str, payload: dict) -> dict:
    """Convert Xelo-native ground truth to legacy asset-list format used by this script."""
    nodes = payload.get("nodes", []) if isinstance(payload.get("nodes"), list) else []
    assets: List[dict] = []
    for node in nodes:
        component_type = str(node.get("component_type") or node.get("type") or "").upper()
        if not component_type:
            continue
        evidence = node.get("evidence", [])
        first_ev = evidence[0] if isinstance(evidence, list) and evidence else {}
        location = first_ev.get("location", {}) if isinstance(first_ev, dict) else {}
        metadata = node.get("metadata", {}) if isinstance(node.get("metadata"), dict) else {}
        extras = metadata.get("extras", {}) if isinstance(metadata.get("extras"), dict) else {}
        description = extras.get("description")
        if not isinstance(description, str):
            description = ""

        assets.append(
            {
                "asset_type": component_type,
                "name": node.get("name", ""),
                "file_path": location.get("path", ""),
                "line_start": location.get("line"),
                "description": description,
                "framework": metadata.get("framework"),
            }
        )

    return {
        "repo_name": repo_name,
        "repo_url": payload.get("target", ""),
        "assets": assets,
    }


@dataclass
class AssetMatch:
    """Result of matching a discovered asset to ground truth."""
    ground_truth_name: str
    ground_truth_type: str
    discovered_name: Optional[str] = None
    discovered_type: Optional[str] = None
    matched: bool = False
    match_type: str = "none"  # exact, fuzzy, type_only, none


@dataclass 
class ScanEvaluationResult:
    """Evaluation metrics for a single repo."""
    repo_name: str
    repo_url: str
    ground_truth_count: int
    discovered_count: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1_score: float
    by_type: Dict[str, Dict[str, int]] = field(default_factory=dict)
    matches: List[AssetMatch] = field(default_factory=list)
    false_positive_assets: List[dict] = field(default_factory=list)
    discovery_time_seconds: float = 0.0
    error: Optional[str] = None


def load_ground_truth(repo_name: str) -> Optional[dict]:
    """Load ground truth for a benchmark repo."""
    gt_path = BENCHMARK_DIR / repo_name / "ground_truth.json"
    if not gt_path.exists():
        return None
    with open(gt_path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict) and "schema_version" in data and "nodes" in data:
        return _convert_xelo_ground_truth_to_legacy(repo_name, data)
    return data


def list_benchmark_repos() -> List[str]:
    """List all available benchmark repos."""
    repos = []
    if BENCHMARK_DIR.exists():
        for item in BENCHMARK_DIR.iterdir():
            if item.is_dir() and (item / "ground_truth.json").exists():
                repos.append(item.name)
    return sorted(repos)


async def run_discovery(repo_url: str, github_token: Optional[str] = None) -> Tuple[List[dict], float, Optional[str]]:
    """Run AI streaming service discovery on a repo."""
    import os
    import time
    
    token = github_token or os.getenv("GITHUB_TOKEN")
    
    payload = {
        "github_url": repo_url,
        "branch": "main",
    }
    if token:
        payload["github_token"] = token
    
    start_time = time.time()
    
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{SERVICE_URL}/stream/analyze/github/discovery",
                json=payload
            )
            elapsed = time.time() - start_time
            
            if response.status_code != 200:
                return [], elapsed, f"HTTP {response.status_code}: {response.text[:200]}"
            
            data = response.json()
            # Handle streaming service response format
            assets = data.get("detected_assets", [])
            if not assets:
                # Try alternate response structure
                assets = data.get("discovery", {}).get("detected_assets", [])
            return assets, elapsed, None
            
    except Exception as e:
        elapsed = time.time() - start_time
        return [], elapsed, str(e)


def normalize_name(name: str) -> str:
    """Normalize asset name for comparison."""
    return name.lower().replace("_", "").replace("-", "").replace(" ", "")


def normalize_type(asset_type: str) -> str:
    """Normalize asset type for comparison."""
    return asset_type.upper().strip()


def match_assets(
    ground_truth_assets: List[dict],
    discovered_assets: List[dict]
) -> Tuple[List[AssetMatch], List[dict]]:
    """Match discovered assets against ground truth."""
    matches = []
    matched_discovered_indices = set()
    
    # Create lookup for discovered assets
    discovered_by_type: Dict[str, List[Tuple[int, dict]]] = {}
    for i, d in enumerate(discovered_assets):
        d_type = normalize_type(d.get("type", ""))
        if d_type not in discovered_by_type:
            discovered_by_type[d_type] = []
        discovered_by_type[d_type].append((i, d))
    
    # Try to match each ground truth asset
    for gt in ground_truth_assets:
        gt_name = gt.get("name", "")
        gt_type = normalize_type(gt.get("asset_type", ""))
        gt_name_norm = normalize_name(gt_name)
        
        match = AssetMatch(
            ground_truth_name=gt_name,
            ground_truth_type=gt_type,
        )
        
        # Look for matches of same type
        candidates = discovered_by_type.get(gt_type, [])
        
        best_match = None
        best_match_score = 0
        
        for idx, disc in candidates:
            if idx in matched_discovered_indices:
                continue
            
            disc_name = disc.get("name", "")
            disc_name_norm = normalize_name(disc_name)
            
            # Exact match
            if disc_name_norm == gt_name_norm:
                best_match = (idx, disc, "exact")
                best_match_score = 100
                break
            
            # Fuzzy match - one contains the other
            if gt_name_norm in disc_name_norm or disc_name_norm in gt_name_norm:
                if best_match_score < 80:
                    best_match = (idx, disc, "fuzzy")
                    best_match_score = 80
            
            # Partial match - share significant substring
            if len(gt_name_norm) >= 3 and len(disc_name_norm) >= 3:
                # Check for common prefix/suffix
                common_len = 0
                for k in range(min(len(gt_name_norm), len(disc_name_norm)), 2, -1):
                    if gt_name_norm[:k] == disc_name_norm[:k] or gt_name_norm[-k:] == disc_name_norm[-k:]:
                        common_len = k
                        break
                if common_len >= 4 and best_match_score < 60:
                    best_match = (idx, disc, "partial")
                    best_match_score = 60
        
        if best_match:
            idx, disc, match_type = best_match
            match.discovered_name = disc.get("name")
            match.discovered_type = normalize_type(disc.get("type", ""))
            match.matched = True
            match.match_type = match_type
            matched_discovered_indices.add(idx)
        
        matches.append(match)
    
    # Collect false positives (discovered but not matched)
    false_positives = [
        discovered_assets[i] for i in range(len(discovered_assets))
        if i not in matched_discovered_indices
    ]
    
    return matches, false_positives


def calculate_metrics(
    ground_truth: dict,
    discovered_assets: List[dict],
    discovery_time: float
) -> ScanEvaluationResult:
    """Calculate precision, recall, F1 for a repo."""
    gt_assets = ground_truth.get("assets", [])
    
    matches, false_positives = match_assets(gt_assets, discovered_assets)
    
    true_positives = sum(1 for m in matches if m.matched)
    false_negatives = sum(1 for m in matches if not m.matched)
    fp_count = len(false_positives)
    
    precision = true_positives / (true_positives + fp_count) if (true_positives + fp_count) > 0 else 0.0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    # Calculate by-type metrics
    by_type: Dict[str, Dict[str, int]] = {}
    for m in matches:
        t = m.ground_truth_type
        if t not in by_type:
            by_type[t] = {"tp": 0, "fn": 0, "fp": 0}
        if m.matched:
            by_type[t]["tp"] += 1
        else:
            by_type[t]["fn"] += 1
    
    for fp in false_positives:
        t = normalize_type(fp.get("type", "UNKNOWN"))
        if t not in by_type:
            by_type[t] = {"tp": 0, "fn": 0, "fp": 0}
        by_type[t]["fp"] += 1
    
    return ScanEvaluationResult(
        repo_name=ground_truth.get("repo_name", ""),
        repo_url=ground_truth.get("repo_url", ""),
        ground_truth_count=len(gt_assets),
        discovered_count=len(discovered_assets),
        true_positives=true_positives,
        false_positives=fp_count,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1_score=f1,
        by_type=by_type,
        matches=[asdict(m) for m in matches],
        false_positive_assets=false_positives[:10],  # Limit to first 10
        discovery_time_seconds=discovery_time,
    )


async def evaluate_repo(repo_name: str, github_token: Optional[str] = None) -> ScanEvaluationResult:
    """Evaluate a single benchmark repo."""
    gt = load_ground_truth(repo_name)
    if not gt:
        return ScanEvaluationResult(
            repo_name=repo_name,
            repo_url="",
            ground_truth_count=0,
            discovered_count=0,
            true_positives=0,
            false_positives=0,
            false_negatives=0,
            precision=0.0,
            recall=0.0,
            f1_score=0.0,
            error=f"Ground truth not found for {repo_name}"
        )
    
    repo_url = gt.get("repo_url", "")
    if not repo_url:
        return ScanEvaluationResult(
            repo_name=repo_name,
            repo_url="",
            ground_truth_count=0,
            discovered_count=0,
            true_positives=0,
            false_positives=0,
            false_negatives=0,
            precision=0.0,
            recall=0.0,
            f1_score=0.0,
            error=f"No repo_url in ground truth for {repo_name}"
        )
    
    print(f"\n  Analyzing {repo_name}...")
    discovered, elapsed, error = await run_discovery(repo_url, github_token)
    
    if error:
        return ScanEvaluationResult(
            repo_name=repo_name,
            repo_url=repo_url,
            ground_truth_count=len(gt.get("assets", [])),
            discovered_count=0,
            true_positives=0,
            false_positives=0,
            false_negatives=0,
            precision=0.0,
            recall=0.0,
            f1_score=0.0,
            discovery_time_seconds=elapsed,
            error=error
        )
    
    result = calculate_metrics(gt, discovered, elapsed)
    return result


def print_result(result: ScanEvaluationResult):
    """Print evaluation result for a repo."""
    if result.error:
        print(f"\n  ❌ {result.repo_name}: ERROR - {result.error}")
        return
    
    status = "✅" if result.f1_score >= 0.80 else "⚠️" if result.f1_score >= 0.50 else "❌"
    
    print(f"\n  {status} {result.repo_name}")
    print(f"     Ground Truth: {result.ground_truth_count} | Discovered: {result.discovered_count}")
    print(f"     TP: {result.true_positives} | FP: {result.false_positives} | FN: {result.false_negatives}")
    print(f"     Precision: {result.precision:.1%} | Recall: {result.recall:.1%} | F1: {result.f1_score:.1%}")
    print(f"     Time: {result.discovery_time_seconds:.1f}s")
    
    if result.by_type:
        print("     By Type:")
        for asset_type, metrics in sorted(result.by_type.items()):
            tp, fn, fp = metrics["tp"], metrics["fn"], metrics["fp"]
            type_precision = tp / (tp + fp) if (tp + fp) > 0 else 0
            type_recall = tp / (tp + fn) if (tp + fn) > 0 else 0
            print(f"       {asset_type}: P={type_precision:.0%} R={type_recall:.0%} (TP={tp}, FN={fn}, FP={fp})")


async def main():
    parser = argparse.ArgumentParser(description="Evaluate AI streaming service against benchmarks")
    parser.add_argument("--repo", "-r", help="Specific repo to evaluate")
    parser.add_argument("--all", "-a", action="store_true", help="Evaluate all benchmark repos")
    parser.add_argument("--output", "-o", help="Output JSON file for results")
    parser.add_argument("--token", "-t", help="GitHub token (or set GITHUB_TOKEN in .env)")
    parser.add_argument("--skip-synthetic", action="store_true", help="Skip synthetic repos")
    args = parser.parse_args()
    
    # CLI --token overrides env var
    if args.token:
        os.environ["GITHUB_TOKEN"] = args.token
    
    if not args.repo and not args.all:
        print("Error: Specify --repo <name> or --all")
        parser.print_help()
        sys.exit(1)
    
    print("\n" + "=" * 70)
    print("  AI Streaming Service Benchmark Evaluation")
    print("=" * 70)
    
    # Check service availability
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{SERVICE_URL}/health")
            if resp.status_code != 200:
                print(f"\n  ❌ Service not healthy at {SERVICE_URL}")
                sys.exit(1)
    except Exception as e:
        print(f"\n  ❌ Cannot connect to service at {SERVICE_URL}: {e}")
        print("     Hint: Start with: docker compose up ai-service-stream -d")
        sys.exit(1)
    
    print(f"  Service: {SERVICE_URL} ✓")
    
    # Get repos to evaluate
    if args.all:
        repos = list_benchmark_repos()
        if args.skip_synthetic:
            repos = [r for r in repos if "synthetic" not in r.lower()]
    else:
        repos = [args.repo]
    
    print(f"  Repos to evaluate: {len(repos)}")
    
    results = []
    for repo in repos:
        result = await evaluate_repo(repo, args.token)
        results.append(result)
        print_result(result)
    
    # Summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    
    valid_results = [r for r in results if not r.error]
    if valid_results:
        avg_precision = sum(r.precision for r in valid_results) / len(valid_results)
        avg_recall = sum(r.recall for r in valid_results) / len(valid_results)
        avg_f1 = sum(r.f1_score for r in valid_results) / len(valid_results)
        total_tp = sum(r.true_positives for r in valid_results)
        total_fp = sum(r.false_positives for r in valid_results)
        total_fn = sum(r.false_negatives for r in valid_results)
        
        print(f"\n  Evaluated: {len(valid_results)} repos")
        print(f"  Total Assets: TP={total_tp}, FP={total_fp}, FN={total_fn}")
        print("\n  Average Metrics:")
        print(f"    Precision: {avg_precision:.1%}")
        print(f"    Recall:    {avg_recall:.1%}")
        print(f"    F1 Score:  {avg_f1:.1%}")
        
        # Aggregate by type
        all_types: Dict[str, Dict[str, int]] = {}
        for r in valid_results:
            for t, m in r.by_type.items():
                if t not in all_types:
                    all_types[t] = {"tp": 0, "fn": 0, "fp": 0}
                all_types[t]["tp"] += m["tp"]
                all_types[t]["fn"] += m["fn"]
                all_types[t]["fp"] += m["fp"]
        
        if all_types:
            print("\n  By Asset Type:")
            for t in sorted(all_types.keys()):
                m = all_types[t]
                tp, fn, fp = m["tp"], m["fn"], m["fp"]
                p = tp / (tp + fp) if (tp + fp) > 0 else 0
                r = tp / (tp + fn) if (tp + fn) > 0 else 0
                f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
                print(f"    {t:15} P={p:.0%} R={r:.0%} F1={f1:.0%} (TP={tp}, FN={fn}, FP={fp})")
    
    # Output JSON if requested
    if args.output:
        output_data = {
            "evaluated_at": str(Path(__file__).stat().st_mtime),
            "service_url": SERVICE_URL,
            "repos_evaluated": len(valid_results),
            "summary": {
                "avg_precision": avg_precision if valid_results else 0,
                "avg_recall": avg_recall if valid_results else 0,
                "avg_f1": avg_f1 if valid_results else 0,
            },
            "results": [asdict(r) for r in results]
        }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\n  Results saved to: {args.output}")
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
