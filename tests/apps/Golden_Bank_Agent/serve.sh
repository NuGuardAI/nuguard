#!/usr/bin/env bash
# serve.sh — Launch the ADK API server for marketing_campaign_agent
#
# Usage:
#   ./serve.sh                  # default port 8090
#   PORT=9000 ./serve.sh        # custom port
#   ./serve.sh --reload         # auto-reload on code changes
#
# Once running, interact via:
#   curl -s http://localhost:${PORT}/docs          # Swagger UI (browser)
#
#   # 1. Create a session
#   SESSION=$(curl -s -X POST http://localhost:${PORT}/apps/marketing_campaign_agent/users/user1/sessions \
#     -H "Content-Type: application/json" -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
#
#   # 2. Run the agent
#   curl -s -X POST http://localhost:${PORT}/run \
#     -H "Content-Type: application/json" \
#     -d "{\"app_name\":\"Golden_Bank_Agent\",\"user_id\":\"user1\",\"session_id\":\"$SESSION\",
#          \"new_message\":{\"role\":\"user\",\"parts\":[{\"text\":\"An eco-friendly reusable water bottle with a built-in filter\"}]}}" \
#     | python3 -m json.tool

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8080}"

# ── Activate virtualenv if not already active ─────────────────────────────────
#if [[ -z "${VIRTUAL_ENV:-}" ]]; then
#  if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
#    source "$SCRIPT_DIR/.venv/bin/activate"
#  else
#    echo "ERROR: .venv not found. Run: python -m venv .venv && pip install -r requirements.txt" >&2
#    exit 1
#  fi
#fi

# ── Load .env if present ──────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +o allexport
fi

# ── Validate required env vars ────────────────────────────────────────────────
if [[ -z "${GOOGLE_API_KEY:-}" && -z "${GEMINI_API_KEY:-}" ]]; then
  echo "WARNING: Neither GOOGLE_API_KEY nor GEMINI_API_KEY is set. LLM calls will fail." >&2
fi

echo "Starting ADK API server for Golden_Bank_Agent on http://127.0.0.1:${PORT}"
echo "  Swagger UI : http://127.0.0.1:${PORT}/docs"
echo "  Agent name : Golden_Bank_Agent"
echo ""

# Ensure the .adk directory exists for SQLite session storage
mkdir -p "$SCRIPT_DIR/.adk"

# Kill any process already holding the port
if lsof -ti tcp:"$PORT" >/dev/null 2>&1; then
  echo "Port $PORT in use — killing existing process..."
  lsof -ti tcp:"$PORT" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

#exec adk api_server \
#  --host 127.0.0.1 \
#  --port "$PORT" \
#  --auto_create_session \
#  --session_service_uri memory:// \
#  "$@" \
#  "$SCRIPT_DIR"

exec uv run python3 -s serve.py $PORT > "$SCRIPT_DIR/reports/server.log" 2>&1