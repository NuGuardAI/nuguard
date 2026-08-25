#!/usr/bin/env bash
# Deploy shop-chat-agent to a single Azure Container Instance for NuGuard testing.
#
# shop-chat-agent has no published image, so this script builds it from
# ./repo via `az acr build` (cloud build, no local Docker required) and
# deploys a single ACI container.
#
# Prerequisites: az CLI logged in (az login), .env populated, ./repo cloned
# (run ./clone-shop-chat-agent.sh first, or this script will do it for you).
#
# WARNING: this is a standalone/degraded run (no real Shopify dev store) with
# a public FQDN:port and no auth in front of it. Use only for short-lived
# testing and tear down (last command below) as soon as done.
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
fi

if [[ ! -d repo/.git ]]; then
  ./clone-shop-chat-agent.sh
fi

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-nuguard-shop-chat-agent-test}"
LOCATION="${AZURE_LOCATION:-eastus}"
ACR_NAME="${ACR_NAME:-nuguardshopchatacr$(openssl rand -hex 3)}"
CONTAINER_NAME="${ACI_CONTAINER_NAME:-shop-chat-agent-nuguard-test}"
DNS_LABEL="${ACI_DNS_LABEL:-shop-chat-agent-nuguard-$(openssl rand -hex 4)}"
# 80 here is both the internal listen port and the public ACI port — the
# app's Dockerfile has no USER directive (runs as root), so it can bind 80
# directly, unlike phlox (see tests/apps/phlox-app/deploy-azure-aci.sh).
PORT=80
PUBLIC_URL="http://${DNS_LABEL}.${LOCATION}.azurecontainer.io:${PORT}"

echo "Creating resource group $RESOURCE_GROUP in $LOCATION (idempotent)..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "Creating Azure Container Registry $ACR_NAME (idempotent)..."
az acr create --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" \
  --sku Basic --admin-enabled true --output none 2>/dev/null || true

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' -o tsv)

echo "Building shop-chat-agent image via ACR build (cloud build, no local Docker needed)..."
cp seed-sessions.sql repo/seed-sessions.sql
az acr build --registry "$ACR_NAME" --image "shop-chat-agent:latest" repo
rm -f repo/seed-sessions.sql

# Re-apply the (idempotent) seed on every boot so a restart/redeploy always
# converges back to the same known canary state. dev.sqlite intentionally
# stays on the container's local ephemeral disk, NOT an Azure Files volume —
# SQLite needs real byte-range file locking, which Azure Files (SMB) does not
# reliably provide; mounting the DB there causes exit-code-1 crash loops
# within seconds of "prisma migrate deploy" / server startup touching the
# file. Losing in-run chat/conversation history on restart is an acceptable
# trade-off for a test target; the canary Session profiles are what matter
# and those are always freshly reseeded.
STARTUP_CMD='sh -c '\''npm run setup && ( cat seed-sessions.sql | npx prisma db execute --schema=./prisma/schema.prisma --stdin || true ) && npm run start'\'''

echo "Deploying container instance $CONTAINER_NAME..."
az container create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_NAME" \
  --image "$ACR_LOGIN_SERVER/shop-chat-agent:latest" \
  --registry-login-server "$ACR_LOGIN_SERVER" \
  --registry-username "$ACR_USERNAME" \
  --registry-password "$ACR_PASSWORD" \
  --cpu 1 \
  --memory 1.5 \
  --os-type Linux \
  --ports "$PORT" \
  --dns-name-label "$DNS_LABEL" \
  --command-line "$STARTUP_CMD" \
  --environment-variables \
    AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-}" \
    AZURE_OPENAI_MODEL_NAME="${AZURE_OPENAI_MODEL_NAME:-}" \
    AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2024-06-01}" \
    SHOPIFY_API_KEY="${SHOPIFY_API_KEY:-YOUR_APP_CLIENT_ID}" \
    SCOPES="${SCOPES:-unauthenticated_read_product_listings}" \
    REDIRECT_URL="${REDIRECT_URL:-https://localhost:3458/auth/callback}" \
    SHOPIFY_APP_URL="$PUBLIC_URL" \
    PORT="$PORT" \
    NODE_ENV=production \
  --secure-environment-variables \
    AZURE_OPENAI_KEY="${AZURE_OPENAI_KEY:-}" \
    SHOPIFY_API_SECRET="${SHOPIFY_API_SECRET:-YOUR_APP_CLIENT_SECRET}" \
  --restart-policy OnFailure

FQDN=$(az container show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_NAME" \
  --query ipAddress.fqdn -o tsv)

echo "---"
echo "shop-chat-agent deployed: http://${FQDN}:${PORT}"
echo "Chat endpoint (SSE, GET or POST): http://${FQDN}:${PORT}/chat"
echo "Update tests/apps/shop-chat-agent/nuguard.yaml target.url / redteam.target with this URL."
echo "Note: standalone run — no real Shopify dev store connected, MCP tools unavailable (see README.md)."
echo "---"
echo "To tear down: az container delete --resource-group $RESOURCE_GROUP --name $CONTAINER_NAME --yes"
echo "              az group delete --name $RESOURCE_GROUP --yes --no-wait"
