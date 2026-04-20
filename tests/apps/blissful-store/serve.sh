#!/usr/bin/env bash
# serve.sh — Launch the Blissful Store webapp on port 8081
#
# Usage:
#   ./serve.sh              # default port 8081
#   PORT=9000 ./serve.sh    # custom port
#
# Logs are written to reports/server.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8081}"
REPORTS_DIR="$SCRIPT_DIR/reports"
LOG_FILE="$REPORTS_DIR/server.log"

# ── Ensure reports directory exists ──────────────────────────────────────────
mkdir -p "$REPORTS_DIR"

# ── Load .env if present ──────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +o allexport
fi

# ── Validate required env vars ────────────────────────────────────────────────
if [[ -z "${GOOGLE_API_KEY:-}" && -z "${GEMINI_API_KEY:-}" ]]; then
  echo "WARNING: Neither GOOGLE_API_KEY nor GEMINI_API_KEY is set. LLM calls may fail." >&2
fi

# ── Kill any process already holding the port ─────────────────────────────────
if lsof -ti tcp:"$PORT" >/dev/null 2>&1; then
  echo "Port $PORT in use — killing existing process..."
  lsof -ti tcp:"$PORT" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

echo "Starting Blissful Store webapp on http://127.0.0.1:${PORT}"
echo "  Logs: $LOG_FILE"
echo ""

exec env PORT="$PORT" uv run python3 "$SCRIPT_DIR/webapp/app.py" \
  >> "$LOG_FILE" 2>&1
