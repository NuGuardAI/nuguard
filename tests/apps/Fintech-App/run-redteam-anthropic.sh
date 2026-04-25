#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load .env if present ──────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +o allexport
else
  echo "WARNING: .env not found — ensure APP_USERNAME, APP_PASSWORD, GEMINI_API_KEY are set." >&2
fi

mkdir -p "$SCRIPT_DIR/reports"

# ── Log setup ─────────────────────────────────────────────────────────────────
LOG_FILE="$SCRIPT_DIR/reports/agentic-test-$(date +%Y%m%dT%H%M%S).log"
# Tee all output (stdout + stderr) to the log file, keeping console output live.
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Preparing Fintech App Agent for NuGuard Testing..."
echo "Log: $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

echo "---"
echo "Running redteam tests ..."

# redteam tests exit 2 when findings are present — expected in testing; treat as non-fatal.
uv run nuguard redteam \
  --config "$SCRIPT_DIR/nuguard-sbom-anthropic.yaml" \
  --format markdown \
  --output "$SCRIPT_DIR/reports/fintech-app-redteam.md" || true
