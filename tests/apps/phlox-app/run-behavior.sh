#!/usr/bin/env bash
# Run just the behavior suite against Phlox.
# Usage: ./run-behavior.sh [nuguard.yaml|nuguard-azure.yaml|nuguard-gcp.yaml]
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

uv run nuguard behavior \
  --config "$SCRIPT_DIR/$CONFIG" \
  --format markdown \
  -o "$SCRIPT_DIR/reports/phlox-behavior.md"
