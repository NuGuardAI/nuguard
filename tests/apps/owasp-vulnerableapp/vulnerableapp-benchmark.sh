#!/usr/bin/env bash
# Runs nuguard pentest against the deployed VulnerableApp instance, then submits
# the findings to VulnerableApp's own benchmark comparator
# (https://github.com/SasanLabs/VulnerableApp/blob/master/benchmarks/README.md)
# to score NuGuard's real detection coverage against ground truth.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$SCRIPT_DIR/reports"

BASE_URL="${BASE_URL:-http://vulnerableapp-demo.eastus2.azurecontainer.io:9090/VulnerableApp}"
REPORT_JSON="$SCRIPT_DIR/reports/vulnerableapp-pentest.json"
COVERAGE_JSON="$SCRIPT_DIR/reports/vulnerableapp-benchmark-coverage.json"

echo "Running pentest against $BASE_URL ..."

# pentest exits 2 when findings meet output.fail_on — expected in testing; treat as non-fatal.
uv run nuguard pentest \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --acknowledge-authorization \
  --allow-active-fuzzing \
  --format json \
  --output "$REPORT_JSON" || true

echo "---"
echo "Submitting findings to VulnerableApp's benchmark comparator ..."

python3 "$SCRIPT_DIR/vulnerableapp_benchmark.py" \
  --report "$REPORT_JSON" \
  --base-url "$BASE_URL" \
  --tool NuGuard \
  --output "$COVERAGE_JSON"

echo "---"
echo "Coverage report saved to $COVERAGE_JSON"
