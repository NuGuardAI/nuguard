#!/usr/bin/env bash
# Run just the redteam suite against Phlox.
# Usage: ./run-redteam.sh [nuguard.yaml|nuguard-azure.yaml|nuguard-gcp.yaml]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${1:-nuguard.yaml}"
CONFIG_PATH="$SCRIPT_DIR/$CONFIG"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +o allexport
fi

LOG_FILE="$SCRIPT_DIR/reports/phlox-test-$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Preparing Phlox (bloodworks-io/phlox) for NuGuard testing..."
echo "Config: $CONFIG_PATH"
echo "Log:    $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

mkdir -p "$SCRIPT_DIR/reports"
echo "Generating AI-SBOM from source: https://github.com/bloodworks-io/phlox ..."

uv run nuguard sbom generate \
  --config "$CONFIG_PATH" \
  --format json \
  -o "$SCRIPT_DIR/phlox.sbom.json"

uv run nuguard redteam \
  --config "$CONFIG_PATH" \
  --format markdown \
  --output "$SCRIPT_DIR/reports/phlox-redteam.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Log saved to: $LOG_FILE"
echo "Reports:      $SCRIPT_DIR/reports/"
