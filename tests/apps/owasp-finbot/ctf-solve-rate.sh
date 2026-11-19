#!/usr/bin/env bash
# Score a FinBot redteam report against the finbot-ctf challenge benchmark.
# Usage: ctf-solve-rate.sh [reports/finbot-redteam.md]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT="${1:-$SCRIPT_DIR/reports/finbot-redteam.md}"
[[ -s "$REPORT" ]] || { echo "ERROR: report not found: $REPORT" >&2; exit 1; }
uv run python - "$SCRIPT_DIR/ctf-benchmark.yaml" "$REPORT" <<'PY'
import sys
from pathlib import Path
from nuguard.redteam.workflows.benchmark import score

print(score(Path(sys.argv[1]), Path(sys.argv[2]).read_text(encoding="utf-8")).to_markdown())
PY
