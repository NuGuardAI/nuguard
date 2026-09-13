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

echo "Preparing OpenCode Agent for NuGuard Testing..."
echo "Log: $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

echo "Preparing OpenCode Agent for NuGuard Testing..."

uv run nuguard sbom generate \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format json \
  -o "$SCRIPT_DIR/opencode.sbom.json"

echo "SBOM generated successfully."

echo "Drafting Cognitive Policy (LLM, grounded in the SBOM) — created at runtime, not checked in..."
# Only creates cognitive-policy.md / canary.example.json; nuguard.yaml already
# exists in this directory so `init` leaves it untouched.
uv run nuguard init \
  --path "$SCRIPT_DIR" \
  --target "$(grep -m1 '^\s*url:' "$SCRIPT_DIR/nuguard.yaml" | awk '{print $2}')" \
  --source "https://github.com/anomalyco/opencode" \
  --llm

echo "Compiling Cognitive Policy controls..."

uv run nuguard policy compile --config "$SCRIPT_DIR/nuguard.yaml"

echo "Cognitive Policy Check..."

# policy check exits 2 when gaps are found — expected in testing; treat as non-fatal
uv run nuguard policy check \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format markdown \
  -o "$SCRIPT_DIR/reports/opencode-policy-check.md" || true

echo "Done."

echo "---"

echo "Security Analysis Check..."

# analyze exits 2 when findings are present — expected in testing; treat as non-fatal
uv run nuguard analyze \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format markdown \
  -o "$SCRIPT_DIR/reports/opencode-sec-analysis.md" || true

echo "Done."

echo "---"

echo "Running behavior analysis (static + dynamic)..."

# behavior exits 2 when findings are present — expected in testing; treat as non-fatal.
# --mode static+dynamic: runs SBOM×Policy alignment checks then live intent-aware probing.
uv run nuguard behavior \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --mode static+dynamic \
  --format markdown \
  -o "$SCRIPT_DIR/reports/opencode-behavior.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Log saved to: $LOG_FILE"
echo "Report:       $SCRIPT_DIR/reports/opencode-behavior.md"

echo "---"
echo "Running redteam tests ..."

# redteam tests exit 2 when findings are present — expected in testing; treat as non-fatal.
uv run nuguard redteam \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format markdown \
  --output "$SCRIPT_DIR/reports/opencode-redteam.md" || true

echo "Report:       $SCRIPT_DIR/reports/opencode-redteam.md"

echo "All tests completed."

# Wait for the tee log-capture background process to flush all output before exiting.
# Without this, the exec > >(tee) pipe may close before the last lines reach the log file.
#wait