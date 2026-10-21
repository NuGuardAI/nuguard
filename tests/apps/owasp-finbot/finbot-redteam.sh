#!/usr/bin/env bash
# Run conventional pentest followed by AI redteam, or select one stage.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE=all
case "${1:-}" in
  --pentest-only) MODE=pentest; shift ;;
  --redteam-only) MODE=redteam; shift ;;
  -h|--help)
    echo "Usage: $(basename "$0") [--pentest-only|--redteam-only]"
    echo "Pentest defaults to finbot-app's private Docker IP; override FINBOT_PENTEST_TARGET."
    exit 0 ;;
esac
[[ $# -eq 0 ]] || { echo "ERROR: Unknown argument: $1" >&2; exit 1; }
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +a
fi
mkdir -p "$SCRIPT_DIR/reports"
LOG_FILE="$SCRIPT_DIR/reports/finbot-security-$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "FinBot security tests. Log: $LOG_FILE"
if [[ "$MODE" != redteam ]]; then
  "$SCRIPT_DIR/finbot-pentest.sh"
fi
if [[ "$MODE" != pentest ]]; then
  # Pentesting can invalidate sessions. Start redteam with a fresh pair at
  # the configured chat origin, independent of the conventional scanner.
  unset FINBOT_SESSION_URL
  # shellcheck source=prepare-session.sh
  source "$SCRIPT_DIR/prepare-session.sh"
  REDTEAM_TMP="$(mktemp -d "${TMPDIR:-/tmp}/finbot-redteam.XXXXXX")"
  trap 'rm -rf "$REDTEAM_TMP"' EXIT
  status=0
  uv run nuguard redteam \
    --config "$CONFIG_FILE" \
    --format markdown \
    --output "$REDTEAM_TMP/report.md" || status=$?
  if [[ "$status" -ne 0 && "$status" -ne 2 ]] || [[ ! -s "$REDTEAM_TMP/report.md" ]]; then
    echo "ERROR: FinBot redteam failed (exit $status); no completed report published." >&2
    (( status != 0 )) || status=1
    exit "$status"
  fi
  mv "$REDTEAM_TMP/report.md" "$SCRIPT_DIR/reports/finbot-redteam.md"
  echo "Report: $SCRIPT_DIR/reports/finbot-redteam.md"
fi
