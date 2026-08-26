#!/usr/bin/env bash
# Deploy Studyield to Google Cloud Run for NuGuard testing.
#
# Studyield has no published image, so this script:
#   1. Builds the backend/frontend images from ./repo via Cloud Build and
#      pushes them to Artifact Registry.
#   2. Deploys the backend as one Cloud Run service with postgres/redis/qdrant/
#      clickhouse as *sidecar containers* (Cloud Run multi-container revisions
#      — all containers in a revision share localhost, so the backend reaches
#      them the same way it reaches docker-compose service names). Only the
#      backend container receives ingress traffic.
#   3. Deploys the frontend as a second, plain Cloud Run service pointing at
#      the backend service's URL.
#
# NOTE: Cloud Run container storage is ephemeral (emptyDir) — postgres/qdrant/
# clickhouse data resets whenever the revision restarts or scales to zero.
# --min-instances=1 keeps one warm instance alive for the duration of a test
# run; data is still lost on redeploy. For anything beyond short-lived testing,
# point DATABASE_HOST/QDRANT_HOST/etc. at managed services (Cloud SQL,
# Memorystore, a hosted Qdrant/ClickHouse Cloud instance) instead.
#
# Prerequisites: gcloud CLI logged in (gcloud init), .env populated, a GCP
# project with Cloud Build + Cloud Run + Artifact Registry APIs enabled,
# ./repo cloned (run ./clone-studyield.sh first, or this script will do it).
#
# WARNING: --allow-unauthenticated exposes both services publicly. Use only
# for short-lived testing with synthetic canary data and tear down (last
# commands below) when done.
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
: "${GCP_PROJECT:?Set GCP_PROJECT (your GCP project ID) in .env or the environment}"

if [[ ! -d repo/.git ]]; then
  ./clone-studyield.sh
fi

REGION="${GCP_REGION:-us-central1}"
BACKEND_SERVICE="${CLOUD_RUN_BACKEND_SERVICE:-studyield-backend-nuguard-test}"
FRONTEND_SERVICE="${CLOUD_RUN_FRONTEND_SERVICE:-studyield-frontend-nuguard-test}"
REPO_NAME="${ARTIFACT_REPO:-studyield-nuguard}"
POSTGRES_DB="${POSTGRES_DB:-studyield_dev}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
BACKEND_PORT="${BACKEND_PORT:-3010}"

echo "Ensuring Artifact Registry repo $REPO_NAME exists in $REGION (idempotent)..."
gcloud artifacts repositories create "$REPO_NAME" \
  --project "$GCP_PROJECT" --location "$REGION" --repository-format=docker \
  --output=none 2>/dev/null || true

IMAGE_BASE="${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO_NAME}"

echo "Building + pushing backend image via Cloud Build..."
gcloud builds submit repo/backend \
  --project "$GCP_PROJECT" --tag "${IMAGE_BASE}/backend:latest"

echo "Building + pushing frontend image via Cloud Build..."
gcloud builds submit repo/frontend \
  --project "$GCP_PROJECT" --tag "${IMAGE_BASE}/frontend:latest"

SERVICE_YAML=$(mktemp /tmp/studyield-cloudrun-XXXXXX.yaml)
trap 'rm -f "$SERVICE_YAML"' EXIT

cat > "$SERVICE_YAML" <<EOF
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: $BACKEND_SERVICE
  labels: {cloud.googleapis.com/location: $REGION}
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: '1'
        autoscaling.knative.dev/maxScale: '1'
        run.googleapis.com/container-dependencies: '{"backend":["postgres","redis","qdrant","clickhouse"]}'
    spec:
      containers:
        - name: backend
          image: ${IMAGE_BASE}/backend:latest
          ports:
            - containerPort: $BACKEND_PORT
          env:
            - {name: NODE_ENV, value: 'production'}
            - {name: PORT, value: '$BACKEND_PORT'}
            - {name: DATABASE_HOST, value: 'localhost'}
            - {name: DATABASE_PORT, value: '5432'}
            - {name: DATABASE_USER, value: '$POSTGRES_USER'}
            - {name: DATABASE_PASSWORD, value: '$POSTGRES_PASSWORD'}
            - {name: DATABASE_NAME, value: '$POSTGRES_DB'}
            - {name: REDIS_HOST, value: 'localhost'}
            - {name: REDIS_PORT, value: '6379'}
            - {name: QDRANT_HOST, value: 'localhost'}
            - {name: QDRANT_PORT, value: '6333'}
            - {name: CLICKHOUSE_HOST, value: 'localhost'}
            - {name: CLICKHOUSE_PORT, value: '8123'}
            - {name: JWT_ACCESS_SECRET, value: '$JWT_ACCESS_SECRET'}
            - {name: JWT_REFRESH_SECRET, value: '$JWT_REFRESH_SECRET'}
            - {name: OPENROUTER_API_KEY, value: '${OPENROUTER_API_KEY:-}'}
            - {name: OPENAI_API_KEY, value: '${OPENAI_API_KEY:-}'}
          resources:
            limits: {cpu: '1', memory: 1Gi}
        - name: postgres
          image: postgres:15-alpine
          env:
            - {name: POSTGRES_USER, value: '$POSTGRES_USER'}
            - {name: POSTGRES_PASSWORD, value: '$POSTGRES_PASSWORD'}
            - {name: POSTGRES_DB, value: '$POSTGRES_DB'}
          resources:
            limits: {cpu: '0.5', memory: 512Mi}
        - name: redis
          image: redis:7-alpine
          args: ["redis-server", "--appendonly", "yes"]
          resources:
            limits: {cpu: '0.25', memory: 256Mi}
        - name: qdrant
          image: qdrant/qdrant:latest
          resources:
            limits: {cpu: '0.5', memory: 512Mi}
        - name: clickhouse
          image: clickhouse/clickhouse-server:latest
          env:
            - {name: CLICKHOUSE_DB, value: '${CLICKHOUSE_DATABASE:-studyield_analytics}'}
            - {name: CLICKHOUSE_USER, value: '${CLICKHOUSE_USER:-default}'}
          resources:
            limits: {cpu: '0.5', memory: 512Mi}
EOF

echo "Deploying backend service (+ datastore sidecars) to Cloud Run..."
gcloud run services replace "$SERVICE_YAML" --project "$GCP_PROJECT" --region "$REGION"
gcloud run services add-iam-policy-binding "$BACKEND_SERVICE" \
  --project "$GCP_PROJECT" --region "$REGION" \
  --member="allUsers" --role="roles/run.invoker" --output=none

BACKEND_URL=$(gcloud run services describe "$BACKEND_SERVICE" \
  --project "$GCP_PROJECT" --region "$REGION" --format='value(status.url)')

echo "Deploying frontend service (VITE_API_URL=$BACKEND_URL)..."
gcloud run deploy "$FRONTEND_SERVICE" \
  --project "$GCP_PROJECT" --region "$REGION" \
  --image "${IMAGE_BASE}/frontend:latest" \
  --cpu 1 --memory 512Mi \
  --allow-unauthenticated \
  --set-env-vars "VITE_API_URL=${BACKEND_URL}"

FRONTEND_URL=$(gcloud run services describe "$FRONTEND_SERVICE" \
  --project "$GCP_PROJECT" --region "$REGION" --format='value(status.url)')

echo "---"
echo "Studyield deployed:"
echo "  Frontend: $FRONTEND_URL"
echo "  Backend API: $BACKEND_URL"
echo "Update tests/apps/studyield-app/nuguard-gcp.yaml target.url / redteam.target with"
echo "the backend URL above."
echo "Register a test account at the frontend URL, then set APP_USERNAME/APP_PASSWORD in .env."
echo "---"
echo "To tear down:"
echo "  gcloud run services delete $BACKEND_SERVICE --project $GCP_PROJECT --region $REGION --quiet"
echo "  gcloud run services delete $FRONTEND_SERVICE --project $GCP_PROJECT --region $REGION --quiet"
