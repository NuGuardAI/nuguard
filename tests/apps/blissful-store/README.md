# Blissful Store Fixture

This folder contains a realistic Google CES-based retail assistant fixture used to test NuGuard capabilities (SBOM generation, behavior, and redteam).

## What This Fixture Is

Blissful Store is a multi-agent retail assistant scenario that represents a production-like AI app:

- Root agent routes user requests for gardening and store support.
- Child agents handle upsell and out-of-scope conversations.
- Tools perform cart changes, recommendations, scheduling, CRM actions, and discount workflows.
- Guardrails and policy files model safety and prompt-protection behavior.
- A local proxy web app exposes a simple endpoint for NuGuard testing while forwarding to Google CES.

## Design Summary

Core design goals:

- Keep fixture behavior close to a real deployed agent system.
- Separate UI/proxy concerns from CES invocation logic.
- Provide both static artifacts (agent/tool JSON configs) and runnable runtime behavior.
- Support deterministic security testing via NuGuard profiles.

High-level architecture:

1. Local client sends a chat request to /api/chat on the fixture web server.
2. Web proxy builds CES runSession payload and app/session resource names.
3. Proxy obtains OAuth2 access token (env token, ADC, or gcloud fallback).
4. Proxy forwards request to CES and returns CES response back to caller.
5. NuGuard tools hit the local endpoint for validation and adversarial testing.

## Key Files and Folders

- app.json
  Main CES app definition (root agent, display metadata, model settings, guardrails).

- agents/
  Agent definitions and prompts for retail_agent, cymbal_upsell_agent, and out_of_scope_handling.

- tools/ and toolsets/
  Tool contracts and Python implementations, including CRM toolset OpenAPI schema.

- guardrails/
  Guardrail configurations used by the agent app.

- webapp/app.py
  Local HTTP server and CES proxy implementation.

- webapp/index.html and webapp/style.css
  Basic local UI used during manual testing.

- .env
  Local environment values for project/app/deployment IDs and auth/runtime settings.

- nuguard.yaml
  Fixture-specific NuGuard profile for sbom/behavior/redteam runs.

- test_api.py
  Direct smoke test against CES runSession.

- test_structure.py
  Basic structure check script in this fixture.

- reports/
  Generated SBOM and other test outputs.

## Implementation Notes

Runtime proxy behavior in webapp/app.py:

- Runs a ThreadingHTTPServer on HOST and PORT (defaults 127.0.0.1:8081).
- Serves static UI for GET / and forwards POST /api/chat to CES.
- Payload shape includes:
  - config.session
  - config.app_version
  - config.deployment
  - inputs[0].text
- Timeout is controlled by BLISSFUL_REQUEST_TIMEOUT.

Token acquisition order:

1. GCP_ACCESS_TOKEN or GOOGLE_ACCESS_TOKEN env var
2. Application Default Credentials via google-auth
3. gcloud auth print-access-token

This allows both CI/container and local developer workflows.

## Prerequisites

- Python environment available in repo (recommended: uv-managed .venv).
- Google access configured via one of:
  - ADC login
  - valid OAuth2 access token in env
  - working gcloud auth context
- IAM permission to call CES runSession on configured app resources.

Suggested quick checks:

1. Confirm .env in this folder has valid BLISSFUL_PROJECT_ID, BLISSFUL_LOCATION, BLISSFUL_APP_ID, BLISSFUL_APP_VERSION, BLISSFUL_DEPLOYMENT_ID.
2. Ensure your principal can call ces.sessions.runSession.

## How To Run Locally

Start web proxy (Windows-friendly command):

    uv run python tests/apps/blissful-store/webapp/app.py

Alternative bash launcher (Linux/macOS shells):

    bash tests/apps/blissful-store/serve.sh

Run CES smoke test:

    uv run python tests/apps/blissful-store/test_api.py

Expected smoke result:

- HTTP status 200
- JSON response with outputs text from CES agent

## Deploy To Cloud Run (Public URL)

Use this when you want the blissful-store UI and proxy reachable from the internet.

### 1) Prerequisites

- Google Cloud project with billing enabled
- gcloud authenticated with deploy permissions
- Cloud Run Admin, Service Account User, and Artifact Registry permissions
- A runtime service account with permission to invoke CES runSession

### 2) Build and push the container

From repo root:

  $PROJECT_ID = "your-gcp-project-id"
  $REGION = "us-central1"
  $SERVICE_NAME = "blissful-store-webapp"
  $IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/blissful-store/$SERVICE_NAME:latest"

  gcloud config set project $PROJECT_ID
  gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
  gcloud artifacts repositories create blissful-store --repository-format=docker --location=$REGION --description="blissful store images"
  gcloud auth configure-docker "$REGION-docker.pkg.dev"

  gcloud builds submit tests/apps/blissful-store/webapp --tag $IMAGE

### 3) Deploy the service

Replace the placeholders with your deployed CES identifiers:

  $RUNTIME_SA = "blissful-store-runtime@$PROJECT_ID.iam.gserviceaccount.com"
  $BLISSFUL_LOCATION = "us"
  $BLISSFUL_APP_ID = "<app-id>"
  $BLISSFUL_APP_VERSION = "<version-id>"
  $BLISSFUL_DEPLOYMENT_ID = "<deployment-id>"

  gcloud run deploy $SERVICE_NAME `
    --image $IMAGE `
    --region $REGION `
    --platform managed `
    --allow-unauthenticated `
    --service-account $RUNTIME_SA `
    --set-env-vars "HOST=0.0.0.0,BLISSFUL_PROJECT_ID=$PROJECT_ID,BLISSFUL_LOCATION=$BLISSFUL_LOCATION,BLISSFUL_APP_ID=$BLISSFUL_APP_ID,BLISSFUL_APP_VERSION=$BLISSFUL_APP_VERSION,BLISSFUL_DEPLOYMENT_ID=$BLISSFUL_DEPLOYMENT_ID,BLISSFUL_REQUEST_TIMEOUT=60"

Notes:

- Cloud Run sets PORT automatically; app.py reads it from env.
- HOST must be 0.0.0.0 in Cloud Run.

### 4) Smoke test the public URL

  $SERVICE_URL = gcloud run services describe $SERVICE_NAME --region $REGION --format="value(status.url)"
  Invoke-WebRequest -Uri $SERVICE_URL -Method GET

  $body = @{ text = "Hi, I need a recommendation for indoor plants."; session_id = "public-smoke-test" } | ConvertTo-Json
  Invoke-WebRequest -Uri "$SERVICE_URL/api/chat" -Method POST -ContentType "application/json" -Body $body

Expected result:

- HTTP 200 from both checks
- JSON outputs text from CES

### 5) Optional custom domain

After deployment, map a custom domain to Cloud Run for a friendly URL:

  gcloud run domain-mappings create --service $SERVICE_NAME --domain your.domain.example --region $REGION

Then update DNS records as instructed by gcloud.

## Manual Test Walkthrough (Windows)

Use this sequence to validate the app manually before running larger NuGuard scans.

1. Open PowerShell in the repo root and activate the venv:

     .\.venv\Scripts\Activate.ps1

2. Ensure ADC auth is valid:

     gcloud auth application-default login
     gcloud auth application-default print-access-token

  Expected: the second command prints a token instead of an error.

3. Verify fixture env values in `tests/apps/blissful-store/.env`:

  - `BLISSFUL_PROJECT_ID`
  - `BLISSFUL_LOCATION`
  - `BLISSFUL_APP_ID`
  - `BLISSFUL_APP_VERSION`
  - `BLISSFUL_DEPLOYMENT_ID`

4. Start the local proxy app:

     uv run python tests/apps/blissful-store/webapp/app.py

  Expected: `Serving Blissful webapp on http://127.0.0.1:8081`

5. Open the app in browser and send a few prompts:

  - `http://127.0.0.1:8081`

6. In a second PowerShell window, test the API directly:

     $body = @{ text = "Hi, I need a recommendation for indoor plants." } | ConvertTo-Json
     Invoke-WebRequest -Uri http://127.0.0.1:8081/api/chat -Method POST -ContentType "application/json" -Body $body

  Expected: HTTP 200 and JSON output from CES.

7. Run fixture smoke test:

     uv run python tests/apps/blissful-store/test_api.py

8. Optional pre-redteam target check:

     $env:APP_USERNAME='test-user'
     $env:APP_PASSWORD='test-pass'
     uv run nuguard target verify --config tests/apps/blissful-store/nuguard.yaml --mode redteam --target http://127.0.0.1:8081 --endpoint /api/chat

  Expected: `Status ok`, HTTP 200.

9. If errors appear:

  - 401 from CES: re-run ADC login and restart app.
  - Port conflict: change `PORT` in `.env` or stop existing process on 8081.
  - 500 from local proxy: check app terminal output for upstream CES/auth details.

## How To Test With NuGuard

Generate SBOM from this fixture source:

    uv run nuguard sbom generate --source tests/apps/blissful-store --format json -o tests/apps/blissful-store/reports/blissful-store-sbom-YYYYMMDD-HHMMSS.json

Run behavior analysis against local proxy:

  uv run nuguard behavior --config tests/apps/blissful-store/nuguard.yaml

Run redteam against local proxy:

    uv run nuguard redteam --config tests/apps/blissful-store/nuguard.yaml

Notes:

- Prefer timestamped output filenames in reports/ to avoid overwriting prior runs.
- sbom_generation.llm in config controls whether SBOM enrichment metadata is added.

## Common Troubleshooting

Port already in use:

- Change PORT env var or stop the existing process on 8081.

Auth errors (401/403):

- Verify ADC login or token freshness.
- Verify IAM includes permission for CES runSession.
- Confirm resource IDs in .env match deployed CES app/version/deployment.

bash serve.sh fails on Windows:

- Use direct Python launch command instead of bash script.

Slow or timed-out CES responses:

- Increase BLISSFUL_REQUEST_TIMEOUT in .env.

## Security Testing Intent

This fixture is intentionally rich in agent-tool interactions and policy/guardrail surface area so NuGuard can:

- Discover runtime and configuration attack surface via SBOM.
- Assess happy-path and policy compliance behavior.
- Exercise adversarial scenarios (prompt injection, tool abuse, privilege escalation, data exfiltration, policy violations, MCP toxic flow where configured).

Use this fixture as a baseline for repeatable local and CI security testing workflows.
