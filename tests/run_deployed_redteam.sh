#!/usr/bin/env bash
# Run NuGuard redteam scan against the deployed openai-cs-agents-demo.
#
# Usage:
#   bash tests/run_deployed_redteam.sh [--skip-sbom] [--profile ci|full]
#
# Keys are loaded from tests/redteam/.env automatically.
# Output files are written to tests/output/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="$REPO_ROOT/tests/output"
FIXTURE_DIR="$REPO_ROOT/tests/benchmark/fixtures/openai-cs-agents-demo"
ENV_FILE="$SCRIPT_DIR/redteam/.env"

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
SKIP_SBOM=0
PROFILE=""

for arg in "$@"; do
  case "$arg" in
    --skip-sbom) SKIP_SBOM=1 ;;
    --profile=*) PROFILE="${arg#--profile=}" ;;
    --profile)   shift; PROFILE="$1" ;;
  esac
done

# ---------------------------------------------------------------------------
# Load env vars
# ---------------------------------------------------------------------------
if [[ -f "$ENV_FILE" ]]; then
  echo "Loading env from $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "WARNING: $ENV_FILE not found — using current environment"
fi

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
mkdir -p "$OUTPUT_DIR"
TIMESTAMP=$(date +%Y%m%dT%H%M%SZ)
SBOM_PATH="$OUTPUT_DIR/sbom_openai-cs-agents-demo-deployed.json"
FINDINGS_PATH="$OUTPUT_DIR/findings_openai-cs-agents-demo-deployed_${TIMESTAMP}.json"

# Override profile from flag; fall back to NUGUARD_REDTEAM_PROFILE env var; default ci
EFFECTIVE_PROFILE="${PROFILE:-${NUGUARD_REDTEAM_PROFILE:-ci}}"

echo ""
echo "======================================================================"
echo " NuGuard Deployed Redteam Scan — openai-cs-agents-demo"
echo "======================================================================"
echo " Target:   https://openai-cs-agents-backend.azurewebsites.net"
echo " Profile:  $EFFECTIVE_PROFILE"
echo " SBOM:     $SBOM_PATH"
echo " Findings: $FINDINGS_PATH"
echo "======================================================================"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Generate SBOM from GitHub repo (skippable if unchanged)
# ---------------------------------------------------------------------------
if [[ $SKIP_SBOM -eq 1 && -f "$SBOM_PATH" ]]; then
  echo "[1/2] SBOM already exists — skipping (pass without --skip-sbom to regenerate)"
else
  echo "[1/2] Generating SBOM from https://github.com/NuGuardAI/openai-cs-agents-demo ..."
  uv run nuguard sbom generate \
    --from-repo "https://github.com/NuGuardAI/openai-cs-agents-demo" \
    --ref main \
    --output "$SBOM_PATH" \
    --llm
  echo "      SBOM written to $SBOM_PATH"
fi

# ---------------------------------------------------------------------------
# Step 2: Run redteam scan
# ---------------------------------------------------------------------------
echo ""
echo "[2/2] Running redteam scan (profile=$EFFECTIVE_PROFILE) ..."
uv run nuguard redteam \
  --config "$FIXTURE_DIR/nuguard.yaml" \
  --sbom "$SBOM_PATH" \
  --policy "$FIXTURE_DIR/cognitive_policy.md" \
  --target "https://openai-cs-agents-backend.azurewebsites.net" \
  --output "$FINDINGS_PATH" \
  --format json \
  --profile "$EFFECTIVE_PROFILE" \
  --fail-on critical

echo ""
echo "======================================================================"
echo " Scan complete"
echo " Findings JSON: $FINDINGS_PATH"
echo "======================================================================"
