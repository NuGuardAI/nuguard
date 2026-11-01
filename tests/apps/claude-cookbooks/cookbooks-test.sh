#!/usr/bin/env bash
# Static-only NuGuard test against the Claude Cookbooks repo.
#
# Claude Cookbooks is a set of example notebooks/agents, not a running
# application — there's no live endpoint to drive behavior/redteam against,
# so this script only exercises the static pipeline: sbom generate -> analyze.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$SCRIPT_DIR/reports"

LOG_FILE="$SCRIPT_DIR/reports/sbom-test-$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Generating AI-SBOM for claude-cookbooks (from GitHub, no local checkout)..."
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

uv run nuguard sbom generate \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format json \
  -o "$SCRIPT_DIR/claude-cookbooks.sbom.json"

echo "SBOM generated successfully."
echo "---"

echo "Security Analysis Check..."

# analyze exits 2 when findings are present — expected in testing; treat as non-fatal
uv run nuguard analyze \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format markdown \
  -o "$SCRIPT_DIR/reports/claude-cookbooks-sec-analysis.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Log saved to: $LOG_FILE"
echo "Report:       $SCRIPT_DIR/reports/claude-cookbooks-sec-analysis.md"
