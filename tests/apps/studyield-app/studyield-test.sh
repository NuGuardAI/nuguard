#!/usr/bin/env bash
# Full NuGuard pipeline against a running Studyield instance.
#
# Usage:
#   ./studyield-test.sh                 # local docker-compose target (nuguard.yaml)
#   ./studyield-test.sh nuguard-azure.yaml
#   ./studyield-test.sh nuguard-gcp.yaml
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
LOG_FILE="$SCRIPT_DIR/reports/studyield-test-$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Preparing Studyield (studyield/studyield) for NuGuard testing..."
echo "Config: $CONFIG_PATH"
echo "Log:    $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

echo "Generating AI-SBOM from source: https://github.com/studyield/studyield ..."
uv run nuguard sbom generate \
  --config "$CONFIG_PATH" \
  --format json \
  -o "$SCRIPT_DIR/studyield.sbom.json"

echo "Done."
echo "---"

echo "Drafting Cognitive Policy (LLM, grounded in the SBOM) — created at runtime, not checked in..."
uv run nuguard init \
  --path "$SCRIPT_DIR" \
  --target "$(grep -m1 '^\s*url:' "$CONFIG_PATH" | awk '{print $2}')" \
  --source "https://github.com/studyield/studyield" \
  --llm

echo "Compiling Cognitive Policy controls..."
uv run nuguard policy compile --config "$CONFIG_PATH"

echo "Cognitive Policy check..."
# exits 2 when gaps are found — expected in testing; treat as non-fatal
uv run nuguard policy check \
  --config "$CONFIG_PATH" \
  --format markdown \
  -o "$SCRIPT_DIR/reports/studyield-policy-check.md" || true

echo "---"
echo "Static security analysis..."
# exits 2 when findings are present — expected in testing; treat as non-fatal
uv run nuguard analyze \
  --config "$CONFIG_PATH" \
  --format markdown \
  -o "$SCRIPT_DIR/reports/studyield-sec-analysis.md" || true

echo "---"
echo "Running behavior analysis (static + dynamic)..."
uv run nuguard behavior \
  --config "$CONFIG_PATH" \
  --mode static+dynamic \
  --format markdown \
  -o "$SCRIPT_DIR/reports/studyield-behavior.md" || true

echo "---"
echo "Running redteam tests..."
uv run nuguard redteam \
  --config "$CONFIG_PATH" \
  --format markdown \
  --output "$SCRIPT_DIR/reports/studyield-redteam.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Log saved to: $LOG_FILE"
echo "Reports:      $SCRIPT_DIR/reports/"
