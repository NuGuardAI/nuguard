#!/usr/bin/env bash
set -euo pipefail

# Single-command Azure deployment for Healthcare Voice Agent.
# Usage:
#   OPENAI_API_KEY=... GEMINI_API_KEY=... ./deploy_azure.sh
# Optional overrides:
#   RG_NAME, LOCATION, ACR_NAME, APP_NAME, ACA_ENV_NAME, LAW_NAME,
#   PG_SERVER_NAME, PG_DB_NAME, PG_ADMIN_USER, PG_ADMIN_PASSWORD,
#   APP_IMAGE_TAG, APP_CPU, APP_MEMORY

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_cmd az
require_cmd curl

if ! az account show >/dev/null 2>&1; then
  echo "Azure CLI is not logged in. Run: az login" >&2
  exit 1
fi

RG_NAME="${RG_NAME:-Healthcare-agent}"
LOCATION="${LOCATION:-centralus}"
APP_NAME="${APP_NAME:-hcagentcu}"
ACA_ENV_NAME="${ACA_ENV_NAME:-healthcare-agent-env-centralus}"
LAW_NAME="${LAW_NAME:-healthcare-agent-law-centralus}"
PG_DB_NAME="${PG_DB_NAME:-healthcare}"
PG_ADMIN_USER="${PG_ADMIN_USER:-pgadmin}"
APP_IMAGE_TAG="${APP_IMAGE_TAG:-latest}"
APP_CPU="${APP_CPU:-1.0}"
APP_MEMORY="${APP_MEMORY:-2.0Gi}"

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required." >&2
  exit 1
fi
if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "GEMINI_API_KEY is required." >&2
  exit 1
fi

# Build arg for frontend compile-time key (falls back to GEMINI_API_KEY)
VITE_GEMINI_API_KEY="${VITE_GEMINI_API_KEY:-$GEMINI_API_KEY}"

if [[ -z "${PG_ADMIN_PASSWORD:-}" ]]; then
  PG_ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 20)Aa1!"
fi

if [[ -z "${ACR_NAME:-}" ]]; then
  # ACR names must be globally unique, 5-50 chars, alphanumeric only.
  ACR_NAME="hcagent$(date +%m%d%H%M)$((RANDOM%1000))"
fi
ACR_NAME="$(echo "$ACR_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"

if [[ -z "${PG_SERVER_NAME:-}" ]]; then
  # Flexible server names must be globally unique.
  PG_SERVER_NAME="healthcare-pg-cu-$(date +%m%d%H%M)$((RANDOM%1000))"
fi
PG_SERVER_NAME="$(echo "$PG_SERVER_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-')"

echo "[1/9] Creating resource group..."
az group create -n "$RG_NAME" -l "$LOCATION" -o none

echo "[2/9] Ensuring Azure providers/extensions..."
az extension add --name containerapp --upgrade -y >/dev/null
az provider register --namespace Microsoft.App >/dev/null
az provider register --namespace Microsoft.OperationalInsights >/dev/null
az provider register --namespace Microsoft.DBforPostgreSQL >/dev/null

echo "[3/9] Ensuring ACR..."
if ! az acr show -g "$RG_NAME" -n "$ACR_NAME" >/dev/null 2>&1; then
  az acr create -g "$RG_NAME" -n "$ACR_NAME" --sku Basic --admin-enabled true -l "$LOCATION" -o none
fi
ACR_LOGIN_SERVER="$(az acr show -g "$RG_NAME" -n "$ACR_NAME" --query loginServer -o tsv)"
APP_IMAGE="${ACR_LOGIN_SERVER}/healthcare-agent:${APP_IMAGE_TAG}"

echo "[4/9] Building and pushing app image..."
az acr build -r "$ACR_NAME" -t "healthcare-agent:${APP_IMAGE_TAG}" \
  --build-arg VITE_GEMINI_API_KEY="$VITE_GEMINI_API_KEY" . -o none

echo "[5/9] Ensuring PostgreSQL Flexible Server..."
if ! az postgres flexible-server show -g "$RG_NAME" -n "$PG_SERVER_NAME" >/dev/null 2>&1; then
  az postgres flexible-server create \
    -g "$RG_NAME" -n "$PG_SERVER_NAME" -l "$LOCATION" \
    --admin-user "$PG_ADMIN_USER" --admin-password "$PG_ADMIN_PASSWORD" \
    --sku-name Standard_B1ms --tier Burstable --version 15 \
    --public-access all --storage-size 32 -o none
else
  az postgres flexible-server update \
    -g "$RG_NAME" -n "$PG_SERVER_NAME" \
    --admin-password "$PG_ADMIN_PASSWORD" -o none
fi

PG_STATE="$(az postgres flexible-server show -g "$RG_NAME" -n "$PG_SERVER_NAME" --query state -o tsv)"
if [[ "$PG_STATE" != "Ready" ]]; then
  echo "Waiting for PostgreSQL server to be Ready..."
  for _ in $(seq 1 60); do
    sleep 10
    PG_STATE="$(az postgres flexible-server show -g "$RG_NAME" -n "$PG_SERVER_NAME" --query state -o tsv)"
    [[ "$PG_STATE" == "Ready" ]] && break
  done
fi
if [[ "$PG_STATE" != "Ready" ]]; then
  echo "PostgreSQL server did not become Ready." >&2
  exit 1
fi

if ! az postgres flexible-server db show -g "$RG_NAME" -s "$PG_SERVER_NAME" -d "$PG_DB_NAME" >/dev/null 2>&1; then
  az postgres flexible-server db create -g "$RG_NAME" -s "$PG_SERVER_NAME" -d "$PG_DB_NAME" -o none
fi

echo "[6/9] Initializing database schema/functions..."
if ! command -v psql >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update >/dev/null
    sudo apt-get install -y postgresql-client >/dev/null
  else
    echo "psql not found and apt-get is unavailable. Install PostgreSQL client and rerun." >&2
    exit 1
  fi
fi

PG_HOST="${PG_SERVER_NAME}.postgres.database.azure.com"
export PGPASSWORD="$PG_ADMIN_PASSWORD"

psql "host=$PG_HOST port=5432 dbname=$PG_DB_NAME user=$PG_ADMIN_USER sslmode=require" -f sql/schema.sql >/dev/null
for f in sql/functions/*.sql; do
  psql "host=$PG_HOST port=5432 dbname=$PG_DB_NAME user=$PG_ADMIN_USER sslmode=require" -f "$f" >/dev/null
done
psql "host=$PG_HOST port=5432 dbname=$PG_DB_NAME user=$PG_ADMIN_USER sslmode=require" -f sql/populate_more_specialists.sql >/dev/null

echo "[7/9] Ensuring Log Analytics + Container Apps environment..."
if ! az monitor log-analytics workspace show -g "$RG_NAME" -n "$LAW_NAME" >/dev/null 2>&1; then
  az monitor log-analytics workspace create -g "$RG_NAME" -n "$LAW_NAME" -l "$LOCATION" -o none
fi

if ! az containerapp env show -g "$RG_NAME" -n "$ACA_ENV_NAME" >/dev/null 2>&1; then
  LAW_ID="$(az monitor log-analytics workspace show -g "$RG_NAME" -n "$LAW_NAME" --query customerId -o tsv)"
  LAW_KEY="$(az monitor log-analytics workspace get-shared-keys -g "$RG_NAME" -n "$LAW_NAME" --query primarySharedKey -o tsv)"
  az containerapp env create -g "$RG_NAME" -n "$ACA_ENV_NAME" -l "$LOCATION" \
    --logs-workspace-id "$LAW_ID" --logs-workspace-key "$LAW_KEY" -o none
fi

ACR_USER="$(az acr credential show -n "$ACR_NAME" --query username -o tsv)"
ACR_PASS="$(az acr credential show -n "$ACR_NAME" --query passwords[0].value -o tsv)"

echo "[8/9] Deploying/updating container app..."
if az containerapp show -g "$RG_NAME" -n "$APP_NAME" >/dev/null 2>&1; then
  az containerapp secret set -g "$RG_NAME" -n "$APP_NAME" \
    --secrets \
      openai-key="$OPENAI_API_KEY" \
      gemini-key="$GEMINI_API_KEY" \
      pg-password="$PG_ADMIN_PASSWORD" -o none

  az containerapp update -g "$RG_NAME" -n "$APP_NAME" \
    --image "$APP_IMAGE" \
    --set-env-vars \
      OPENAI_API_KEY=secretref:openai-key \
      GEMINI_API_KEY=secretref:gemini-key \
      VITE_GEMINI_API_KEY=secretref:gemini-key \
      DB_HOST="$PG_HOST" \
      DB_PORT=5432 \
      DB_USER="$PG_ADMIN_USER" \
      DB_PASSWORD=secretref:pg-password \
      DB_NAME="$PG_DB_NAME" \
      DB_SSLMODE=require \
      FRONTEND_ORIGIN='*' -o none
else
  az containerapp create -g "$RG_NAME" -n "$APP_NAME" --environment "$ACA_ENV_NAME" \
    --image "$APP_IMAGE" \
    --ingress external --target-port 8080 \
    --registry-server "$ACR_LOGIN_SERVER" --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
    --secrets \
      openai-key="$OPENAI_API_KEY" \
      gemini-key="$GEMINI_API_KEY" \
      pg-password="$PG_ADMIN_PASSWORD" \
    --env-vars \
      OPENAI_API_KEY=secretref:openai-key \
      GEMINI_API_KEY=secretref:gemini-key \
      VITE_GEMINI_API_KEY=secretref:gemini-key \
      DB_HOST="$PG_HOST" \
      DB_PORT=5432 \
      DB_USER="$PG_ADMIN_USER" \
      DB_PASSWORD=secretref:pg-password \
      DB_NAME="$PG_DB_NAME" \
      DB_SSLMODE=require \
      FRONTEND_ORIGIN='*' \
    --cpu "$APP_CPU" --memory "$APP_MEMORY" -o none
fi

APP_FQDN="$(az containerapp show -g "$RG_NAME" -n "$APP_NAME" --query properties.configuration.ingress.fqdn -o tsv)"
APP_URL="https://${APP_FQDN}"
az containerapp update -g "$RG_NAME" -n "$APP_NAME" --set-env-vars FRONTEND_ORIGIN="$APP_URL" -o none

echo "[9/9] Verifying deployment..."
HEALTH=''
for _ in $(seq 1 40); do
  HEALTH="$(curl -sS --max-time 15 "$APP_URL/api/health" || true)"
  if echo "$HEALTH" | grep -q '"status"'; then
    break
  fi
  sleep 4
done

echo ""
echo "Deployment complete"
echo "Resource Group : $RG_NAME"
echo "Location       : $LOCATION"
echo "Container App  : $APP_NAME"
echo "App URL        : $APP_URL"
echo "Postgres Host  : $PG_HOST"
echo "Health         : $HEALTH"
echo ""
echo "If you need to redeploy code only:"
echo "  APP_NAME=$APP_NAME ACR_NAME=$ACR_NAME PG_SERVER_NAME=$PG_SERVER_NAME OPENAI_API_KEY=... GEMINI_API_KEY=... ./deploy_azure.sh"
