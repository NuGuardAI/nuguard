"""Compare generated sparkflows.sbom.json against the hand-curated ground truth."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASE = Path("tests/apps/sparkflows")
GENERATED = BASE / "sparkflows.sbom.json"
GROUND_TRUTH = BASE / "sparkflows.ground-truth.sbom.json"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _key(node: dict) -> tuple[str, str]:
    return (node["component_type"], _norm(node["name"]))


def main() -> int:
    generated = json.loads(GENERATED.read_text())["nodes"]
    ground_truth = json.loads(GROUND_TRUTH.read_text())["nodes"]

    gen_by_key = {_key(n): n for n in generated}
    gt_by_key = {_key(n): n for n in ground_truth}

    matched = sorted(set(gen_by_key) & set(gt_by_key))
    missing = sorted(set(gt_by_key) - set(gen_by_key))  # in ground truth, not generated (false negatives)
    extra = sorted(set(gen_by_key) - set(gt_by_key))  # in generated, not ground truth (not necessarily wrong)

    precision = len(matched) / len(gen_by_key) if gen_by_key else 0.0
    recall = len(matched) / len(gt_by_key) if gt_by_key else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    print(f"generated nodes: {len(generated)} (unique keys: {len(gen_by_key)})")
    print(f"ground-truth nodes: {len(ground_truth)} (unique keys: {len(gt_by_key)})")
    print(f"matched: {len(matched)}")
    print(f"precision={precision:.2f} recall={recall:.2f} f1={f1:.2f}")
    print()

    if missing:
        print(f"MISSING from generated SBOM ({len(missing)}) — ground truth expects these but extractor didn't find them:")
        for ctype, norm in missing:
            gt = gt_by_key[(ctype, norm)]
            print(f"  [{ctype}] {gt['name']}")
    else:
        print("MISSING: none — every ground-truth node was found.")
    print()

    print(f"EXTRA in generated SBOM not in ground truth ({len(extra)}) — expected, ground truth is representative-only:")
    for ctype, norm in extra:
        gen = gen_by_key[(ctype, norm)]
        print(f"  [{ctype}] {gen['name']}")

    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
