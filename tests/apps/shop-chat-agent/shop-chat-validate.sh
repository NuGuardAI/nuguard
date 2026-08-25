#!/usr/bin/env bash
# Full NuGuard pipeline against a running shop-chat-agent instance.
#
# Usage:
#   ./shop-chat-agent-test.sh                 # local docker target (nuguard.yaml)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${1:-nuguard.yaml}"
CONFIG_PATH="$SCRIPT_DIR/$CONFIG"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +o allexport
else
  echo "WARNING: .env not found — copy .env.example to .env first." >&2
fi

mkdir -p "$SCRIPT_DIR/reports"
LOG_FILE="$SCRIPT_DIR/reports/shop-chat-agent-test-$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Preparing shop-chat-agent (Shopify/shop-chat-agent) for NuGuard testing..."
echo "Config: $CONFIG_PATH"
echo "Log:    $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

echo "Running behavior analysis (static + dynamic)..."
uv run nuguard behavior \
  --config "$CONFIG_PATH" \
  --mode static+dynamic \
  --format markdown \
  -o "$SCRIPT_DIR/reports/shop-chat-agent-behavior.md" || true

echo "---"
echo "Running redteam tests..."
uv run nuguard redteam \
  --config "$CONFIG_PATH" \
  --format markdown \
  --output "$SCRIPT_DIR/reports/shop-chat-agent-redteam.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Log saved to: $LOG_FILE"
echo "Reports:      $SCRIPT_DIR/reports/"
