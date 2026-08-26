#!/usr/bin/env bash
# Full NuGuard pipeline against a running Phlox instance.
#
# Usage:
#   ./phlox-test.sh                 # local docker-compose target (nuguard.yaml)
#   ./phlox-test.sh nuguard-azure.yaml
#   ./phlox-test.sh nuguard-gcp.yaml
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
LOG_FILE="$SCRIPT_DIR/reports/phlox-test-$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Preparing Phlox (bloodworks-io/phlox) for NuGuard testing..."
echo "Config: $CONFIG_PATH"
echo "Log:    $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

echo "Generating AI-SBOM from source: https://github.com/bloodworks-io/phlox ..."
uv run nuguard sbom generate \
  --config "$CONFIG_PATH" \
  --format json \
  -o "$SCRIPT_DIR/phlox.sbom.json"

echo "Done."
echo "---"

echo "Drafting Cognitive Policy (LLM, grounded in the SBOM) — created at runtime, not checked in..."
# Only creates cognitive-policy.md / canary.example.json; nuguard.yaml already
# exists in this directory so `init` leaves it untouched.
uv run nuguard init \
  --path "$SCRIPT_DIR" \
  --target "$(grep -m1 '^\s*url:' "$CONFIG_PATH" | awk '{print $2}')" \
  --source "https://github.com/bloodworks-io/phlox" \
  --llm

echo "Compiling Cognitive Policy controls..."
uv run nuguard policy compile --config "$CONFIG_PATH"

echo "Cognitive Policy check..."
# exits 2 when gaps are found — expected in testing; treat as non-fatal
uv run nuguard policy check \
  --config "$CONFIG_PATH" \
  --format markdown \
  -o "$SCRIPT_DIR/reports/phlox-policy-check.md" || true

echo "---"
echo "Static security analysis..."
# exits 2 when findings are present — expected in testing; treat as non-fatal
uv run nuguard analyze \
  --config "$CONFIG_PATH" \
  --format markdown \
  -o "$SCRIPT_DIR/reports/phlox-sec-analysis.md" || true

echo "---"
echo "Running behavior analysis (static + dynamic)..."
uv run nuguard behavior \
  --config "$CONFIG_PATH" \
  --mode static+dynamic \
  --format markdown \
  -o "$SCRIPT_DIR/reports/phlox-behavior.md" || true

echo "---"
echo "Running redteam tests..."
uv run nuguard redteam \
  --config "$CONFIG_PATH" \
  --format markdown \
  --output "$SCRIPT_DIR/reports/phlox-redteam.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Log saved to: $LOG_FILE"
echo "Reports:      $SCRIPT_DIR/reports/"
