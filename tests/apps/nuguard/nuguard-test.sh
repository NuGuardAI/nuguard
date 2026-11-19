#!/usr/bin/env bash
# Self-scan of the nuguard package with NuGuard itself.
# Pipeline: sbom generate → policy validate/compile → policy check → analyze → behavior (static).
# No live target is required, so redteam is not run.
#
# Usage: ./nuguard-test.sh [--no-llm]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/nuguard.yaml"
EXTRA_FLAGS=()

if [[ "${1:-}" == "--no-llm" ]]; then
  EXTRA_FLAGS+=(--no-llm)
fi

# ── Load .env if present ──────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +o allexport
else
  echo "WARNING: .env not found — LLM steps need AZURE_OPENAI_KEY and AZURE_API_BASE." >&2
fi

mkdir -p "$SCRIPT_DIR/reports"

# ── Log setup ─────────────────────────────────────────────────────────────────
LOG_FILE="$SCRIPT_DIR/reports/nuguard-test-$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "NuGuard self-scan"
echo "Log: $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

echo "[1/6] Generating AI-SBOM from nuguard package source..."
uv run nuguard sbom generate \
  --config "$CONFIG" \
  --format json \
  -o "$SCRIPT_DIR/nuguard.sbom.json" \
  "${EXTRA_FLAGS[@]}"

echo "[2/6] Validating cognitive policy..."
uv run nuguard policy validate --file "$SCRIPT_DIR/cognitive-policy.md"

echo "[3/6] Compiling cognitive policy..."
uv run nuguard policy compile --config "$CONFIG" "${EXTRA_FLAGS[@]}"

echo "[4/6] Cross-checking policy against SBOM..."
# policy check exits 2 when gaps are found — expected for a self-scan; treat as non-fatal.
uv run nuguard policy check \
  --config "$CONFIG" \
  --format markdown \
  -o "$SCRIPT_DIR/reports/policy-check.md" \
  "${EXTRA_FLAGS[@]}" || true

echo "[5/6] Static analysis..."
# analyze exits non-zero when findings meet fail_on — non-fatal here.
uv run nuguard analyze \
  --config "$CONFIG" \
  --verbose \
  --format markdown \
  -o "$SCRIPT_DIR/reports/analysis.md" \
  "${EXTRA_FLAGS[@]}" || true

echo "[6/6] Behavior analysis (static mode, no live target)..."
# behavior exits 2 when findings are present — non-fatal here.
uv run nuguard behavior \
  --config "$CONFIG" \
  --mode static \
  --format markdown \
  -o "$SCRIPT_DIR/reports/behavior-static.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Reports: $SCRIPT_DIR/reports/"
echo "Log saved to: $LOG_FILE"
