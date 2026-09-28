#!/usr/bin/env bash
# Full source-analysis and deployed-target workflow for OWASP VulnerableApp.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/nuguard.yaml}"
LOG_FILE="$SCRIPT_DIR/reports/vulnerableapp-test-$(date -u +%Y%m%dT%H%M%SZ).log"

mkdir -p "$SCRIPT_DIR/reports"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Preparing OWASP VulnerableApp for NuGuard testing ..."
echo "Config: $CONFIG_FILE"
echo "Log:    $LOG_FILE"

echo "Generating VulnerableApp SBOM ..."
uv run nuguard sbom generate \
  --config "$CONFIG_FILE" \
  --format json \
  --output "$SCRIPT_DIR/vulnerableapp.sbom.json"

echo "Running static analysis ..."
set +e
uv run nuguard analyze \
  --config "$CONFIG_FILE" \
  --atlas --osv --trivy --grype --checkov \
  --format markdown \
  --output "$SCRIPT_DIR/reports/vulnerableapp-analysis.md"
ANALYZE_STATUS=$?
set -e
if (( ANALYZE_STATUS > 1 )); then
  echo "ERROR: Static analysis failed with exit code $ANALYZE_STATUS." >&2
  exit "$ANALYZE_STATUS"
fi

echo "Running deployed-target pentest and benchmark ..."
CONFIG_FILE="$CONFIG_FILE" "$SCRIPT_DIR/vulnerableapp-benchmark.sh"

echo "Completed OWASP VulnerableApp testing."
echo "Analysis report: $SCRIPT_DIR/reports/vulnerableapp-analysis.md"
echo "Log:             $LOG_FILE"
