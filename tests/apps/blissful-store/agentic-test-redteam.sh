#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  source "$SCRIPT_DIR/.env"
  set +o allexport
else
  echo "WARNING: .env not found — ensure required env vars are set." >&2
fi

mkdir -p "$SCRIPT_DIR/reports"

# ── Log setup ─────────────────────────────────────────────────────────────────
LOG_FILE="$SCRIPT_DIR/reports/agentic-test-$(date +%Y%m%dT%H%M%S).log"
# Tee all output (stdout + stderr) to the log file, keeping console output live.
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Preparing Marketing Campaign Google ADK chatbot for NuGuard Testing..."
echo "Log: $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

echo "🚀 Preparing Blissful chatbot for NuGuard Testing... \n"

 uv run nuguard sbom generate --source . \
 --format markdown -o ./reports/blissful-sbom.json

echo "✅ SBOM generated successfully! \n"


# validate exits 2 when findings are present — expected in testing; treat as non-fatal
# uv run nuguard behavior --config ./nuguard.yaml --format markdown -o ./reports/blissful-validation.md || true
uv run nuguard redteam \
  --config "./nuguard.yaml" \
  --format markdown \
  -o "./reports/blissful-redteam.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Log saved to: $LOG_FILE"
echo "Report:       $SCRIPT_DIR/reports/blissful-redteam.md"