#!/usr/bin/env bash
# Starts a headless OpenCode server for nuguard behavior/redteam validation.
#
# OpenCode ships an HTTP server behind its TUI ("opencode serve"). It has no
# single-turn "/api/chat" endpoint — it's session based:
#   POST /session            -> { id, ... }               (create a session)
#   POST /session/:id/message -> { info, parts }           (send a turn, wait for reply)
#
# nuguard's redteam/behavior target contract expects one flat chat endpoint
# (target.endpoint + chat_payload_key/chat_response_key), so this server is
# fronted by opencode_chat_proxy.py which adapts the two calls above into a
# single POST /api/chat {message} -> {response} call. Point nuguard.yaml's
# target.url at the PROXY (see opencode_chat_proxy.py), not at this server.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +o allexport
fi

OPENCODE_HOST="${OPENCODE_HOST:-127.0.0.1}"
OPENCODE_PORT="${OPENCODE_PORT:-4096}"

if ! command -v opencode >/dev/null 2>&1; then
  echo "opencode CLI not found — installing…" >&2
  curl -fsSL https://opencode.ai/install | bash
  export PATH="$HOME/.opencode/bin:$HOME/bin:$PATH"
fi

echo "Starting opencode serve on ${OPENCODE_HOST}:${OPENCODE_PORT} ..."
# OPENCODE_SERVER_PASSWORD (if set in .env) protects the server with HTTP basic auth.
exec opencode serve --hostname "$OPENCODE_HOST" --port "$OPENCODE_PORT"
