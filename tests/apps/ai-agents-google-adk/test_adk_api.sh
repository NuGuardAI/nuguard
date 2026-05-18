#!/usr/bin/env bash
# test_api.sh — Exercise marketing_campaign_agent via the ADK API server
#
# Usage:
#   ./test_api.sh                                          # default task + port 8090
#   PORT=9000 ./test_api.sh                                # custom port
#   TASK="A subscription coffee service for offices" ./test_api.sh

set -euo pipefail

PORT="${PORT:-8090}"
BASE="http://127.0.0.1:${PORT}"
APP="marketing_campaign_agent"
USER="user1"
TASK="${TASK:-An eco-friendly reusable water bottle with a built-in filter}"

command -v curl >/dev/null || { echo "ERROR: curl required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "ERROR: python3 required" >&2; exit 1; }

# ── Check server is reachable (retry up to 15s) ───────────────────────────────
echo "==> Waiting for server at $BASE ..."
for i in $(seq 1 15); do
  if curl -sf --max-time 1 "$BASE/docs" >/dev/null 2>&1; then
    echo "    Server ready."
    break
  fi
  if [[ $i -eq 15 ]]; then
    echo "ERROR: ADK API server not reachable at $BASE after 15s"
    echo "  Start it first:  ./serve.sh"
    exit 1
  fi
  sleep 1
done

echo "Task : $TASK"
echo "Agent: $APP"
echo ""

# ── 1. Create session ─────────────────────────────────────────────────────────
echo "==> Creating session..."
SESSION_JSON=$(curl -s -X POST "$BASE/apps/$APP/users/$USER/sessions" \
  -H "Content-Type: application/json" \
  -d '{}')

if [[ -z "$SESSION_JSON" ]]; then
  echo "ERROR: Empty response from session endpoint. Is the server running?"
  echo "  Start it first:  ./serve.sh"
  exit 1
fi

SESSION=$(echo "$SESSION_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>&1)
if [[ $? -ne 0 ]]; then
  echo "ERROR: Failed to parse session response:"
  echo "$SESSION_JSON"
  exit 1
fi
echo "    session_id: $SESSION"
echo ""

# ── 2. Run agent ──────────────────────────────────────────────────────────────
echo "==> Running agent (this may take 30–90s for research + generation)..."
echo ""

PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({
    'app_name': '$APP',
    'user_id':  '$USER',
    'session_id': '$SESSION',
    'new_message': {
        'role': 'user',
        'parts': [{'text': '''$TASK'''}]
    }
}))
")

RESPONSE=$(curl -s -X POST "$BASE/run" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

if [[ -z "$RESPONSE" ]]; then
  echo "ERROR: Empty response from /run endpoint."
  exit 1
fi

# ── 3. Pretty-print the final agent text ─────────────────────────────────────
echo "$RESPONSE" > /tmp/adk_run_response.json

python3 << 'PYEOF'
import json, sys

with open('/tmp/adk_run_response.json') as f:
    raw = f.read()

if not raw.strip():
    print('[No response from server]')
    sys.exit(1)

try:
    data = json.loads(raw)
except json.JSONDecodeError as e:
    print(f'[JSON parse error: {e}]')
    print('Raw (first 500 chars):', raw[:500])
    sys.exit(1)

events = data if isinstance(data, list) else [data]
found = False
for event in events:
    content = event.get('content') or {}
    for part in content.get('parts') or []:
        text = part.get('text', '')
        if text:
            print(text)
            found = True

if not found:
    print('[No text output found in response]')
    print(json.dumps(data, indent=2))
PYEOF
