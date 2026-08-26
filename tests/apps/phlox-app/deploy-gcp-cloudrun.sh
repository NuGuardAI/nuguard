#!/usr/bin/env bash
# Deploy Phlox to Google Cloud Run for NuGuard testing.
#
# Prerequisites: gcloud CLI logged in and configured (gcloud init), .env populated,
# a GCP project with Cloud Run + Secret Manager APIs enabled.
#
# NOTE: Cloud Run storage is ephemeral — the SQLCipher DB resets whenever the
# instance restarts or scales to zero. --min-instances=1 keeps one warm instance
# alive for the duration of a test run; data is still lost on redeploy.
#
# WARNING: Phlox has no built-in auth by default. --allow-unauthenticated
# exposes it publicly. Use only for short-lived testing with synthetic canary
# data and tear the service down (last command below) when done.
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +o allexport
fi

: "${DB_ENCRYPTION_KEY:?Set DB_ENCRYPTION_KEY in .env first}"
: "${GCP_PROJECT:?Set GCP_PROJECT (your GCP project ID) in .env or the environment}"

SERVICE_NAME="${CLOUD_RUN_SERVICE:-phlox-nuguard-test}"
REGION="${GCP_REGION:-us-central1}"
PORT="${PORT:-5000}"

echo "Deploying $SERVICE_NAME to Cloud Run (project $GCP_PROJECT, region $REGION)..."
gcloud run deploy "$SERVICE_NAME" \
  --project "$GCP_PROJECT" \
  --region "$REGION" \
  --image ghcr.io/bloodworks-io/phlox:latest \
  --port "$PORT" \
  --cpu 1 \
  --memory 2Gi \
  --min-instances 1 \
  --max-instances 1 \
  --no-cpu-throttling \
  --allow-unauthenticated \
  --set-env-vars "ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-*},PORT=${PORT},SERVER_HOST=0.0.0.0,DB_ENCRYPTION_KEY=${DB_ENCRYPTION_KEY}"

URL=$(gcloud run services describe "$SERVICE_NAME" \
  --project "$GCP_PROJECT" \
  --region "$REGION" \
  --format='value(status.url)')

echo "---"
echo "Phlox deployed: $URL"
echo "Update tests/apps/phlox-app/nuguard-gcp.yaml target.url / redteam.target with this URL."
echo "Complete first-run Settings -> Model Settings configuration in the UI before testing."
echo "---"
echo "To tear down: gcloud run services delete $SERVICE_NAME --project $GCP_PROJECT --region $REGION --quiet"
