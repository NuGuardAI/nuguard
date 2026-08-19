#!/usr/bin/env bash
# Start shop-chat-agent locally via Docker for NuGuard testing.
#
# shop-chat-agent has no published image — it clones ./repo on first run
# (see clone-shop-chat-agent.sh) and builds/runs it from its own Dockerfile.
#
# This is a *standalone, degraded-mode* run: no Shopify Partner app/dev store
# is connected, so the storefront/customer MCP tool calls in app/mcp-client.js
# will fail to connect and the app falls back to "continuing without tools"
# (see app/routes/chat.jsx) — Claude still answers via CLAUDE_API_KEY, but
# without product search/cart/order MCP tools. See README.md for how to
# connect a real dev store instead.
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example to .env and fill in CLAUDE_API_KEY first." >&2
  exit 1
fi

set -o allexport
# shellcheck disable=SC1091
source .env
set +o allexport

if [[ ! -d repo/.git ]]; then
  ./clone-shop-chat-agent.sh
fi

mkdir -p data logs

echo "Building shop-chat-agent image from ./repo ..."
docker build -t shop-chat-agent-nuguard-test ./repo

docker rm -f shop-chat-agent-nuguard-test >/dev/null 2>&1 || true

echo "Starting shop-chat-agent container..."
docker run -d \
  --name shop-chat-agent-nuguard-test \
  -p "127.0.0.1:${PORT:-3000}:${PORT:-3000}" \
  -e CLAUDE_API_KEY="${CLAUDE_API_KEY:-}" \
  -e SHOPIFY_API_KEY="${SHOPIFY_API_KEY:-YOUR_APP_CLIENT_ID}" \
  -e SHOPIFY_API_SECRET="${SHOPIFY_API_SECRET:-YOUR_APP_CLIENT_SECRET}" \
  -e SCOPES="${SCOPES:-unauthenticated_read_product_listings}" \
  -e REDIRECT_URL="${REDIRECT_URL:-https://localhost:3458/auth/callback}" \
  -e PORT="${PORT:-3000}" \
  -e NODE_ENV=production \
  -v "$ROOT_DIR/data:/app/prisma" \
  --restart unless-stopped \
  shop-chat-agent-nuguard-test

echo "Waiting for shop-chat-agent to become healthy..."
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:${PORT:-3000}/" -o /dev/null; then
    break
  fi
  sleep 2
done

echo "shop-chat-agent is running at http://localhost:${PORT:-3000}"
echo "Chat endpoint (SSE, GET or POST): http://localhost:${PORT:-3000}/chat"
echo "Note: MCP storefront/customer tools are unavailable in this standalone run"
echo "(no real Shopify store/dev-store connected) — Claude replies without tools."
