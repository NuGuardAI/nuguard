#!/usr/bin/env bash
# Deploy Phlox to a single Azure Container Instance for NuGuard testing.
#
# Prerequisites: az CLI logged in (az login), .env populated.
#
# WARNING: Phlox ships with no built-in authentication by default. This opens
# a public FQDN:port with no auth in front of it. Use only for short-lived
# testing with synthetic canary data (see canary.json) and tear down the
# instance (last command below) as soon as testing is done.
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +o allexport
fi

: "${DB_ENCRYPTION_KEY:?Set DB_ENCRYPTION_KEY in .env first}"

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-nuguard-phlox-test}"
LOCATION="${AZURE_LOCATION:-eastus}"
CONTAINER_NAME="${ACI_CONTAINER_NAME:-phlox-nuguard-test}"
DNS_LABEL="${ACI_DNS_LABEL:-phlox-nuguard-$(openssl rand -hex 4)}"
PORT="${PORT:-5000}"

echo "Creating resource group $RESOURCE_GROUP in $LOCATION (idempotent)..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "Deploying container instance $CONTAINER_NAME..."
az container create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_NAME" \
  --image ghcr.io/bloodworks-io/phlox:latest \
  --cpu 1 \
  --memory 2 \
  --os-type Linux \
  --ports "$PORT" \
  --dns-name-label "$DNS_LABEL" \
  --environment-variables \
    ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-*}" \
    PORT="$PORT" \
    SERVER_HOST=0.0.0.0 \
  --secure-environment-variables \
    DB_ENCRYPTION_KEY="$DB_ENCRYPTION_KEY" \
  --restart-policy OnFailure

FQDN=$(az container show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_NAME" \
  --query ipAddress.fqdn -o tsv)

echo "---"
echo "Phlox deployed: http://${FQDN}:${PORT}"
echo "Update tests/apps/phlox-app/nuguard-azure.yaml target.url / redteam.target with this URL."
echo "Complete first-run Settings -> Model Settings configuration in the UI before testing."
echo "---"
echo "To tear down: az container delete --resource-group $RESOURCE_GROUP --name $CONTAINER_NAME --yes"
echo "              az group delete --name $RESOURCE_GROUP --yes --no-wait"
