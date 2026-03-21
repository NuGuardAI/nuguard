# NuGuard OSS — High-Level Design and Architecture

**Date:** March 2026
**Repo:** https://github.com/NuGuardAI/nuguard-oss
**Status:** Draft

---

## 1. What NuGuard OSS Is

NuGuard OSS is an **open-source AI application security CLI** that gives developers a complete security pipeline for agentic AI systems — from SBOM generation and static analysis, through cognitive policy compliance checking, to live red-team testing against the running application.

It **subsumes Xelo** (the AI-SBOM generation tool), bundling SBOM generation directly into the `nuguard sbom` command. Xelo is retired as an independent entity. All Xelo SBOM generation, validation, and schema capabilities are now part of NuGuard OSS.

Five commands in a single `nuguard` CLI:

```
nuguard sbom      → generate, validate, and register AI-SBOMs (subsumes Xelo)
nuguard analyze   → static risk findings from the AI-SBOM alone (no app needed)
nuguard policy    → cognitive policy validation and compliance framework checking
nuguard redteam   → dynamic adversarial testing against the running application
nuguard scan      → meta-command: runs sbom → analyze → policy → redteam in sequence
```

`nuguard scan` is the recommended entry point for CI pipelines and first-time users. The four individual commands remain unchanged for users who need granular control.

**Design principles for OSS:**
- **Zero infrastructure to start**: SQLite by default, Postgres optional. No Redis, no Neo4j.
- **Single package**: `pip install nuguard` installs everything.
- **Single container**: `docker run ghcr.io/nuguardai/nuguard` runs everything.
- **CI-first**: all capabilities produce SARIF output and CI-compatible exit codes.
- **No mandatory LLM API key**: template-based payloads work without one; LLM improves payload quality when available.
- **File-based composition**: `nuguard.yaml` is the "pipe" between commands — each command reads the SBOM path, policy path, and target URL from it automatically. Individual commands can also be chained with `&&` for scripting.

**Commercial boundary:** The NuGuard commercial product (`nuguard-app`) adds: web dashboard, hosted service, multi-tenant org management, enterprise SSO, portfolio risk visibility, ServiceNow and Azure DevOps integrations, and advanced static analysis and red-team capabilities (future). Everything in this repo is open-source.

---

## 2. Capabilities

### 2.1 `nuguard sbom` — SBOM Generation and Management

Generate, validate, and register Xelo AI-SBOMs. NuGuard OSS bundles SBOM generation directly — no separate Xelo installation required.

```bash
# Generate an AI-SBOM by introspecting the application source
nuguard sbom generate --source ./my-agent-app --output ./app.sbom.json

# Generate from a running application's introspection endpoint
nuguard sbom generate --from-url http://localhost:3000/introspect --output ./app.sbom.json

# Validate an existing SBOM against the Xelo schema
nuguard sbom validate --file ./app.sbom.json

# Register for subsequent commands
nuguard sbom register --file ./app.sbom.json

# Inspect a stored SBOM
nuguard sbom show --sbom-id <id>
```

**SBOM generation discovers** (via the absorbed Xelo `AiSbomExtractor` pipeline):

| Node type | What it captures |
|---|---|
| `AGENT` | LangGraph, CrewAI, AutoGen, OpenAI Agent orchestrators |
| `MODEL` | LLM/embedding model references and provider |
| `TOOL` | Function tools and MCP tools wired to agents |
| `PROMPT` | System instructions and prompt templates (with `injection_risk_score`) |
| `DATASTORE` | Vector stores, databases, caches — with PII/PHI classification per table |
| `GUARDRAIL` | Content filters and safety validators |
| `AUTH` | OAuth2, Bearer, API key, JWT, MCP auth providers |
| `PRIVILEGE` | Capability grants (`db_write`, `filesystem_write`, `code_execution`, etc.) |
| `API_ENDPOINT` | Exposed HTTP routes and MCP endpoints |
| `IAM` | IAM roles, service accounts, trust policies from IaC |
| `DEPLOYMENT` | Kubernetes, Terraform, CloudFormation, GitHub Actions units |

The generated document conforms to the Xelo AI-SBOM schema v1.3.0. The canonical JSON Schema is bundled at `nuguard/sbom/schema/aibom.schema.json`.

### 2.2 `nuguard analyze` — Static SBOM Analysis

Analyze the AI-SBOM to produce security risk findings **without running the application**. A fast, zero-infrastructure pass that identifies attack surface issues from the SBOM structure alone.

What static analysis detects (built on the absorbed Xelo toolbox plugins):

**From `VulnerabilityScannerPlugin` + `AtlasAnnotatorPlugin` (VLA rules + MITRE ATLAS graph checks):**
- Tools with no-auth connected to high-privilege data stores
- `PRIVILEGE` nodes with `db_write` or `filesystem_write` reachable from unauthenticated tools
- Agents missing a `GUARDRAIL` node on paths to sensitive `DATASTORE` nodes
- `PROMPT` nodes with high `injection_risk_score` fed from external data
- `IAM` nodes with overly permissive policies (`overly_permissive_iam`)
- Container/deployment nodes running as root or missing resource limits

**From NuGuard's attack graph detectors (`nuguard/analysis/detectors/`):**
- HITL triggers in cognitive policy with no enforcement path in the SBOM graph
- Cross-tenant data access paths (agent accessing DATASTORE outside its declared scope)
- Low→high privilege escalation paths through chained tool calls
- API endpoints with `IDOR-surface` or `SQL-injectable` risk attributes
- Missing rate limits on externally-accessible endpoints

**From SBOM `ScanSummary.security_findings`** (pre-computed by Xelo):
- `container_runs_as_root`, `missing_health_check`, `no_resource_limits`, `secrets_in_env_vars`, `overly_permissive_iam`

```bash
nuguard analyze --sbom ./app.sbom.json                     # markdown output to stdout
nuguard analyze --sbom ./app.sbom.json --format sarif      # SARIF for CI gate
nuguard analyze --sbom ./app.sbom.json --policy ./policy.md  # cross-check against policy
nuguard analyze --sbom ./app.sbom.json --min-severity high   # filter findings
```

**Output:** Findings with severity, affected component, attack path, OWASP LLM Top 10 reference, and remediation advice — all from the SBOM, no live app required. Completes in seconds.

### 2.3 `nuguard policy` — Cognitive Policy Management

Parse, validate, and check cognitive policies. Cross-checks the policy against the SBOM to find structural gaps — e.g., HITL triggers that have no enforcement path in the graph, or restricted actions referencing tools not in the SBOM.

```bash
nuguard policy validate --file ./policy.md                            # lint for completeness and ambiguity
nuguard policy check --sbom ./app.sbom.json --policy ./policy.md     # static cross-check
nuguard policy show --policy-id <id>                                  # inspect parsed policy sections
```

**Compliance framework output:** Findings are tagged with applicable framework references:
- OWASP LLM Top 10 (v1)
- NIST AI RMF (v1.5)
- EU AI Act Articles (v1.5)

### 2.4 `nuguard redteam` — Dynamic Red-Team Testing

Send adversarial inputs to the running AI application and evaluate its behavior against the cognitive policy. Requires the application to be running at a `--target` URL.

```bash
# Seed canary data before the scan
nuguard seed --target http://localhost:3000 --seed-file ./canary.json

# Run red-team test
nuguard redteam \
  --sbom ./app.sbom.json \
  --policy ./policy.md \
  --target http://localhost:3000 \
  --canary ./canary.json \
  --profile ci

# Get report
nuguard report --test-id <id> --format sarif
nuguard report --test-id <id> --format markdown
nuguard findings --test-id <id> --severity critical,high

# Replay a scan deterministically
nuguard replay --test-id <id> --target http://localhost:3000
```

### 2.5 `nuguard scan` — Pipeline Meta-Command

Run the full security pipeline with a single command. `nuguard scan` is a thin orchestrator — it calls the same `SbomGenerator`, `StaticAnalyzer`, `PolicyChecker`, and `RedteamOrchestrator` that the individual commands call. No new business logic, no duplication.

**`nuguard.yaml` is the pipe.** The SBOM produced by step 1 is written to disk; steps 2–4 read it from the path configured in `nuguard.yaml` (or `--output-dir`). Each step writes its artifacts so intermediate results are always inspectable.

```bash
# First-time: generate SBOM, run static analysis and policy check
nuguard scan --source . --policy ./policy.md

# CI: static-only gate (no live app required)
nuguard scan --steps sbom,analyze,policy --fail-on high --output-dir ./reports

# Full pipeline including redteam against a staging URL
nuguard scan --target https://staging.myapp.com --profile ci

# With nuguard.yaml configured, all paths are picked up automatically
nuguard scan
```

**Execution order (always sequential, earlier failures stop the chain):**

```
nuguard scan
│
├─ Step 1: sbom      nuguard sbom generate  → output-dir/sbom.json
├─ Step 2: analyze   nuguard analyze        → output-dir/findings.json
│                                              output-dir/findings.sarif
│                                              output-dir/report.md
├─ Step 3: policy    nuguard policy check   → output-dir/policy-report.md
└─ Step 4: redteam   nuguard redteam        → output-dir/redteam-report.md
                     (skipped if --target not set and not in nuguard.yaml)
```

**CLI flags:**

| Flag | Purpose | Default |
|---|---|---|
| `--source` | Source path for SBOM generation | `.` |
| `--policy` | Path to cognitive policy file | from `nuguard.yaml` |
| `--target` | Live app URL for redteam step | from `nuguard.yaml` |
| `--steps` | Comma-separated subset: `sbom,analyze,policy,redteam` | all except `redteam` when no target |
| `--output-dir` | Directory for all artifacts | `./nuguard-reports` |
| `--fail-on` | Severity threshold for non-zero exit | `high` |
| `--profile` | Redteam profile: `ci` or `full` | `ci` |
| `--from-sbom` | Use existing SBOM, skip generation | None |

**When to use `nuguard scan` vs individual commands:**

| User | Command |
|---|---|
| First time, just explore | `nuguard scan --source .` |
| CI, static-only gate | `nuguard scan --steps sbom,analyze,policy --fail-on high` |
| CI, full pipeline | `nuguard scan --target https://staging.myapp.com` |
| Debug a specific step | `nuguard analyze --sbom ./sbom.json --format sarif` |
| Script with `&&` | `nuguard sbom generate && nuguard analyze` |

**Why not Unix stdin/stdout piping** (`nuguard sbom generate | nuguard analyze`): The SBOM JSON is large, intermediate artifacts should be inspectable, and `redteam` requires flags (`--target`, `--policy`) that cannot come from stdin. File-based handoff via `nuguard.yaml` and `--output-dir` is the correct composition model.

---

## 3. Architecture

### 3.1 Package Structure

```
nuguard/                          # PyPI package: "nuguard"
  cli/
    main.py                       # typer entry point: nuguard <command>
    commands/
      sbom.py                     # nuguard sbom generate|validate|register|show
      analyze.py                  # nuguard analyze
      policy.py                   # nuguard policy validate|check|show
      redteam.py                  # nuguard redteam
      scan.py                     # nuguard scan (meta-command: orchestrates sbom→analyze→policy→redteam)
      seed.py                     # nuguard seed
      report.py                   # nuguard report
      findings.py                 # nuguard findings
      replay.py                   # nuguard replay

  sbom/
    generator.py                  # Thin wrapper around AiSbomExtractor (absorbed from Xelo)
    extractor/                    # Absorbed Xelo AiSbomExtractor pipeline
      core.py                     # AiSbomExtractor entry point
      config.py                   # AiSbomConfig (scan scope, LLM enrichment)
      serializer.py               # AiSbomSerializer: Xelo JSON, CycloneDX, SPDX
      framework_adapters/
        langchain.py              # LangChain agent/tool extractor
        openai_functions.py       # OpenAI function calling extractor
        autogen.py                # AutoGen agent extractor
        crewai.py                 # CrewAI extractor
      iac_scanners/               # Terraform, K8s, CloudFormation, GitHub Actions
      pii_classifier.py           # SQL + Pydantic/ORM PII/PHI classification
    toolbox/
      plugins/
        vulnerability.py          # VulnerabilityScannerPlugin (VLA rules + OSV/Grype)
        atlas_annotator.py        # AtlasAnnotatorPlugin (MITRE ATLAS graph checks)
        sarif_exporter.py         # SarifExporterPlugin
        markdown_exporter.py      # MarkdownExporterPlugin
    parser.py                     # Xelo SBOM JSON → XeloSBOM Pydantic model
    validator.py                  # Validate against Xelo schema
    schema/
      aibom.schema.json           # Bundled Xelo AI-SBOM JSON Schema v1.3.0

  graph/
    mapper.py                     # XeloSBOM → Node/Edge list
    enricher.py                   # Assigns risk attributes to nodes
    graph_store.py                # GraphStore abstraction (networkx v1, Neo4j v2)
    graph_builder.py              # mapper → enricher → graph_store
    graph_serializer.py           # DiGraph ↔ JSON

  analysis/
    static_analyzer.py            # Orchestrates all static detectors
    detectors/
      no_auth_detector.py         # Finds no-auth-required tools/endpoints
      idor_detector.py            # Finds IDOR surfaces
      hitl_gap_detector.py        # Finds HITL triggers with no enforcement path
      injection_surface.py        # Finds SQL-injectable / SSRF-possible nodes
      privilege_path.py           # Finds low→high privilege escalation paths
      cross_tenant.py             # Finds cross-tenant data access paths
      prompt_injection_surface.py # External data feeding into system prompts

  policy/
    parser.py                     # Cognitive Policy Markdown → CognitivePolicy model
    validator.py                  # Lint for completeness, ambiguity
    checker.py                    # Cross-check policy against SBOM
    compliance/
      owasp_mapper.py             # Finding → OWASP LLM Top 10 ref
      nist_mapper.py              # Finding → NIST AI RMF ref (v1.5)
      eu_ai_act_mapper.py         # Finding → EU AI Act ref (v1.5)

  redteam/
    scenarios/
      scenario_types.py           # AttackScenario, ScenarioType enum
      prompt_injection.py
      tool_abuse.py
      privilege_escalation.py
      pre_scorer.py
      generator.py                # Orchestrates all generators + pre-scoring
    agents/
      base_agent.py               # Abstract Observe→Decide→Execute→Evaluate→Update
      scan_state.py               # In-process ScanState dataclass (no Redis)
      recon_agent.py
      injection_agent.py
      tool_abuse_agent.py
      exfiltration_agent.py
      persistence_agent.py        # Disabled in ci profile
    executor/
      executor.py                 # AttackExecutor: sequential phase runner
      orchestrator.py             # Orchestrates full scan: graph → scenarios → executor
      chain_assembler.py          # ExploitStep list → ExploitChain DAG
    target/
      client.py                   # TargetAppClient: httpx adversarial HTTP client
      canary.py                   # CanaryConfig loader + response scanner
      session.py                  # Session management for multi-turn attacks
      action_logger.py
    policy_engine/
      evaluator.py                # Runs all detectors against agent response trace
      detectors/
        topic_boundary.py         # topic_boundary_breach (2-tier: restricted/out-of-scope)
        restricted_action.py      # restricted_action_executed
        hitl_bypass.py            # HITL_bypassed
    risk_engine/
      severity_scorer.py
      risk_scorer.py              # Severity-weighted aggregate score
      compliance_mapper.py        # attack_technique → ComplianceRef
      remediation_generator.py    # Template-based fix suggestions

  output/
    sarif_generator.py            # Findings → SARIF 2.1.0
    markdown_generator.py         # Findings → developer-readable Markdown
    json_generator.py

  db/
    local.py                      # SQLite async engine (default: ~/.nuguard/nuguard.db)
    postgres.py                   # Postgres async engine (optional: DATABASE_URL)
    migrations/
      001_initial.sql

  models/                         # Pydantic data models (shared across all capabilities)
    attack_graph.py
    exploit_chain.py
    finding.py
    policy.py
    sbom.py
    scan.py

  common/                         # Shared utilities — used by sbom, analyze, policy, redteam
    llm_client.py                 # LiteLLM wrapper; template fallback when no API key
    logging.py                    # Structured logger (JSON in CI, human-readable locally)
    errors.py                     # Shared exception hierarchy (NuGuardError, ScanError, …)
    http.py                       # Shared httpx client factory (timeouts, retries, UA header)

  config.py                       # Config resolution: nuguard.yaml → env vars → defaults (pydantic-settings + PyYAML)
```

### 3.2 LLM Client

All LLM calls go through a single `LLMClient` wrapper in `common/llm_client.py`, shared across all capabilities (SBOM enrichment, static analysis, redteam payload generation). It follows the same LiteLLM pattern as the absorbed Xelo codebase:

```python
class LLMClient:
    model: str      # from LITELLM_MODEL env var; default: gemini/gemini-2.0-flash
    api_key: str    # from LITELLM_API_KEY env var; None = template mode

    async def complete(self, prompt: str, **kwargs) -> str:
        if self.api_key is None:
            return self._canned_response(prompt)   # template-based fallback
        return await litellm.acompletion(model=self.model, ...)
```

SBOM generation also supports optional LLM enrichment (via `AiSbomConfig(enable_llm=True, llm_model=...)`) using the same env var. No LLM key = template-based attack payloads and un-enriched SBOM still work.

### 3.3 Unified Node and Edge Taxonomy

Since nuguard-oss subsumes Xelo, the attack graph adopts the Xelo SBOM node and edge types directly. There is no translation layer — `graph/mapper.py` only filters out non-attack nodes and enriches surviving nodes with risk attributes from SBOM metadata.

**Nodes**

| Type | Attack graph role |
|---|---|
| `AGENT` | Attack node — entry point for injection, escalation |
| `TOOL` | Attack node — abuse, unauthorized invocation |
| `API_ENDPOINT` | Attack node — IDOR, injection, auth bypass |
| `DATASTORE` | Attack node — exfiltration, poisoning; `datastore_type` attribute (`vector`, `relational`, `kv`, `knowledge_base`) distinguishes subtypes |
| `PROMPT` | Attack node — injection surface; `injection_risk_score` from SBOM |
| `AUTH` | Attack node — bypass, credential theft |
| `GUARDRAIL` | Attack node — bypass is an explicit finding type; detectors flag paths that skip it |
| `PRIVILEGE` | Attack node — escalation paths traverse it; `privilege_scope` attribute (`db_write`, `filesystem_write`, `code_execution`, etc.) |
| `MODEL`, `FRAMEWORK`, `IAM`, `CONTAINER_IMAGE`, `DEPLOYMENT` | Indexed from SBOM for context; not traversed as attack nodes in v1 |

**Edges**

| Type | Meaning | Notes |
|---|---|---|
| `CALLS` | Agent or tool invokes another tool or API endpoint | |
| `ACCESSES` | Agent or tool reads from / writes to a datastore | Add `access_type: read\|write\|readwrite` attribute (inferred from SBOM metadata) |
| `USES` | Agent uses a model | Kept; traversal skips `MODEL` nodes |
| `PROTECTS` | Guardrail covers an agent or tool | Detectors flag `CALLS` paths that lack a `PROTECTS` edge from a `GUARDRAIL` |
| `DEPLOYS` | — | Dropped from attack graph |

This replaces the previous NuGuard-specific edge set (`INVOKES`, `READS`, `WRITES`, `CALLS_ENDPOINT`, `EXECUTES`, `OWNS`). Read/write distinction is preserved via `access_type` on `ACCESSES` edges.

### 3.4 Data Flow

Commands can be run individually (for granular control) or via `nuguard scan` (which orchestrates them in sequence). `nuguard.yaml` is the handoff artifact between steps — each command reads the SBOM path, policy path, and target URL from it automatically.

```
                    ┌─────────────────────────────────────┐
                    │         nuguard scan                 │
                    │  (orchestrates steps 1–4 in order;  │
                    │   each step writes to --output-dir)  │
                    └──┬──────────┬──────────┬────────────┘
                       │ step 1   │ step 2   │ step 3/4
                       ▼          ▼          ▼
             (individual commands below can also be run directly)

Source Code / Running App
        │
        ▼
┌───────────────────┐
│   SBOM Generator  │  nuguard sbom generate  →  sbom.json
│  (Xelo bundled)   │                              (file handoff)
└────────┬──────────┘
         │ nuguard.yaml picks up sbom path automatically
         ▼
   AI-SBOM JSON + Cognitive Policy
         │
         ▼
┌───────────────────┐
│   SBOM Ingestion  │  nuguard sbom validate/register
└────────┬──────────┘
         │
         ├──────────────────────────────────┐
         │                                  │
         ▼                                  ▼
┌────────────────────┐            ┌──────────────────────┐
│  Attack Graph      │            │  Policy Engine        │
│  Builder           │            │  parser + checker     │
│  (networkx)        │            └──────────┬───────────┘
└────────┬───────────┘                       │
         │                                   │
         ├──────────────────────────┐        │
         │                          │        │
         ▼                          ▼        ▼
┌────────────────────┐    ┌────────────────────────────┐
│  Static Analyzer   │    │   Redteam Executor          │
│  nuguard analyze   │    │   nuguard redteam           │
│  (no app needed)   │    │   → TargetAppClient         │
│                    │    │   → Policy Evaluator         │
└────────┬───────────┘    └──────────────┬─────────────┘
         │                               │
         └──────────────┬────────────────┘
                        │
                        ▼
              ┌──────────────────┐
              │  Risk Engine     │
              │  + Output Layer  │
              │  SARIF / MD / JSON│
              └──────────────────┘
```

### 3.5 Storage

| Mode | Technology | When |
|---|---|---|
| Local (default) | SQLite (`~/.nuguard/nuguard.db`) | Local dev, no infrastructure needed |
| CI / team | Postgres (`DATABASE_URL` env var) | When scan history or shared state is needed |

No Redis. No Neo4j. The attack graph lives in-process for the lifetime of a scan; findings persist to SQLite/Postgres.

### 3.6 Configuration

Configuration is resolved in this priority order (highest wins):

```
CLI flags  >  nuguard.yaml  >  environment variables  >  built-in defaults
```

**`nuguard.yaml`** — project-level config file, checked into the repo alongside the SBOM and policy. Loaded automatically when present in the current directory, or via `--config ./path/to/nuguard.yaml`.

```yaml
# nuguard.yaml

sbom: ./app.sbom.json               # path to AI-SBOM (or use `source` to generate)
source: ./                          # generate SBOM from source (alternative to sbom)
policy: ./cognitive-policy.md

llm:
  model: gemini/gemini-2.0-flash    # any LiteLLM model string
  # api_key: loaded from LITELLM_API_KEY env var — never put keys in this file

database:
  # url: loaded from DATABASE_URL env var
  # omit for SQLite default at ~/.nuguard/nuguard.db

redteam:
  target: http://localhost:3000
  target_endpoint: /api/chat        # agent endpoint path (default: / or from SBOM)
  auth_header: Authorization: Bearer ${TARGET_TOKEN}   # ${VAR} expands env vars
  canary: ./canary.json
  profile: ci                       # ci | full
  scenarios:                        # omit to run all
    - prompt-injection
    - tool-abuse
    - privilege-escalation
  min_impact_score: 0.0

analyze:
  min_severity: medium              # critical | high | medium | low

output:
  format: markdown                  # markdown | sarif | json
  fail_on: high                     # severity threshold for non-zero exit
  sarif_file: ./nuguard-results.sarif
```

Secrets are never stored in `nuguard.yaml`. Use `${ENV_VAR}` interpolation for any value that would otherwise be a secret — nuguard expands it at runtime from the environment.

**Environment variables** (fallback when not set in `nuguard.yaml`):

```bash
LITELLM_MODEL=gemini/gemini-2.0-flash
LITELLM_API_KEY=...
DATABASE_URL=postgresql+asyncpg://...   # omit for SQLite default
TARGET_TOKEN=...                         # referenced via ${TARGET_TOKEN} in nuguard.yaml
```

The `nuguard.yaml` and `cognitive-policy.md` files together form the complete security configuration for a project and are the primary artifacts committed to source control.

---

## 4. GitHub Actions Integration

A single GitHub Action covers all four capabilities. The calling workflow starts the AI application before the action runs.

### 4.1 Action Definition (`nuguardai/nuguard-action@v1`)

```yaml
name: 'NuGuard AI Security Scan'
description: 'SBOM generation, static analysis, policy compliance, and red-team testing for agentic AI'
inputs:
  sbom:
    required: false
    description: 'Path to Xelo AI-SBOM file (if omitted, nuguard generates one from --source)'
  source:
    required: false
    description: 'Path to application source for SBOM generation (alternative to providing sbom)'
  policy:
    required: false
    description: 'Path to Cognitive Policy Markdown file'
  target:
    required: false
    description: 'URL of running AI application (required for redteam capability)'
  canary:
    required: false
    description: 'Path to canary JSON file for exfiltration detection'
  capabilities:
    required: false
    default: 'analyze,policy,redteam'
    description: 'Comma-separated list: sbom, analyze, policy, redteam'
  profile:
    required: false
    default: 'ci'
    description: 'ci (fast, no persistence phase) | full'
  fail-on:
    required: false
    default: 'high'
    description: 'Minimum severity for non-zero exit: critical | high | medium'
  sarif-output:
    required: false
    default: 'true'
    description: 'Upload SARIF results to GitHub Security tab'
runs:
  using: 'docker'
  image: 'docker://ghcr.io/nuguardai/nuguard:latest'
```

### 4.2 Example Workflow

```yaml
name: NuGuard AI Security
on: [pull_request]

jobs:
  nuguard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # Start AI application (developer's own step)
      - name: Start AI application
        run: |
          docker compose up -d
          npx wait-on http://localhost:3000/health

      # Seed canary data
      - name: Seed canary data
        run: |
          docker run --network host ghcr.io/nuguardai/nuguard:latest \
            seed --target http://localhost:3000 --seed-file ./canary.json \
            --output-canary /tmp/canary-result.json

      - uses: nuguardai/nuguard-action@v1
        with:
          sbom: ./app.sbom.json        # or omit and provide source: ./ for auto-generation
          policy: ./cognitive-policy.md
          target: http://localhost:3000
          canary: /tmp/canary-result.json
          capabilities: analyze,policy,redteam   # maps to nuguard scan --steps
          profile: ci
          fail-on: high
          sarif-output: true
        env:
          LITELLM_API_KEY: ${{ secrets.LITELLM_API_KEY }}   # optional

      # Equivalent direct CLI invocation (when not using the Action):
      # nuguard scan \
      #   --from-sbom ./app.sbom.json \
      #   --policy ./cognitive-policy.md \
      #   --target http://localhost:3000 \
      #   --steps analyze,policy,redteam \
      #   --fail-on high \
      #   --output-dir ./nuguard-reports
```

### 4.3 SARIF Upload

When `sarif-output: true`, the action uploads results from all capabilities to the GitHub Security tab via `github/codeql-action/upload-sarif`. Static analysis findings and redteam findings appear together in one unified view.

---

## 5. Container Image

Single container image that runs all capabilities:

```dockerfile
FROM python:3.12-slim
RUN pip install nuguard
ENTRYPOINT ["nuguard"]
```

**Usage:**

```bash
# Generate SBOM from source
docker run -v $(pwd):/work ghcr.io/nuguardai/nuguard:latest \
  sbom generate --source /work --output /work/app.sbom.json

# Run static analysis
docker run -v $(pwd):/work ghcr.io/nuguardai/nuguard:latest \
  analyze --sbom /work/app.sbom.json --format sarif

# Run full pipeline against local app (host networking)
docker run --network host -v $(pwd):/work ghcr.io/nuguardai/nuguard:latest \
  redteam --sbom /work/app.sbom.json --policy /work/policy.md \
  --target http://localhost:3000

# Persist scan history to Postgres
docker run --network host \
  -e DATABASE_URL=postgresql+asyncpg://user:pass@localhost/nuguard \
  ghcr.io/nuguardai/nuguard:latest \
  redteam --sbom /work/app.sbom.json ...
```

---

## 6. CLI Exit Codes

Consistent across all capabilities:

| Code | Meaning |
|---|---|
| `0` | Clean — no findings at or above `--fail-on` threshold |
| `1` | Findings at or above `--fail-on` threshold |
| `2` | Critical findings (always non-zero, regardless of `--fail-on`) |
| `3` | Scan error (invalid SBOM, unreachable target, infrastructure failure) |

---

## 7. Capability vs Version Plan

| Capability | Component | v1 (OSS launch) | v1.5 | v2 |
|---|---|---|---|---|
| `nuguard.yaml` project config file | `config.py` | ✅ | | |
| Pipeline meta-command | `nuguard scan` | ✅ | | |
| SBOM generation (Xelo bundled) | `nuguard sbom generate` | ✅ | | |
| SBOM validation + registration | `nuguard sbom validate/register` | ✅ | | |
| Attack graph builder | `nuguard/graph/` | ✅ | | Neo4j backend |
| Static SBOM analysis | `nuguard analyze` | ✅ | | |
| Policy parsing + lint | `nuguard policy validate` | ✅ | | |
| Policy ↔ SBOM static check | `nuguard policy check` | ✅ | | |
| OWASP LLM Top 10 mapping | `compliance/owasp_mapper.py` | ✅ | | |
| NIST AI RMF + EU AI Act mapping | `compliance/nist_mapper.py` | | ✅ | |
| Redteam executor (sequential) | `nuguard redteam` | ✅ | | |
| TargetAppClient (HTTP) | `redteam/target/` | ✅ | | |
| Canary exfiltration detection | `redteam/target/canary.py` | ✅ | | |
| SARIF output | `output/sarif_generator.py` | ✅ | | |
| GitHub Actions | `nuguardai/nuguard-action@v1` | ✅ | | |
| Signed JSONL trace | `redteam/executor/trace_writer.py` | | ✅ | |
| Application log correlation | | | ✅ | |
| Azure DevOps task | | | ✅ | |
| Redis-backed parallel agent swarm | | | ✅ | |
| Adaptive attack mutation | | | | ✅ |
| Multi-modal attacks | | | | ✅ |

---

## 8. Relationship to Other NuGuard Repos

| Repo | Role |
|---|---|
| `NuGuardAI/xelo` | **Retired.** SBOM generation capability is bundled into `nuguard sbom generate`. The Xelo AI-SBOM schema is maintained inside this repo. |
| `NuGuardAI/nuguard-app` | Commercial product. Uses `nuguard` PyPI package as a library. Adds: web dashboard, hosted service, multi-tenant org management, enterprise SSO, ServiceNow integration, Azure DevOps integration, and advanced static analysis / red-team capabilities. |
| `NuGuardAI/nuguard-oss` | This repo. OSS CLI + GitHub Action + container image. |

**Dependency direction:** `nuguard-app` depends on `nuguard` (OSS package). Not the other way around. The OSS package has zero dependency on `nuguard-app`.

---

## 9. Key Technical Decisions

| Decision | Resolution | Rationale |
|---|---|---|
| Xelo subsumption | SBOM generation bundled in `nuguard sbom generate` | Single install, unified schema ownership, simpler OSS story |
| DB default | SQLite (`~/.nuguard/nuguard.db`) | Zero infrastructure for local use; Postgres optional via `DATABASE_URL` |
| Graph store | networkx in-process (v1) | No graph DB dependency for OSS; Neo4j via abstraction in v2 |
| Agent state | In-process `ScanState` dataclass (v1) | No Redis dependency; Redis-backed parallel swarm deferred to v1.5 |
| LLM dependency | Optional (`LITELLM_API_KEY`) via LiteLLM | Template-based payloads work without a key; LLM improves payload quality |
| Signed traces | Deferred to v1.5 | Adds implementation complexity without blocking core finding quality in v1 |
| Config file | `nuguard.yaml` (project-level) | Single checked-in file replaces long CLI flag lists; secrets via `${ENV_VAR}` interpolation, never stored in file |
| Config priority | CLI flags > `nuguard.yaml` > env vars > defaults | Predictable override chain; CI can override any value via flag without touching the file |
| Container runtime | Single image, no compose required | OSS users shouldn't manage multi-service infra |
| Package manager | `uv` + `pyproject.toml` | Fast, modern, reproducible |
| Python version | 3.12+ | Consistent with redteam-service design |

---

## 10. What This Enables End-to-End

Projects commit a `nuguard.yaml` alongside their SBOM and cognitive policy. `nuguard.yaml` is the "pipe" — each command reads the SBOM path, policy path, and target URL from it automatically. This means `nuguard scan` and the individual commands all share the same config without flag repetition.

```yaml
# nuguard.yaml  (committed to repo)
sbom: ./app.sbom.json
policy: ./cognitive-policy.md

redteam:
  target: http://localhost:3000
  auth_header: Authorization: Bearer ${TARGET_TOKEN}
  canary: ./canary.json
  profile: ci

output:
  format: markdown
  fail_on: high
```

### Quick start (first-time users and CI)

```bash
# One command: generates SBOM, runs static analysis, policy check, redteam
# All config (sbom path, policy, target) is read from nuguard.yaml
nuguard scan

# Static-only (no live app needed)
nuguard scan --steps sbom,analyze,policy

# Against a staging URL (overrides nuguard.yaml target)
nuguard scan --target https://staging.myapp.com --profile full
```

All output artifacts land in `./nuguard-reports/` (or `--output-dir`):

```
nuguard-reports/
├── sbom.json           # AI-SBOM
├── findings.json       # Static analysis findings (NuGuard format)
├── findings.sarif      # Consolidated SARIF (GitHub Code Scanning)
├── report.md           # Human-readable full report
└── policy-report.md    # Policy compliance report
```

### Granular control (advanced users and debugging)

```bash
# 1. Generate SBOM (one-time or on schema change)
nuguard sbom generate --source . --output ./app.sbom.json

# 2. Static analysis — reads sbom from nuguard.yaml
nuguard analyze

# 3. Policy lint and compliance check
nuguard policy validate
nuguard policy check

# 4. Start the app locally
docker compose up -d

# 5. Seed canary data
nuguard seed --seed-file ./canary.json

# 6. Red-team test — all config from nuguard.yaml
nuguard redteam

# 7. Review findings
nuguard report --test-id <id> --format markdown
nuguard findings --test-id <id> --severity critical,high
```

Individual commands can also be chained with `&&` for scripting — earlier failures stop the chain via exit codes:

```bash
nuguard sbom generate && nuguard analyze && nuguard policy check
```

CLI flags override any value in `nuguard.yaml` — useful for CI to point at a staging URL without modifying the file:

```bash
nuguard redteam --target https://staging.myapp.com --profile full
```

All of the above is also available as a single GitHub Actions step in CI.

---

*NuGuard OSS — High-Level Design — March 2026*
