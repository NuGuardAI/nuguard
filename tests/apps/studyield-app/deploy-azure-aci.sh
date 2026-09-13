#!/usr/bin/env bash
# Deploy Studyield to Azure Container Instances (single multi-container group)
# for NuGuard testing.
#
# Studyield has no published image, so this script:
#   1. Builds the backend/frontend images from ./repo via `az acr build` (ACR
#      builds in the cloud — no local Docker build required).
#   2. Deploys one ACI container group with backend + frontend + postgres +
#      redis + qdrant + clickhouse, all sharing one network namespace (they
#      reach each other via localhost, same as docker-compose's service DNS
#      but flattened — see the generated container group YAML below).
#
# Prerequisites: az CLI logged in (az login), .env populated, ./repo cloned
# (run ./clone-studyield.sh first, or this script will do it for you).
#
# WARNING: this exposes the frontend/backend on a public FQDN with the
# credentials/test data you configured. Use only for short-lived testing with
# synthetic canary data (see canary.json) and tear down as soon as done.
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
fi

: "${JWT_ACCESS_SECRET:?Set JWT_ACCESS_SECRET in .env first}"
: "${JWT_REFRESH_SECRET:?Set JWT_REFRESH_SECRET in .env first}"

if [[ ! -d repo/.git ]]; then
  ./clone-studyield.sh
fi

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-nuguard-studyield-test}"
LOCATION="${AZURE_LOCATION:-eastus}"
ACR_NAME="${ACR_NAME:-nuguardstudyieldacr$(openssl rand -hex 3)}"
CONTAINER_GROUP="${ACI_CONTAINER_NAME:-studyield-nuguard-test}"
DNS_LABEL="${ACI_DNS_LABEL:-studyield-nuguard-$(openssl rand -hex 4)}"
# Postgres data persistence — an emptyDir volume (local host disk, real POSIX
# semantics) mounted into the postgres container so account/session data
# survives individual container restarts (previously postgres had no volume
# at all: any crash/restart under load silently wiped every seeded account,
# which is what caused redteam scans to fail partway through with "User not
# found" once real concurrent traffic hit the target). NOT an Azure Files
# share: Postgres's data directory requires real Unix ownership, which
# Azure Files (SMB/CIFS) cannot provide — confirmed live, postgres
# crash-looped with "FATAL: data directory has wrong ownership" every time.
POSTGRES_DB="${POSTGRES_DB:-studyield_dev}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
# Frontend owns port 80 — the browsable demo URL for this app. Backend stays
# on its own dedicated port (still publicly reachable, just not the default
# HTTP port) since NuGuard's behavior/redteam tooling runs externally and
# needs a reachable API endpoint; nginx's own "listen 80" needs no override
# here since frontend keeps port 80.
BACKEND_PORT="${BACKEND_PORT:-3010}"
FRONTEND_PORT="${FRONTEND_PORT:-80}"

echo "Creating resource group $RESOURCE_GROUP in $LOCATION (idempotent)..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "Creating Azure Container Registry $ACR_NAME (idempotent)..."
az acr create --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" \
  --sku Basic --admin-enabled true --output none 2>/dev/null || true

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' -o tsv)

# Vite inlines VITE_* vars at build time, so the frontend must be built with
# the public FQDN baked in — passing it as a container-runtime env var later
# has no effect on the already-built bundle (this previously caused the
# frontend to fall back to its localhost default, which the browser then
# blocks as a private-network request from a public/insecure origin).
FQDN="${DNS_LABEL}.${LOCATION}.azurecontainer.io"
BACKEND_URL="http://${FQDN}:${BACKEND_PORT}/api/v1"

echo "Building frontend image via ACR build (VITE_API_URL=$BACKEND_URL)..."
az acr build --registry "$ACR_NAME" --image "studyield-frontend:latest" \
  --build-arg "VITE_API_URL=$BACKEND_URL" repo/frontend

# Seeder sidecar: bakes tests/apps/studyield-app/seed-users.js into the
# backend image (scripts/ is already copied into the final stage — see
# repo/backend/Dockerfile) so it can register student-alpha/beta/gamma
# against the backend over localhost on every boot, idempotently.
cp seed-users.js repo/backend/scripts/seed-users.js
echo "Rebuilding backend image with seed-users.js baked in..."
az acr build --registry "$ACR_NAME" --image "studyield-backend:latest" repo/backend
rm -f repo/backend/scripts/seed-users.js

TEMPLATE_FILE=$(mktemp /tmp/studyield-aci-XXXXXX.yaml)
trap 'rm -f "$TEMPLATE_FILE"' EXIT

# Optional Docker Hub credentials (set DOCKERHUB_USERNAME/DOCKERHUB_PASSWORD in
# .env) — postgres/redis/qdrant/clickhouse are pulled straight from Docker Hub
# on every deploy, and anonymous pulls get rate-limited (429) surprisingly
# easily. Authenticated pulls avoid that.
DOCKERHUB_CREDS=""
if [[ -n "${DOCKERHUB_USERNAME:-}" && -n "${DOCKERHUB_PASSWORD:-}" ]]; then
  DOCKERHUB_CREDS="    - server: index.docker.io
      username: $DOCKERHUB_USERNAME
      password: '$DOCKERHUB_PASSWORD'
"
fi

cat > "$TEMPLATE_FILE" <<EOF
apiVersion: '2021-09-01'
location: $LOCATION
name: $CONTAINER_GROUP
properties:
  osType: Linux
  restartPolicy: OnFailure
  imageRegistryCredentials:
${DOCKERHUB_CREDS}    - server: $ACR_LOGIN_SERVER
      username: $ACR_USERNAME
      password: $ACR_PASSWORD
  ipAddress:
    type: Public
    dnsNameLabel: $DNS_LABEL
    ports:
      - protocol: tcp
        port: $BACKEND_PORT
      - protocol: tcp
        port: $FRONTEND_PORT
  containers:
    - name: postgres
      properties:
        image: postgres:15-alpine
        environmentVariables:
          - {name: POSTGRES_USER, value: '$POSTGRES_USER'}
          - {name: POSTGRES_PASSWORD, secureValue: '$POSTGRES_PASSWORD'}
          - {name: POSTGRES_DB, value: '$POSTGRES_DB'}
          - {name: PGDATA, value: '/mnt/postgres-data/pgdata'}
        volumeMounts:
          - {name: postgres-data-volume, mountPath: /mnt/postgres-data}
        resources:
          # Bumped from 1.5/1.5 — multiple concurrent NuGuard sessions (behavior +
          # redteam, each holding several open connections) need headroom for
          # postgres's connection pool, not just a single interactive user.
          requests: {cpu: 2, memoryInGb: 2}
    - name: redis
      properties:
        image: redis:7-alpine
        command: ["redis-server", "--appendonly", "yes"]
        resources:
          requests: {cpu: 1, memoryInGb: 0.5}
    - name: qdrant
      properties:
        image: qdrant/qdrant:latest
        resources:
          requests: {cpu: 1, memoryInGb: 1}
    - name: clickhouse
      properties:
        image: clickhouse/clickhouse-server:latest
        environmentVariables:
          - {name: CLICKHOUSE_DB, value: '${CLICKHOUSE_DATABASE:-studyield_analytics}'}
          - {name: CLICKHOUSE_USER, value: '${CLICKHOUSE_USER:-default}'}
        resources:
          requests: {cpu: 1, memoryInGb: 1.5}
    - name: backend
      properties:
        image: $ACR_LOGIN_SERVER/studyield-backend:latest
        ports:
          - port: $BACKEND_PORT
        environmentVariables:
          - {name: NODE_ENV, value: 'production'}
          - {name: PORT, value: '$BACKEND_PORT'}
          - {name: DATABASE_HOST, value: 'localhost'}
          - {name: DATABASE_PORT, value: '5432'}
          - {name: DATABASE_USER, value: '$POSTGRES_USER'}
          - {name: DATABASE_PASSWORD, secureValue: '$POSTGRES_PASSWORD'}
          - {name: DATABASE_NAME, value: '$POSTGRES_DB'}
          - {name: REDIS_HOST, value: 'localhost'}
          - {name: REDIS_PORT, value: '6379'}
          - {name: QDRANT_HOST, value: 'localhost'}
          - {name: QDRANT_PORT, value: '6333'}
          - {name: CLICKHOUSE_HOST, value: 'localhost'}
          - {name: CLICKHOUSE_PORT, value: '8123'}
          - {name: JWT_ACCESS_SECRET, secureValue: '$JWT_ACCESS_SECRET'}
          - {name: JWT_REFRESH_SECRET, secureValue: '$JWT_REFRESH_SECRET'}
          - {name: OPENROUTER_API_KEY, secureValue: '${OPENROUTER_API_KEY:-}'}
          - {name: OPENAI_API_KEY, secureValue: '${OPENAI_API_KEY:-}'}
          - {name: AZURE_OPENAI_ENDPOINT, value: '${AZURE_OPENAI_ENDPOINT:-}'}
          - {name: AZURE_OPENAI_KEY, secureValue: '${AZURE_OPENAI_KEY:-}'}
          - {name: AZURE_OPENAI_DEPLOYMENT_NAME, value: '${AZURE_OPENAI_DEPLOYMENT_NAME:-}'}
          - {name: AZURE_OPENAI_API_VERSION, value: '${AZURE_OPENAI_API_VERSION:-2025-01-01-preview}'}
          - {name: AZURE_EMBEDDING_ENDPOINT, value: '${AZURE_EMBEDDING_ENDPOINT:-}'}
          - {name: AZURE_EMBEDDING_KEY, secureValue: '${AZURE_EMBEDDING_KEY:-}'}
          - {name: AZURE_EMBEDDING_DEPLOYMENT_NAME, value: '${AZURE_EMBEDDING_DEPLOYMENT_NAME:-}'}
        resources:
          # Bumped from 1 cpu / 1GB — this container already crashed once
          # (exitCode 1) at initial boot under the original allocation, and
          # is the one under the most real load during a redteam scan
          # (concurrency=5 scenario workers all hitting it concurrently).
          # Bumped again 2/2.5 -> 3/4: multiple *simultaneous* nuguard runs
          # (behavior + redteam, or overlapping sessions from different
          # branches/CI jobs) stack their concurrent workers on top of each
          # other against this one backend, not just one run's concurrency.
          requests: {cpu: 3, memoryInGb: 4}
    - name: frontend
      properties:
        image: $ACR_LOGIN_SERVER/studyield-frontend:latest
        ports:
          - port: $FRONTEND_PORT
        resources:
          requests: {cpu: 1, memoryInGb: 0.5}
    - name: seeder
      properties:
        image: $ACR_LOGIN_SERVER/studyield-backend:latest
        command: ["sh", "-c", "sleep 20 && node scripts/seed-users.js"]
        environmentVariables:
          - {name: SEED_BASE_URL, value: 'http://localhost:$BACKEND_PORT/api/v1'}
        resources:
          requests: {cpu: 1, memoryInGb: 0.3}
  volumes:
    # emptyDir, NOT azureFile: Postgres's data directory needs real POSIX
    # ownership (initdb/the server both refuse to start otherwise -- "data
    # directory has wrong ownership"), which Azure Files (an SMB/CIFS share)
    # cannot provide regardless of chmod/chown, and ACI's azureFile volume
    # type has no uid/gid/file_mode mount-option override to work around it
    # (confirmed live: postgres crash-looped with that exact FATAL on this
    # deployment). emptyDir is backed by the container group's local host
    # disk instead, so it has full POSIX semantics and -- critically -- still
    # solves the actual bug (postgres/backend restarting under load silently
    # wiping seeded accounts): it survives individual container
    # crash/restart for the life of the container group. It does NOT survive
    # a full az-container-delete + recreate (a fresh CPU/memory resize
    # always needs one, since ACI can't resize in place) -- re-run
    # ./seed-data.sh after any such redeploy.
    - name: postgres-data-volume
      emptyDir: {}
tags: {}
type: Microsoft.ContainerInstance/containerGroups
EOF

echo "Deploying container group $CONTAINER_GROUP (postgres+redis+qdrant+clickhouse+backend+frontend)..."
az container create --resource-group "$RESOURCE_GROUP" --file "$TEMPLATE_FILE"

FQDN=$(az container show --resource-group "$RESOURCE_GROUP" --name "$CONTAINER_GROUP" \
  --query ipAddress.fqdn -o tsv)

echo "---"
echo "Studyield deployed:"
echo "  Frontend: http://${FQDN}:${FRONTEND_PORT}"
echo "  Backend API: http://${FQDN}:${BACKEND_PORT}"
echo "Update tests/apps/studyield-app/nuguard-azure.yaml target.url / redteam.target with"
echo "the backend URL above."
echo "student-alpha/beta/gamma are seeded automatically by the 'seeder' container"
echo "(see seed-users.js) — set APP_USERNAME/APP_PASSWORD in .env to"
echo "alpha.seed@example-student.test / SeedP@ssw0rd1 for login_flow auth."
echo "---"
echo "To tear down: az container delete --resource-group $RESOURCE_GROUP --name $CONTAINER_GROUP --yes"
echo "              az group delete --name $RESOURCE_GROUP --yes --no-wait"
