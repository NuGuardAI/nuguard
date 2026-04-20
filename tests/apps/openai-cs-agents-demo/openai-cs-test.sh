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

echo "Preparing OpenAI CS Agent for NuGuard Testing..."
echo "Log: $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

echo "Preparing OpenAI CS Agent for NuGuard Testing..."

uv run nuguard sbom generate \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format json \
  -o "$SCRIPT_DIR/openai-cs.sbom.json"

echo "SBOM generated successfully."

#echo "Compiling Cognitive Policy controls..."

#uv run nuguard policy compile --config "$SCRIPT_DIR/nuguard.yaml"

#echo "Cognitive Policy Check..."

# policy check exits 2 when gaps are found — expected in testing; treat as non-fatal
#uv run nuguard policy check \
#  --config "$SCRIPT_DIR/nuguard.yaml" \
#  --format markdown \
#  -o "$SCRIPT_DIR/reports/openai-cs-policy-check.md" || true

#echo "Done."

echo "---"
echo "Running behavior analysis (static + dynamic)..."

# behavior exits 2 when findings are present — expected in testing; treat as non-fatal.
# --mode static+dynamic: runs SBOM×Policy alignment checks then live intent-aware probing.
uv run nuguard behavior \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --mode static+dynamic \
  --format markdown \
  -o "$SCRIPT_DIR/reports/openai-cs-behavior.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Log saved to: $LOG_FILE"
echo "Report:       $SCRIPT_DIR/reports/openai-cs-behavior.md"

echo "---"
echo "Running redteam tests ..."

# redteam tests exit 2 when findings are present — expected in testing; treat as non-fatal.
uv run nuguard redteam \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format markdown \
  --output "$SCRIPT_DIR/reports/openai-cs-redteam.md" || true

# Wait for the tee log-capture background process to flush all output before exiting.
# Without this, the exec > >(tee) pipe may close before the last lines reach the log file.
#wait