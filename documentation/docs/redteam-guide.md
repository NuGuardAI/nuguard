# NuGuard Red-Team Engine

Dynamic adversarial testing for live AI applications. It's designed for AI developers who may not have deep security expertise but want to proactively identify and fix weaknesses in their AI systems before production.

The engine takes an AI-SBOM, a target URL, and optionally a Cognitive Policy, then automatically generates, executes, and scores attack scenarios against the running application — producing structured findings with OWASP/MITRE mappings and LLM-generated remediation briefs.

Only run the red-team engine against a sandbox or staging environment — never against production. It sends adversarial payloads designed to trigger real tool calls, data access, and side effects, so it should only ever run against an environment where that behavior is safe to provoke.

---

## Quick Start

Red-teaming needs a live target — point `nuguard init` at your running app, then run it.

<img src="assets/quickstart-4-redteam.svg" alt="nuguard init --target <your-app-url>. nuguard sbom generate --source <path-to-your-app> --output app.sbom.json. nuguard redteam --config nuguard.yaml --format markdown --output <output-path>." width="620">

**Common changes** (in `nuguard.yaml`, under `target:` / `redteam:`):

- Target URL and endpoint — `target.url`, `target.endpoint` (default: auto-discovered from SBOM, fallback `/chat`)
- Request/response payload shape — `redteam.chat_payload_key` / `chat_response_key` / `chat_payload_extras` if your app doesn't use `{"message": "..."}` / `{"response": "..."}`
- Auth — `target.auth`: `bearer`, `api_key`, `basic`, or `login_flow`

---

## Configuration

The examples below use the [OpenAI Customer Service Agents demo](example-openai-cs-agents.md) — a FastAPI backend serving five airline-support agents — as a worked example. Full walkthrough: [example-openai-cs-agents.md](example-openai-cs-agents.md).

### Target URL

`target.url` is the base URL of the **running backend**, not the frontend. Point it at wherever the app's chat endpoint is served — a local dev server, a staging deployment, or a container. For the demo app that's the FastAPI process, not the Next.js UI:

```yaml
target:
  url: http://localhost:8000        # backend URL, not the frontend
```

`target.endpoint` is the chat endpoint path appended to `target.url`. Leave it empty to auto-discover from the SBOM's `API_ENDPOINT` nodes; it falls back to `/chat` if nothing is discovered:

```yaml
target:
  url: http://localhost:8000
  endpoint: /chat                   # optional — omit to auto-discover from the SBOM
```

Resolution order when no `target.url` is set at all: `--target` CLI flag → `redteam.target` in `nuguard.yaml` → SBOM-discovered URLs (local → staging → production) → error.

Before running a full scan, verify the URL and endpoint actually resolve to a live chat endpoint:

```bash
nuguard target verify --config nuguard.yaml
```

### Auth

`target.auth` is a shared block inherited by both `nuguard behavior` and `nuguard redteam`; override it under `redteam.auth` only if red-teaming needs different credentials than behavioral testing. Supported `type` values:

| Type | Use case | Required fields |
|---|---|---|
| `bearer` | Static bearer token | `header` (e.g. `"Authorization: Bearer ${TARGET_TOKEN}"`) |
| `api_key` | API key in a custom header | `header` (e.g. `"X-API-Key: ${TARGET_API_KEY}"`) |
| `basic` | HTTP Basic Auth | `username`, `password` |
| `login_flow` | App exposes a login endpoint that returns a token | `login_flow.endpoint`, `login_flow.payload`, `login_flow.token_response_key`, `login_flow.token_header` |
| `none` | Open/local-dev endpoint, no credentials | — |

The demo app runs locally with no auth in front of it:

```yaml
target:
  auth:
    type: none
```

Always source credential values from environment variables with `${VAR}` interpolation — never commit tokens or passwords directly into `nuguard.yaml`.

### Redteam profile (CI vs. Full)

`redteam.profile` (`--profile` on the CLI) controls how many scenarios run, trading speed for coverage:

| Profile | Scenario count | Threshold | When to use |
|---|---|---|---|
| `ci` (default) | fast, high-signal only | `base_impact` ≥ 5.0 | Pre-merge gates, PR checks — fails fast on high-severity issues |
| `standard` | ~30 scenarios | `base_impact` ≥ 3.0 | Regular sanity scans during development |
| `full` | all scenarios (50+ on rich SBOMs) | no threshold | Pre-release audits, security review, the OpenAI CS agents example below |

`redteam.min_impact_score` overrides the profile's built-in threshold directly — set it to exclude low pre-score scenarios regardless of profile.

For the demo app's pre-release scan, the `nuguard.yaml` sets:

```yaml
redteam:
  profile: full
```

which is what produced the 111-scenario run described in [Red-Team the Live App](example-openai-cs-agents.md#6-red-team-the-live-app). For a fast CI gate on the same app, switch to:

```bash
nuguard redteam -c nuguard.yaml --profile ci --format sarif --output results.sarif --fail-on high
```

### Scenarios (attack family filter)

`redteam.scenarios` (`--scenarios` on the CLI) restricts the run to specific `GoalType` attack families. Leave it empty to run all families. Values: `prompt-driven-threat`, `policy-violation`, `data-exfiltration`, `privilege-escalation`, `tool-abuse`, `mcp-toxic-flow`, `api-attack`, `agentic-trust-abuse`, `recon-inference`. Stable catalog IDs (e.g. `D01,C03`) also work.

For the customer-service demo, its most sensitive surfaces are `cancel_flight` (restricted action, reachable from every agent) and the SQLite booking/account datastores — so a targeted scan focuses on the goal types that probe those:

```yaml
redteam:
  scenarios:
    - data-exfiltration
    - privilege-escalation
    - prompt-driven-threat
```

Equivalent CLI form:

```bash
nuguard redteam -c nuguard.yaml \
  --scenarios data-exfiltration,privilege-escalation,prompt-driven-threat
```

The full unfiltered run (all 9 families) is what the [example walkthrough](example-openai-cs-agents.md) uses in its `nuguard.yaml`, alongside `canary`, `similar_miss_threshold`, `scenario_timeout`, and `guided_conversations` settings — see [Set Up Project Config](example-openai-cs-agents.md#4-set-up-project-config) for the complete file.

For the full list of attack vectors behind each family — 125 scenarios across 18 categories, with per-scenario impact scores and safe-execution modes — see the [Red-Team Scenario Catalog](redteam-scenario-catalog.md).

### 📖 Need every flag?

[![Read the CLI Reference](https://img.shields.io/badge/→_Read_the_CLI_Reference-111111?style=for-the-badge)](cli-reference.md#nuguard-redteam)

### 🔀 Want to run a different test?

[![Back to Quick Start](https://img.shields.io/badge/←_Back_to_Quick_Start-111111?style=for-the-badge)](quick-start.md)

---
