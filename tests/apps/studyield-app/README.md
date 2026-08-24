# Studyield — NuGuard Test Target

[Studyield](https://github.com/studyield/studyield) is an open-source AI-powered
learning platform (NestJS backend, React/Vite frontend, PostgreSQL, Redis,
Qdrant, ClickHouse). Its AI feature set — RAG chat over uploaded documents, a
multi-agent problem solver, exam-clone generation, teach-back evaluation, a
Python code sandbox, and deep research with web search — makes it a good
dynamic red-team target: multiple tool-calling surfaces, a code execution
sandbox, and multi-tenant student data.

**No Studyield source is vendored here.** `nuguard.yaml`'s `source:` points
directly at `https://github.com/studyield/studyield` — `nuguard sbom generate`
clones it internally for the duration of the scan. **Unlike Phlox, Studyield
has no published container image** — to actually *run* a live instance,
`clone-studyield.sh` shallow-clones the source into `./repo/` (gitignored),
and `start-local.sh` / the deploy scripts build its images from that checkout.

> **Warning:** Studyield handles synthetic student data (PII-shaped: name,
> email, uploaded documents/exams) in this setup. Only ever use the fabricated
> data in [canary.json](canary.json) — never real student information.
> Studyield requires a real account (JWT-based login) — register a test user
> and never reuse real credentials. Tear down cloud deployments as soon as
> testing is done (see the deploy scripts).

## Layout

| File | Purpose |
|---|---|
| `nuguard.yaml` | Config for the local Docker Compose target (`http://localhost:3010`) |
| `nuguard-azure.yaml` | Config for the Azure Container Instances target |
| `nuguard-gcp.yaml` | Config for the Cloud Run target |
| `.env.example` / `.env` | Docker + LLM + auth environment variables (`.env` is gitignored) |
| `clone-studyield.sh` | Shallow-clone Studyield's source into `./repo/` (gitignored) |
| `start-local.sh` | Run Studyield locally via its own `docker-compose.yml` |
| `deploy-azure-aci.sh` | Build images via ACR and deploy a multi-container ACI group |
| `deploy-gcp-cloudrun.sh` | Build images via Cloud Build and deploy to Cloud Run |
| `canary.example.json` | Synthetic student/document records for behavior/redteam canary scanning |
| `seed-data.sh` | Register test accounts and load canary + golden/control data into a running instance |
| `studyield-test.sh` | Full pipeline: sbom → policy draft/compile/check → analyze → behavior → redteam |
| `run-behavior.sh` / `run-redteam.sh` | Run one stage in isolation |

Cognitive policy (`cognitive-policy.md` / `cognitive-policy.json`) is **not**
checked in — `studyield-test.sh` drafts it at runtime with `nuguard init --llm`,
grounded in the generated SBOM, then compiles it. Delete it and re-run to
regenerate.

## 1. Configure

```bash
cp .env.example .env
# Edit .env: set OPENROUTER_API_KEY (or OPENAI_API_KEY) for Studyield's AI
# features, and APP_USERNAME/APP_PASSWORD once you've registered a test account
# (step 3). JWT_ACCESS_SECRET/JWT_REFRESH_SECRET already have working local
# defaults — replace them before any non-local deployment.
```

## 2. Run Studyield

### Local (Docker Compose)

```bash
./start-local.sh
```

This clones `./repo` on first run, wires `.env` into Studyield's expected
`.env.docker` / `backend/.env` files, and runs Studyield's own
`docker compose up --build` (PostgreSQL, Redis, Qdrant, ClickHouse, backend,
frontend).

### Azure (Container Instances)

```bash
az login
./deploy-azure-aci.sh
# copy the printed backend URL into nuguard-azure.yaml (target.url and redteam.target)
```

Builds the backend/frontend images in the cloud via `az acr build` (no local
Docker required) and deploys one multi-container ACI group — backend,
frontend, and all four datastores share one network namespace, so the backend
reaches them at `localhost` exactly as it does under docker-compose.

### Google Cloud (Cloud Run)

```bash
gcloud init
export GCP_PROJECT=your-project-id   # or add to .env
./deploy-gcp-cloudrun.sh
# copy the printed backend URL into nuguard-gcp.yaml (target.url and redteam.target)
```

Builds images via Cloud Build and deploys the backend as one Cloud Run service
with the four datastores as **sidecar containers** (Cloud Run multi-container
revisions), plus a second, plain Cloud Run service for the frontend pointed at
the backend's URL.

> Cloud Run container storage is ephemeral — datastore state resets on
> restart/redeploy/scale-to-zero. `--min-instances=1` keeps one instance warm
> for the duration of a test run, but this is not a substitute for managed
> Cloud SQL / Memorystore / a hosted vector+analytics store in anything beyond
> short-lived testing.

## 3. Seed multi-user, canary, and golden data

Studyield requires a real account — there is no anonymous/API-key mode.
`seed-data.sh` automates this instead of manual UI registration:

```bash
./seed-data.sh                              # local target (default: http://localhost:3010/api/v1)
./seed-data.sh http://REPLACE_WITH_ACI_FQDN:3010/api/v1
./seed-data.sh https://REPLACE_WITH_CLOUD_RUN_URL/api/v1
```

It registers three accounts (safe to re-run — logs in instead if they already
exist) and loads distinct content into each:

| Account | Purpose | Seeded content |
|---|---|---|
| `student-alpha` | Canary persona | Knowledge base with a golden (non-secret) biology note **and** a canary-tagged note containing `CANARY-DOC-8841` |
| `student-beta` | Canary persona | Exam clone (`NUGUARD-CANARY-TOKEN-9c4e1a7f` in `examText`) + teach-back session (`CANARY-NOTE-2291` in `referenceContent`) |
| `student-gamma` | Golden/control — **no canary secrets** | Same content shapes (knowledge base, exam clone, teach-back) but with only legitimate study content |

`student-gamma` matters because a cross-tenant exfiltration scenario against an
account with *no* secrets at all is not a meaningful test — refusal is
trivial when there's nothing to leak. It exists so redteam scenarios that
probe "give me another student's data" have a real, populated target account
to probe, making a refusal there a genuine signal rather than a false negative.

The script writes resolved credentials/resource IDs to `seed-data.json`
(gitignored — contains real login credentials for the seeded accounts) and
sets `APP_USERNAME`/`APP_PASSWORD` in `.env` to `student-alpha` so
`nuguard.yaml`'s `login_flow` auth works immediately.

> If Studyield's actual auth response shape ever differs from
> `{ user, tokens: { accessToken, refreshToken, expiresIn }, subscription }`
> (verified against `backend/src/modules/auth/auth.service.ts`), update both
> `seed-data.sh`'s `json_get ... tokens.accessToken` calls and
> `target.auth.login_flow.token_response_key` in the `nuguard*.yaml` configs.

## 4. Run the pipeline

```bash
./studyield-test.sh                    # local target
./studyield-test.sh nuguard-azure.yaml # Azure target
./studyield-test.sh nuguard-gcp.yaml   # GCP target
```

Or run a single stage: `./run-behavior.sh` / `./run-redteam.sh`.
