#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load .env if present ──────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  echo "Loaded environment variables from $SCRIPT_DIR/.env file."
  set +o allexport
else
  echo "WARNING: .env not found — ensure APP_USERNAME, APP_PASSWORD, GEMINI_API_KEY are set." >&2
fi

mkdir -p "$SCRIPT_DIR/reports"

# ── Log setup ─────────────────────────────────────────────────────────────────
LOG_FILE="$SCRIPT_DIR/reports/agentic-test-$(date +%Y%m%dT%H%M%S).log"
# Tee all output (stdout + stderr) to the log file, keeping console output live.
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Preparing OWASP Juice Shop for NuGuard Testing..."
echo "Log: $LOG_FILE"
echo "Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "---"

# ── Launch the target app ──────────────────────────────────────────────────────
# deploy-local.sh loads .env itself and wires ALCHEMY_API_KEY plus the
# config.local.yml chatbot override (OpenAI) into the container/checkout.
TARGET_PORT="${PORT:-3000}"
TARGET_URL="http://127.0.0.1:${TARGET_PORT}"

echo "Starting OWASP Juice Shop via deploy-local.sh..."
"$SCRIPT_DIR/deploy-local.sh" &
DEPLOY_PID=$!

cleanup() {
  echo "Stopping OWASP Juice Shop..."
  docker stop juice-shop >/dev/null 2>&1 || true
  kill "$DEPLOY_PID" >/dev/null 2>&1 || true
  wait "$DEPLOY_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "Waiting for $TARGET_URL to become ready..."
for _ in $(seq 1 60); do
  if curl -s -o /dev/null "$TARGET_URL"; then
    echo "OWASP Juice Shop is up."
    break
  fi
  sleep 2
done
curl -s -o /dev/null "$TARGET_URL" || { echo "ERROR: $TARGET_URL did not become ready." >&2; exit 1; }

echo "Preparing OWASP Juice Shop for NuGuard Testing..."

uv run nuguard sbom generate \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format json \
  -o "$SCRIPT_DIR/juice-shop.sbom.json"

echo "SBOM generated successfully."

echo "Initializing Cognitive Policy controls..."

uv run nuguard policy draft --sbom "$SCRIPT_DIR/juice-shop.sbom.json" --output "$SCRIPT_DIR/cognitive_policy.md" --force

echo "Compiling Cognitive Policy controls..."

uv run nuguard policy compile --config "$SCRIPT_DIR/nuguard.yaml"

echo "Cognitive Policy Check..."

# policy check exits 2 when gaps are found — expected in testing; treat as non-fatal
uv run nuguard policy check \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format markdown \
  -o "$SCRIPT_DIR/reports/juice-shop-policy-check.md" || true

echo "Done."

echo "Static Analysis Check..."

uv run nuguard analyze \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --llm \
  --verbose \
  --format markdown \
  -o "$SCRIPT_DIR/reports/juice-shop-analysis.md" || true

echo "Done."

echo "---"
echo "Running behavior analysis (static + dynamic)..."

# behavior exits 2 when findings are present — expected in testing; treat as non-fatal.
# --mode static+dynamic: runs SBOM×Policy alignment checks then live intent-aware probing.
uv run nuguard behavior \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --mode static+dynamic \
  --format markdown \
  -o "$SCRIPT_DIR/reports/juice-shop-behavior.md" || true

echo "---"
echo "Done: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Log saved to: $LOG_FILE"
echo "Report:       $SCRIPT_DIR/reports/juice-shop-behavior.md"

echo "---"
echo "Running redteam tests ..."

# redteam tests exit 2 when findings are present — expected in testing; treat as non-fatal.
uv run nuguard redteam \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --format markdown \
  --output "$SCRIPT_DIR/reports/juice-shop-redteam.md" || true

# Wait for the tee log-capture background process to flush all output before exiting.
# Without this, the exec > >(tee) pipe may close before the last lines reach the log file.
#wait