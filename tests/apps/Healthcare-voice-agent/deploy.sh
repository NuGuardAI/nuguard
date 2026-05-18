#!/bin/bash

# Configuration
PROJECT_ID=$(gcloud config get-value project)
SERVICE_NAME="healthcare-voice-agent"
REGION="us-central1"

echo "Deploying $SERVICE_NAME to project $PROJECT_ID in region $REGION..."

# 1. Build and Deploy using Cloud Run
# We use --source . which automatically uses the Dockerfile in the root
gcloud run deploy $SERVICE_NAME \
    --source . \
    --project $PROJECT_ID \
    --region $REGION \
    --allow-unauthenticated \
    --memory 2Gi \
    --set-env-vars FRONTEND_ORIGIN=*

echo "Deployment complete!"
