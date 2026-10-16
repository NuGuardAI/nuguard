#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +a
fi
# shellcheck source=prepare-session.sh
source "$SCRIPT_DIR/prepare-session.sh"
mkdir -p "$SCRIPT_DIR/reports"
LOG_FILE="$SCRIPT_DIR/reports/behavior-$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "Running OWASP FinBot behavior tests. Log: $LOG_FILE"
uv run nuguard behavior \
  --config "$SCRIPT_DIR/nuguard.yaml" \
  --mode dynamic \
  --format markdown \
  --verbose \
  --output "$SCRIPT_DIR/reports/finbot-behavior.md" || {
  status=$?
  # Findings exit 2; authentication and execution errors must fail the wrapper.
  [[ "$status" -eq 2 ]] || exit "$status"
}
