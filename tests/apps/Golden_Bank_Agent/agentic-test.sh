#!/usr/bin/env bash

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

echo "Preparing Golden Bank Google ADK chatbot for NuGuard Testing..."
echo "Log: $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

 uv run nuguard sbom generate --config "$SCRIPT_DIR/nuguard.yaml" --source "$SCRIPT_DIR" \
   --format json -o "$SCRIPT_DIR/reports/golden-bank.sbom.json"


echo "---"
echo "Compiling Cognitive Policy controls..."

# uv run nuguard policy compile --config "$SCRIPT_DIR/nuguard.yaml"

echo "Cognitive Policy Check..."

# policy check exits 2 when gaps are found — expected in testing; treat as non-fatal
# uv run nuguard policy check --config "$SCRIPT_DIR/nuguard.yaml" \
#   --format markdown -o "$SCRIPT_DIR/reports/golden-bank-policy-check.md" || true

echo "---"
echo "Running behavior analysis (static + dynamic)..."

# behavior exits 2 when findings are present — expected in testing; treat as non-fatal.
# --mode static+dynamic: runs SBOM×Policy alignment checks then live intent-aware probing.
uv run nuguard behavior \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --mode static+dynamic \
  --format markdown \
  -o "$SCRIPT_DIR/reports/golden-bank-behavior.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Log saved to: $LOG_FILE"
echo "Report:       $SCRIPT_DIR/reports/golden-bank-behavior.md"
