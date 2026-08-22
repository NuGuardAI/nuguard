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
# 80 is the externally exposed ACI port; the local docker-compose target
# stays on 5000 (docker-compose.yml is unaffected by this default). Phlox
# itself always listens on 5000 internally — its process runs as a non-root
# user and cannot bind port 80 directly (fails with exit code 3) — so a
# socat sidecar in the same container group forwards 80 -> localhost:5000.
PORT=80
INTERNAL_PORT=5000

echo "Creating resource group $RESOURCE_GROUP in $LOCATION (idempotent)..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

MANIFEST=$(mktemp)
trap 'rm -f "$MANIFEST"' EXIT
cat > "$MANIFEST" <<EOF
apiVersion: '2021-10-01'
location: ${LOCATION}
name: ${CONTAINER_NAME}
properties:
  osType: Linux
  restartPolicy: OnFailure
  ipAddress:
    type: Public
    dnsNameLabel: ${DNS_LABEL}
    ports:
    - protocol: tcp
      port: ${PORT}
  containers:
  - name: phlox
    properties:
      image: ghcr.io/bloodworks-io/phlox:latest
      ports:
      - port: ${INTERNAL_PORT}
      environmentVariables:
      - name: ALLOWED_ORIGINS
        value: "${ALLOWED_ORIGINS:-*}"
      - name: PORT
        value: "${INTERNAL_PORT}"
      - name: SERVER_HOST
        value: "0.0.0.0"
      - name: DB_ENCRYPTION_KEY
        secureValue: "${DB_ENCRYPTION_KEY}"
      resources:
        requests:
          cpu: 1
          memoryInGb: 2
  - name: proxy
    properties:
      image: alpine/socat
      command: ["socat", "TCP-LISTEN:${PORT},fork,reuseaddr", "TCP:127.0.0.1:${INTERNAL_PORT}"]
      ports:
      - port: ${PORT}
      resources:
        requests:
          cpu: 0.5
          memoryInGb: 0.5
type: Microsoft.ContainerInstance/containerGroups
EOF

echo "Deploying container instance $CONTAINER_NAME..."
az container create --resource-group "$RESOURCE_GROUP" --file "$MANIFEST" --output none

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
