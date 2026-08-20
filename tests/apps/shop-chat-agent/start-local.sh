#!/usr/bin/env bash
# Start shop-chat-agent locally via Docker for NuGuard testing.
#
# shop-chat-agent has no published image — it clones ./repo on first run
# (see clone-shop-chat-agent.sh) and builds/runs it from its own Dockerfile.
#
# app/services/claude.server.js has been patched (NuGuard test-target change)
# to call Azure OpenAI instead of Anthropic Claude, behind the same Claude-
# shaped message contract — see that file for details.
#
# This is a *standalone, degraded-mode* run: no Shopify Partner app/dev store
# is connected, so the storefront/customer MCP tool calls in app/mcp-client.js
# will fail to connect and the app falls back to "continuing without tools"
# (see app/routes/chat.jsx) — the model still answers via Azure OpenAI, but
# without product search/cart/order MCP tools. See README.md for how to
# connect a real dev store instead.
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example to .env and fill in AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_KEY/AZURE_OPENAI_MODEL_NAME first." >&2
  exit 1
fi

set -o allexport
# shellcheck disable=SC1091
source .env
set +o allexport

if [[ ! -d repo/.git ]]; then
  ./clone-shop-chat-agent.sh
fi

mkdir -p logs

echo "Building shop-chat-agent image from ./repo ..."
docker build -t shop-chat-agent-nuguard-test ./repo

docker rm -f shop-chat-agent-nuguard-test >/dev/null 2>&1 || true

echo "Starting shop-chat-agent container..."
docker run -d \
  --name shop-chat-agent-nuguard-test \
  -p "127.0.0.1:${PORT:-5501}:${PORT:-5501}" \
  -e AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-}" \
  -e AZURE_OPENAI_KEY="${AZURE_OPENAI_KEY:-}" \
  -e AZURE_OPENAI_MODEL_NAME="${AZURE_OPENAI_MODEL_NAME:-}" \
  -e AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2024-06-01}" \
  -e SHOPIFY_API_KEY="${SHOPIFY_API_KEY:-YOUR_APP_CLIENT_ID}" \
  -e SHOPIFY_API_SECRET="${SHOPIFY_API_SECRET:-YOUR_APP_CLIENT_SECRET}" \
  -e SCOPES="${SCOPES:-unauthenticated_read_product_listings}" \
  -e REDIRECT_URL="${REDIRECT_URL:-https://localhost:3458/auth/callback}" \
  -e SHOPIFY_APP_URL="${SHOPIFY_APP_URL:-http://localhost:${PORT:-5501}}" \
  -e PORT="${PORT:-5501}" \
  -e NODE_ENV=production \
  -v shop-chat-agent-nuguard-prisma:/app/prisma \
  --restart unless-stopped \
  shop-chat-agent-nuguard-test

echo "Waiting for shop-chat-agent to become healthy..."
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:${PORT:-5501}/" -o /dev/null; then
    break
  fi
  sleep 2
done

echo "shop-chat-agent is running at http://localhost:${PORT:-5501}"
echo "Chat endpoint (SSE, GET or POST): http://localhost:${PORT:-5501}/chat"
echo "Note: MCP storefront/customer tools are unavailable in this standalone run"
echo "(no real Shopify store/dev-store connected) — the model replies without tools."
