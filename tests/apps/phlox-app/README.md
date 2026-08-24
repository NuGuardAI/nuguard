# Phlox — NuGuard Test Target

[Phlox](https://github.com/bloodworks-io/phlox) is a free, open-source, local-first
AI medical scribe (FastAPI backend, React/Chakra frontend, SQLCipher-encrypted
SQLite DB, agentic tool-calling + MCP server support). This folder wires it up
as a NuGuard test target: static SBOM/analysis of its source, plus dynamic
`behavior`/`redteam` runs against a live instance.

**No Phlox source is vendored here.** `nuguard.yaml`'s `source:` points directly
at `https://github.com/bloodworks-io/phlox` — `nuguard sbom generate` clones it
internally for the duration of the scan.

> **Warning:** Phlox handles synthetic clinical data (PHI-shaped) in this setup.
> Only ever use the fabricated data in [canary.json](canary.json) — never real
> patient information. Phlox ships with **no built-in authentication by
> default**; don't expose a running instance to the public internet without a
> reverse proxy/auth in front of it, and tear down cloud deployments as soon as
> testing is done (see the deploy scripts).

## Layout

| File | Purpose |
|---|---|
| `nuguard.yaml` | Config for the local Docker target (`http://localhost:5000`) |
| `nuguard-azure.yaml` | Config for the Azure Container Instance target |
| `nuguard-gcp.yaml` | Config for the Cloud Run target |
| `.env.example` / `.env` | Docker + LLM environment variables (`.env` is gitignored) |
| `docker-compose.yml` / `start-local.sh` | Run Phlox locally from the published image |
| `deploy-azure-aci.sh` | Deploy Phlox to a single Azure Container Instance |
| `deploy-gcp-cloudrun.sh` | Deploy Phlox to Google Cloud Run |
| `canary.json` | Synthetic patient/clinician records for behavior/redteam canary scanning |
| `seed-data.sh` | Load `canary.json`'s patients into a running Phlox instance via its API |
| `phlox.ground-truth.sbom.json` | Hand-curated ground-truth AI-SBOM (schema per `documentation/docs/sample-sbom.json`) — benchmark `nuguard sbom generate` output against this to check extractor accuracy on the real Phlox codebase |
| `phlox-test.sh` | Full pipeline: sbom → policy draft/compile/check → analyze → behavior → redteam |
| `run-behavior.sh` / `run-redteam.sh` | Run one stage in isolation |

Cognitive policy (`cognitive-policy.md` / `cognitive-policy.json`) is **not**
checked in — `phlox-test.sh` drafts it at runtime with `nuguard init --llm`,
grounded in the generated SBOM, then compiles it. Delete it and re-run to
regenerate.

## 1. Configure

```bash
cp .env.example .env
# Edit .env: set OPENAI_API_KEY (used both by NuGuard's own LLM judge/scenario
# generation and as the model you'll paste into Phlox's Settings UI).
```

## 2. Run Phlox

### Local (Docker)

```bash
./start-local.sh
```

### Azure (Container Instance)

```bash
az login
./deploy-azure-aci.sh
# copy the printed FQDN into nuguard-azure.yaml (target.url and redteam.target)
```

### Google Cloud (Cloud Run)

```bash
gcloud init
export GCP_PROJECT=your-project-id   # or add to .env
./deploy-gcp-cloudrun.sh
# copy the printed URL into nuguard-gcp.yaml (target.url and redteam.target)
```

## 3. First-run setup (required, all environments)

The Docker image has **no bundled LLM or transcription engine** — Phlox reads
model config from its own UI-driven Settings, not environment variables:

1. Open the running instance (`http://localhost:5000`, the ACI FQDN, or the
   Cloud Run URL).
2. Go to **Settings → Model Settings** and point Phlox at an OpenAI-compatible
   LLM endpoint (the same key/model you put in `.env` works if you're using
   OpenAI directly).
3. Seed the synthetic patients in [canary.json](canary.json) so NuGuard's
   canary scan has real records to try to exfiltrate:
   ```bash
   ./seed-data.sh                              # local target (http://localhost:5000)
   ./seed-data.sh http://<aci-fqdn-or-ip>:80    # Azure/GCP target
   ```
   This POSTs each `canary.json` patient (name, DOB, MRN, email, phone,
   diagnosis/medication, plus any paired clinical note) to Phlox's
   `/api/note/save` API — no manual entry via the **Patients** tab needed.
   Safe to re-run.

## 4. Run NuGuard

```bash
./phlox-test.sh                     # local target
./phlox-test.sh nuguard-azure.yaml   # Azure target
./phlox-test.sh nuguard-gcp.yaml     # GCP target
```

Reports land in `reports/`. `analyze`, `policy check`, `behavior`, and `redteam`
exit non-zero when findings are present — that's expected during testing, so
the script treats those as non-fatal and continues.

## Scenario focus

Given Phlox's domain (clinical notes, PHI, MCP tool integrations), the redteam
scenario set favors: `prompt-injection`, `tool-abuse`, `privilege-escalation`,
`data-exfiltration`, `policy-violation`, and `mcp-toxic-flow` — over the
banking-specific scenarios used in `tests/apps/pinnacle-bank-app`.
