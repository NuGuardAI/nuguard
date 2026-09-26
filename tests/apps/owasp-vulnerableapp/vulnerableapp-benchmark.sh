#!/usr/bin/env bash
# Run NuGuard pentest against VulnerableApp and submit the resulting findings
# to VulnerableApp's built-in scanner benchmark comparator.
#
# Usage: vulnerableapp-benchmark.sh [--reuse-report]
#   --reuse-report  benchmark the existing reports/vulnerableapp-pentest.json
#                   (e.g. from vulnerableapp-pentest.sh) without rescanning.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/nuguard.yaml}"
BASE_URL="${VULNERABLEAPP_URL:-http://vulnerableapp-demo.eastus2.azurecontainer.io:9090/VulnerableApp}"
REPORT_JSON="$SCRIPT_DIR/reports/vulnerableapp-pentest.json"
COVERAGE_JSON="$SCRIPT_DIR/reports/vulnerableapp-benchmark-coverage.json"
SBOM_FILE="$SCRIPT_DIR/vulnerableapp.sbom.json"
REPORT_MD="$SCRIPT_DIR/reports/vulnerableapp-pentest.md"

REUSE_REPORT=0
for arg in "$@"; do
  case "$arg" in
    --reuse-report) REUSE_REPORT=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$SCRIPT_DIR/reports"

echo "Checking VulnerableApp scanner API at $BASE_URL/scanner/dast ..."
curl --fail --silent --show-error --max-time 30 \
  "$BASE_URL/scanner/dast" >/dev/null

if (( REUSE_REPORT == 0 )); then
  if [[ ! -s "$SBOM_FILE" ]]; then
    echo "Generating VulnerableApp SBOM ..."
    uv run nuguard sbom generate \
      --config "$CONFIG_FILE" \
      --format json \
      --output "$SBOM_FILE"
  fi

  echo "Running NuGuard pentest against $BASE_URL ..."
  set +e
  uv run nuguard pentest \
    --config "$CONFIG_FILE" \
    --target "$BASE_URL" \
    --acknowledge-authorization \
    --allow-active-fuzzing \
    --profile standard \
    --format markdown,json \
    --output "$REPORT_MD"
  PENTEST_STATUS=$?
  set -e

  # Exit 1 means findings met the configured threshold; the report is still
  # valid and should be benchmarked. Exit 2 indicates a scan/configuration error.
  if (( PENTEST_STATUS > 1 )); then
    echo "ERROR: NuGuard pentest failed with exit code $PENTEST_STATUS." >&2
    exit "$PENTEST_STATUS"
  fi
fi

if [[ ! -s "$REPORT_JSON" ]]; then
  echo "ERROR: NuGuard did not produce $REPORT_JSON." >&2
  exit 2
fi

echo "Submitting findings to VulnerableApp's benchmark comparator ..."
python3 "$SCRIPT_DIR/vulnerableapp_benchmark.py" \
  --report "$REPORT_JSON" \
  --base-url "$BASE_URL" \
  --tool NuGuard \
  --output "$COVERAGE_JSON"

echo "Pentest report:  $REPORT_JSON"
echo "Coverage report: $COVERAGE_JSON"
