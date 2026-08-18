#!/usr/bin/env bash
# Run just the redteam suite against Phlox.
# Usage: ./run-redteam.sh [nuguard.yaml|nuguard-azure.yaml|nuguard-gcp.yaml]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${1:-nuguard.yaml}"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +o allexport
fi

mkdir -p "$SCRIPT_DIR/reports"

uv run nuguard redteam \
  --config "$SCRIPT_DIR/$CONFIG" \
  --format markdown \
  --output "$SCRIPT_DIR/reports/phlox-redteam.md"
