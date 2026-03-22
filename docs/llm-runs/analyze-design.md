# nuguard — Unified Security Analysis — Phase 1 Plan

**Goal:** Make `nuguard scan` a single command that AI developers run to get a complete security picture of their AI application — covering application code, dependencies, containers, and infrastructure — without juggling Trivy, Checkov, Semgrep, and other tools separately.

**Scope:** Phase 1 — purely static analysis, no live cloud connections required.

---

## Current State (What NuGuard SBOM Already Has)

| Domain | Current Coverage |
|---|---|
| AI component detection | 14 Python + 8 TS framework adapters |
| IaC parsing | Terraform, K8s, CloudFormation, Bicep, GCP DM, GitHub Actions |
| Container detection | Dockerfile parsing (FROM, USER, HEALTHCHECK, ARG/ENV secrets) |
| Database detection | SQL, NoSQL, vector, KV, object storage (14+ stores) |
| Dependency vulns | OSV (API) + Grype (CLI) |
| AI structural checks | NGA-001 through NGA-005 (offline NGA rules) |
| Output | JSON, CycloneDX, SPDX, SARIF (plugin), Markdown (plugin) |
| CI integration | GitHub Actions adapter (parsing) + GHAS upload plugin |

**Gaps to close in Phase 1:**

1. IaC misconfiguration checks (Terraform, K8s, CloudFormation, Bicep)
2. Container image deep scanning (OS packages, secrets, misconfigs)
3. AI-specific code security patterns (prompt injection surfaces, insecure LLM usage)
4. Additional NGA rules covering cloud/DB/container/operational attack surface (NGA-006 through NGA-019).
5. Unified terminal report aggregating all findings
6. `nuguard scan` — one command that does everything
7. CI pipeline gate (exit codes, severity thresholds)

---

## New OSS Tool Integrations

### 1. Checkov (IaC misconfiguration)

- **What it checks:** Terraform, K8s YAML, CloudFormation, Bicep, GCP DM, GitHub Actions — ~1,000+ built-in CIS/NIST checks covering encryption, IAM least-privilege, network exposure, secret handling, logging.
- **Why not reinvent:** Checkov has 5+ years of cloud-specific rules maintained by the community. nuguard's IaC adapters parse for AI-specific signals; Checkov covers the cloud hygiene baseline.
- **Integration model:** `checkov` toolbox plugin wraps the `checkov` CLI, passes the target directory, parses JSON output, maps findings to nuguard severity levels, and emits a `ToolResult`.
- **Output mapping:**

  | Checkov severity | nuguard severity |
  |---|---|
  | CRITICAL | CRITICAL |
  | HIGH | HIGH |
  | MEDIUM | MEDIUM |
  | LOW | LOW |

- **Graceful degradation:** If `checkov` binary is not found, emit a warning with install instructions and return `status="skipped"`.

### 2. Trivy (container + filesystem scanning)

- **What it checks:** OS package vulnerabilities in local Dockerfiles and built images, secrets embedded in layers, IaC misconfigs (complements Checkov), language package manifests.
- **Why Trivy over Grype here:** Grype is already used for dependency SBOMs. Trivy adds OS-layer vuln scanning, secret scanning in Dockerfiles/images, and config scanning — things Grype does not cover. They are complementary.
- **Integration model:** `trivy` toolbox plugin wraps the `trivy` CLI in `fs` mode (for local path, no daemon needed), parses JSON output, maps findings to nuguard severity. Can also be invoked in `image` mode if a built image tag is supplied via config.
- **Modes exposed:**

  | Mode | Trigger | What scans |
  |---|---|---|
  | `fs` (default) | target directory | Dockerfile configs, manifests, embedded secrets |
  | `image` | `--config image=<ref>` | Built container image (OS + packages + secrets) |

- **Graceful degradation:** If `trivy` binary is not found, warn and skip. Grype remains active for dep vulns.

### 3. Semgrep (code security patterns)

- **What it checks:** Python and TypeScript code for AI-specific security anti-patterns using custom rules plus community rule packs.
- **Why Semgrep:** Language-agnostic, fast, rule-as-code (YAML), runs offline, first-class support for Python and TypeScript.
- **Integration model:** `semgrep` toolbox plugin wraps the `semgrep` CLI, targeting the scan directory with a bundled nuguard rule pack (see new rules below) plus optionally `p/python` and `p/owasp-top-ten` from the registry.
- **Bundled rule pack location:** `nuguard/sbom/toolbox/semgrep_rules/ai-security.yaml`
- **Graceful degradation:** If `semgrep` binary is not found, warn and skip. Built-in NGA rules still fire.

---
## NGA Rules — Complete Reference

All rules use the `NGA-xxx` prefix (NuGuard AI). Rules are deterministic, offline, derived from the SBOM graph — no external tool required to fire.

### Existing Rules (NGA-001 through NGA-005)

| Rule ID | Severity | Title | Detection Logic |
|---|---|---|---|
| **NGA-001** | CRITICAL | PHI/PII data present while external LLM providers are used | DATASTORE or PROMPT node with PII/PHI classification AND outbound edge to a MODEL node hosted on an external provider (OpenAI, Anthropic, Google, Cohere, etc.) |
| **NGA-002** | HIGH | Insufficient guardrail coverage | Two sub-checks: **(A)** LLM MODEL node with no reachable GUARDRAIL node in the edge graph (HIGH); **(B)** AGENT node with an outbound internet-access edge (external API_ENDPOINT) and no reachable GUARDRAIL node — internet-capable agents without output filtering are especially high-risk. **Note:** NGA-008 is absorbed into this rule. |
| **NGA-003** | HIGH | Secrets exposed as environment variables or no secret store configured | Secrets (`*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`) passed as plaintext env vars in Dockerfile `ENV`/`ARG`, K8s `env:`, or `.env` files checked into source control; no VAULT or SECRETS_MANAGER node in the graph |
| **NGA-004** | HIGH | Container images or K8s workloads running as root | CONTAINER_IMAGE node with `run_as_root=true` or no `USER` directive; K8s pod spec with `runAsNonRoot: false` or no `securityContext` |
| **NGA-005** | LOW | AI workloads deployed without CPU/memory resource limits | DEPLOYMENT node for an AGENT or MODEL workload without resource `requests`/`limits` in pod spec or Compose file |

### New Rules (NGA-006 through NGA-019)

| Rule ID | Severity | Title | Detection Logic |
|---|---|---|---|
| **NGA-006** | HIGH | Unencrypted datastore containing PII/PHI | DATASTORE node with PII or PHI data classification AND `encryption_at_rest=false` or no encryption metadata |
| **NGA-007** | HIGH | Missing authentication on external AI API endpoint | API_ENDPOINT nodes without an AUTH edge and without a reachable GUARDRAIL — catches unauthenticated model inference endpoints exposed externally |
| **NGA-008** | ~~MEDIUM~~ | ~~Agent with internet access but no output guardrail~~ | **Merged into NGA-002 sub-check (B).** This rule ID is retired; detection is now part of the enhanced NGA-002. |
| **NGA-009** | HIGH | Overly permissive IAM role for AI workload | IAM node with wildcard action (`"*"`) or admin-level role attached to a DEPLOYMENT node that hosts an AGENT or MODEL |
| **NGA-010** | MEDIUM | No network policy for AI workload in K8s | DEPLOYMENT node with `iac_format=kubernetes` and no NetworkPolicy evidence in the same namespace |
| **NGA-011** | HIGH | LLM model weight loaded from untrusted registry | MODEL node where source URL is not `huggingface.co`, `ollama.ai`, or an allowlisted private registry — and has no checksum/digest evidence |
| **NGA-012** | LOW | Container image using `latest` tag | CONTAINER_IMAGE node where `image_tag == "latest"` or tag is absent — prevents reproducible deployments |
| **NGA-013** | LOW | AI workload missing health check | DEPLOYMENT or CONTAINER_IMAGE node without `has_health_check=true` |
| **NGA-014** | LOW | Multiple AI agents sharing a single datastore with no access isolation | 2+ AGENT nodes with edges to the same DATASTORE and no IAM node differentiating access |
| **NGA-015** | HIGH | AI application has no audit logging enabled | AGENT node present but no structured audit-log evidence detected: no logging middleware (e.g. `RequestLoggingMiddleware`, `AuditLogHandler`), no `RotatingFileHandler`/`TimedRotatingFileHandler` in Python logging config, no `log_paths` in SBOM summary, and no dedicated audit/observability library (`opentelemetry`, `langfuse`, `arize`, `whylogs`). Audit logs are essential for incident response, compliance (HIPAA, SOC 2), and detecting prompt injection post-hoc. |
| **NGA-016** | HIGH | GitHub Actions: `pull_request_target` with untrusted context injection | Workflow using `pull_request_target` trigger that also references `${{ github.event.pull_request.head... }}` in `run:` steps or env — allows fork PRs to inject code into a privileged context |
| **NGA-017** | HIGH | GitHub Actions: `GITHUB_ENV` written from untrusted input | Workflow step writes to `$GITHUB_ENV` using `echo "KEY=${{ ... }}" >> $GITHUB_ENV` where the value comes from an untrusted source (PR title, body, comment) — allows env variable injection into subsequent steps |
| **NGA-018** | MEDIUM | GitHub Actions: `ACTIONS_RUNNER_DEBUG` secret exposed | `ACTIONS_RUNNER_DEBUG` set to `true` in workflow or referenced as a secret — leaks verbose runner debug output including environment variables and file contents to public workflow logs |
| **NGA-019** | HIGH | Agent pipeline lacks human-in-the-loop approval for high-risk actions | AGENT node with TOOL edges to irreversible/high-impact operations (email send, database write/delete, payment processing, file deletion, external API mutation) AND no HITL/interrupt pattern detected: no `interrupt()` / `interrupt_before` / `interrupt_after` (LangGraph), no `human_input=True` (CrewAI), no `requires_action` handler (OpenAI Assistants), no `HumanApprovalCallbackHandler` (LangChain). Agents executing irreversible actions without a human approval gate are a critical safety risk. |

### Bundled Semgrep Rules (`ai-security.yaml`)

Rules targeting Python and TypeScript code, not already caught by SBOM-level checks:

| Rule ID | Severity | Pattern | Example |
|---|---|---|---|
| `nuguard.prompt-injection-surface` | HIGH | User input flows directly into prompt template without sanitization | `prompt = f"You are... {user_input}"` |
| `nuguard.llm-output-exec` | CRITICAL | LLM output passed to `eval()`, `exec()`, `subprocess`, or `child_process` | `exec(llm_response)` |
| `nuguard.hardcoded-api-key` | CRITICAL | API key/token literal assigned to variable matching `*_key`, `*_token`, `*_secret` | `openai_api_key = "sk-..."` |
| `nuguard.insecure-deserialization` | HIGH | `pickle.loads` or `yaml.load` applied to untrusted data (model weights, agent state) | `pickle.loads(agent_memory)` |
| `nuguard.unvalidated-tool-call` | MEDIUM | Agent tool call result used directly without validation | `result = tool.run(query); db.insert(result)` |
| `nuguard.missing-timeout-llm` | LOW | LLM client instantiation without `request_timeout` or `timeout` | `ChatOpenAI(model=...) # no timeout` |
| `nuguard.world-readable-model-store` | MEDIUM | File permission `0o777` or `chmod 777` on model weight paths | `os.chmod(model_path, 0o777)` |

---

## Meta-Command: `nuguard scan`

A single command that replaces running `nuguard sbom generate` + `nuguard analyze` + `nuguard policy check` + external tool invocations manually. `nuguard scan` is aligned with the design doc's pipeline meta-command.

### Invocation

```bash
# Full scan, all checks, terminal + markdown + SARIF output
nuguard scan --source ./my-ai-app --output-dir ./nuguard-reports

# CI mode: fail on any HIGH or CRITICAL
nuguard scan --source ./my-ai-app --fail-on high --output-dir ./reports

# Static-only (no live app required)
nuguard scan --steps sbom,analyze,policy --source ./my-ai-app

# With container image scan
nuguard scan --source ./my-ai-app --container-image myapp:latest

# Skip tools not installed (default behavior)
nuguard scan --source ./my-ai-app --skip-missing-tools
```

### Execution Sequence

```
nuguard scan --source <path>
│
├─ Step 1: sbom      nuguard sbom generate  → output-dir/sbom.json
│
├─ Step 2: analyze   nuguard analyze        → output-dir/findings.json
│           ├─ NGA rules NGA-001..019 (offline, always, NGA-008 retired/merged)
│           ├─ Atlas graph checks (ATLAS-NC-001..004)
│           ├─ Dependency vuln scan (OSV always, Grype if installed)
│           ├─ IaC misconfiguration (Checkov if installed)
│           ├─ Container security (Trivy if installed)
│           └─ Code security patterns (Semgrep if installed)
│                    output-dir/findings.sarif
│                    output-dir/report.md
│
├─ Step 3: policy    nuguard policy check   → output-dir/policy-report.md
│
└─ Step 4: redteam   nuguard redteam        → output-dir/redteam-report.md
                     (skipped if --target not set and not in nuguard.yaml)
```

### Output Directory Layout

```
./nuguard-reports/
├── sbom.json          # AI-SBOM
├── findings.json      # Static analysis findings (nuguard format)
├── findings.sarif     # Consolidated SARIF 2.1.0 (all tools)
├── report.md          # Human-readable full report
└── policy-report.md   # Policy compliance report
```

### CI Exit Codes

Consistent with the design doc (`§6. CLI Exit Codes`):

| Code | Meaning |
|---|---|
| `0` | Clean — no findings at or above `--fail-on` threshold |
| `1` | Findings at or above `--fail-on` threshold |
| `2` | Critical findings (always non-zero, regardless of `--fail-on`) |
| `3` | Scan error (invalid SBOM, unreachable target, infrastructure failure) |

Default `--fail-on` is `high` (configurable). `INFO` and `LOW` never trigger failures by default.

---

## New Plugin: `terminal` (Unified Terminal Report)

A new toolbox plugin that renders a structured, colored summary to stdout using only the stdlib (no `rich` dependency — plain ANSI codes to keep the install lightweight).

**Output sections:**
1. **Header** — scan target, timestamp, nuguard version
2. **Finding summary** — table: Tool | CRITICAL | HIGH | MEDIUM | LOW | INFO
3. **Top findings** — top 10 by severity, with rule ID, title, affected component, file:line where available
4. **Tool status** — which tools ran, which were skipped (with install hint)
5. **Next steps** — links to docs for the most impactful finding categories

---

## Updated SARIF Output

The existing `sarif_generator.py` (`nuguard/output/sarif_generator.py`) exports only NGA findings. The updated version must:

- Accept a `findings` list from all tools (NGA, Checkov, Trivy, Semgrep)
- Map each tool to its own SARIF `run` with its own `driver` (name, version, rules)
- Preserve `artifactLocation` (file path) and `region` (line numbers) where available
- Emit a single `findings.sarif` consumable by GitHub Code Scanning and VS Code SARIF Viewer

**Multi-run SARIF structure:**

```json
{
  "version": "2.1.0",
  "runs": [
    { "tool": { "driver": { "name": "nuguard-nga", ... } }, "results": [...] },
    { "tool": { "driver": { "name": "checkov", ... } }, "results": [...] },
    { "tool": { "driver": { "name": "trivy", ... } }, "results": [...] },
    { "tool": { "driver": { "name": "semgrep", ... } }, "results": [...] }
  ]
}
```

---

## GitHub Actions Integration

### Bundled Workflow Template

Use the `nuguardai/nuguard-action@v1` action (defined in the design doc `§4`). A ready-to-use workflow template at `docs/github-actions/nuguard-audit.yml`:

```yaml
name: NuGuard AI Security Scan

on:
  push:
    branches: [main, develop]
  pull_request:

jobs:
  nuguard-scan:
    runs-on: ubuntu-latest
    permissions:
      security-events: write   # SARIF upload
      contents: read

    steps:
      - uses: actions/checkout@v4

      - name: Install nuguard + security tools
        run: |
          pip install nuguard
          # Install optional tools (degrade gracefully if missing)
          pip install checkov
          curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
          pip install semgrep

      - uses: nuguardai/nuguard-action@v1
        with:
          source: .
          capabilities: analyze,policy
          fail-on: high
          sarif-output: 'true'
        env:
          LITELLM_API_KEY: ${{ secrets.LITELLM_API_KEY }}   # optional

      - name: Upload SARIF to GitHub Code Scanning
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: nuguard-reports/findings.sarif

      - name: Upload scan artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: nuguard-security-reports
          path: nuguard-reports/
```

### Updated Existing GitHub Actions Adapter

The existing `GitHubActionsAdapter` detects OIDC configuration. In Phase 1 it gains:

- Detection of `ACTIONS_RUNNER_DEBUG` secret (leaks debug output publicly)
- Detection of `pull_request_target` with `${{ github.event.pull_request... }}` — common PWSH injection vector
- Detection of workflows writing to `GITHUB_ENV` from untrusted input — env injection pattern

These map to NGA rule IDs NGA-016 through NGA-018 and fire without any external tool.

---

## Implementation Milestones

### M1 — New Plugins (Checkov, Trivy, Semgrep)

**Deliverables:**
- `nuguard/sbom/toolbox/plugins/checkov_scanner.py` — wraps `checkov -d <path> -o json`, parses results
- `nuguard/sbom/toolbox/plugins/trivy_scanner.py` — wraps `trivy fs <path> --format json`, parses results
- `nuguard/sbom/toolbox/plugins/semgrep_scanner.py` — wraps `semgrep --config <rules> <path> --json`, parses results
- `nuguard/sbom/toolbox/semgrep_rules/ai-security.yaml` — bundled Semgrep rule pack (7 rules)
- Register all three in `_PLUGIN_REGISTRY` in `nuguard/sbom/toolbox/__init__.py`
- Unit tests mocking subprocess calls for each plugin

**Plugin contract (each):**
```python
class CheckovScannerPlugin(ToolPlugin):
    name = "checkov"
    # config keys: path (default: sbom["target"]), frameworks (default: all)
    def run(self, sbom: dict, config: dict) -> ToolResult:
        ...
```

**Tests:**
- `nuguard/sbom/tests/test_toolbox/test_checkov_plugin.py` — mock subprocess, assert finding shape
- `nuguard/sbom/tests/test_toolbox/test_trivy_plugin.py` — mock subprocess, assert finding shape
- `nuguard/sbom/tests/test_toolbox/test_semgrep_plugin.py` — mock subprocess, assert rule hit

---

### M2 — New NGA Rules (NGA-006 through NGA-019, NGA-008 retired)

**Deliverables:**
- Extend `nuguard/sbom/toolbox/plugins/vulnerability.py` with rules NGA-006..007, NGA-009..019 (NGA-008 absorbed into enhanced NGA-002)
- Each rule: `rule_id`, `severity`, `title`, `description`, `remediation`, `affected_components`
- Regression tests in `nuguard/sbom/tests/test_toolbox/test_vulnerability_rules.py` (extend existing)
- New test fixtures where needed (minimal — prefer extending existing fixture apps)

**Key implementation notes:**
- **NGA-002 enhanced**: Two-sub-check implementation. Sub-check A (LLM no guardrail, HIGH) fires when a MODEL node has no reachable GUARDRAIL in the graph. Sub-check B (internet-capable agent no guardrail, HIGH) fires when an AGENT node has an outbound API_ENDPOINT edge and no reachable GUARDRAIL. Both sub-checks emit the same `NGA-002` rule ID with different descriptions.
- **NGA-009** (overly permissive IAM): traverse `edges` for DEPLOYMENT → IAM relationships; check IAM node `permissions` list for `"*"` or `"admin"`.
- **NGA-011** (untrusted model registry): check `NODE.metadata.source_url` against allowlist; default allowlist in config, overridable via `nuguard.yaml` `trusted_registries`.
- **NGA-014** (shared datastore, no IAM isolation): graph traversal — find all AGENT nodes with edges to the same DATASTORE node, check if distinct IAM nodes gate each path.
- **NGA-015** (no audit logging): check SBOM `summary.log_paths` — if empty for a graph containing AGENT nodes, fire. Additionally scan for logging middleware class names and observability library imports in the SBOM's detected frameworks. Severity is HIGH because missing audit logs are a compliance gap (HIPAA §164.312(b), SOC 2 CC7.2) and block incident response.
- **NGA-019** (missing HITL): graph traversal from each AGENT node — collect all reachable TOOL nodes; classify each tool as "irreversible" if its name/description matches patterns (`send_email`, `delete_*`, `write_*`, `execute_sql`, `charge_*`, `create_payment`, etc.); fire if any irreversible tool exists and no HITL pattern is detected in the AGENT's framework metadata (`interrupt`, `human_input`, `requires_action`, `HumanApprovalCallbackHandler`). Severity is HIGH; agents silently executing irreversible actions are a critical safety and liability risk.

---

### M3 — `nuguard scan` Unified Execution + Terminal Plugin

**Deliverables:**
- Extend `nuguard/cli/commands/scan.py` with the unified execution flow (Checkov, Trivy, Semgrep phases)
- `nuguard/sbom/toolbox/plugins/terminal_reporter.py` — ANSI terminal summary (no `rich` dependency)
- `nuguard/output/sarif_generator.py` — extend to support multi-run SARIF (all tools in one file)
- `docs/github-actions/nuguard-audit.yml` — template workflow

**Relevant CLI flags for `nuguard scan` (unified static analysis steps):**

| Flag | Purpose | Default |
|---|---|---|
| `--output-dir` | Directory for all output files | `./nuguard-reports` |
| `--fail-on` | Minimum severity that triggers exit code 1 | `high` |
| `--steps` | Comma-separated subset: `sbom,analyze,policy,redteam` | all except redteam when no target |
| `--skip-missing-tools` | Warn instead of error if checkov/trivy/semgrep missing | `true` |
| `--container-image` | Image ref for `trivy image` scan | None |
| `--semgrep-community` | Also run `p/python` and `p/owasp-top-ten` rule packs | `false` |
| `--checkov-frameworks` | Comma-separated Checkov frameworks | `all` |

**Tests:**
- `tests/test_scan.py` — integration test using a test fixture directory with mocked tool outputs; assert output files created, exit code correct

---

### M4 — Enhanced GitHub Actions Adapter

**Deliverables:**
- Extend `GitHubActionsAdapter` in `nuguard/sbom/extractor/iac_scanners/github_actions.py` with 3 new security signals
- New NGA rules NGA-016 through NGA-018 in `nuguard/sbom/toolbox/plugins/vulnerability.py`
- Regression tests using fixture workflow YAML files

**New detection patterns:**

```python
# NGA-016: pull_request_target with untrusted context
PATTERN_PR_TARGET_INJECTION = re.compile(
    r"pull_request_target.*\$\{\{.*github\.event\.pull_request\.", re.DOTALL
)

# NGA-017: GITHUB_ENV write from untrusted input
PATTERN_GITHUB_ENV_INJECTION = re.compile(
    r'echo\s+.*\$\{\{.*\}\}.*>>\s*\$GITHUB_ENV'
)

# NGA-018: ACTIONS_RUNNER_DEBUG secret exposed
PATTERN_DEBUG_SECRET = re.compile(r'ACTIONS_RUNNER_DEBUG')
```

---

### M5 — Documentation

**Deliverables:**
- `docs/unified-security.md` — user guide for `nuguard scan`, all new checks, tool requirements
- Update `docs/cli-reference.md` — document `scan` command with new flags
- Update `docs/getting-started.md` — add quick-start for unified scan workflow
- Update `README.md` — update the feature overview section
- Update `docs/CHANGELOG.md`

---

## Architecture Diagram

```
nuguard scan --source <path>
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│                    nuguard scan                          │
│  (nuguard/cli/commands/scan.py)                          │
└───────┬────────┬────────┬────────┬────────┬─────────────┘
        │        │        │        │        │
        ▼        ▼        ▼        ▼        ▼
  AiSbomExtractor  NGA Rules  Checkov   Trivy    Semgrep
  (existing)    (extended)  (plugin) (plugin) (plugin)
        │        │        │        │        │
        └────────┴────────┴────────┴────────┘
                          │
                   FindingAggregator
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
  TerminalReporter  sarif_generator   markdown_generator
  (new plugin)       (updated,        (existing, extended)
                     multi-run)
```

---

## Dependency Changes

### New optional extras

```toml
# pyproject.toml additions
[project.optional-dependencies]
toolbox = [
    # checkov, trivy, semgrep are external CLIs — not pip deps
    # but we add them to dev extras for CI
]

dev = [
    # existing dev deps...
    "checkov",      # for integration tests
    "semgrep",      # for integration tests
    # trivy is a binary — installed separately in CI
]
```

No new runtime pip dependencies for the three new plugins — they are subprocess wrappers that degrade gracefully when the tools are absent.

### Install instructions

```bash
pip install nuguard
pip install checkov semgrep          # IaC + code checks (pip)
# Trivy: https://aquasecurity.github.io/trivy/latest/getting-started/installation/
```

---

## Non-Goals for Phase 1

- Live cloud connectivity (no AWS SDK calls, no cloud API auth)
- Compliance report cards (OWASP LLM Top 10 mapping, NIST AI RMF)
- HTML dashboard
- Auto-remediation or PR suggestions
- Custom rule authoring UI
- Windows support for subprocess tools (Trivy/Checkov are Linux/macOS only in Phase 1)

---

## Success Criteria

- `nuguard scan --source ./my-ai-app` produces terminal output, `report.md`, `findings.sarif`, and `sbom.json` from a single command.
- All new NGA rules (NGA-006..007, NGA-009..019; NGA-008 retired/merged into NGA-002) have at least one passing and one failing test case.
- Checkov, Trivy, and Semgrep plugins all degrade gracefully (warn + skip) when the tool binary is absent.
- The bundled GitHub Actions template (`nuguardai/nuguard-action@v1`) runs clean on a sample AI repo with no false-positive failures.
- Exit code behavior is correct: `0` for clean, `1` for findings at threshold, `2` for critical findings, `3` for tool error.
- No new mandatory runtime pip dependencies (all three new integrations are optional CLI wrappers).
- `uv run pytest -m "not smoke"` passes with all new tests.
