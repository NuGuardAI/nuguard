# NuGuard Analysis - Unified Security Analysis — Phase 1 Plan

**Goal:** Make NuGuard Analysis a single tool that AI developers run to get a complete security picture of their AI application — covering application code, dependencies, containers, and infrastructure — without juggling Trivy, Checkov, Semgrep, and other tools separately.

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
| AI structural checks | NuGuard Analysis-001 through NuGuard Analysis-005 (offline VLA rules) |
| Output | JSON, CycloneDX, SPDX, SARIF (plugin), Markdown (plugin) |
| CI integration | GitHub Actions adapter (parsing) + GHAS upload plugin |

**Gaps to close in Phase 1:**

1. IaC misconfiguration checks (Terraform, K8s, CloudFormation, Bicep)
2. Container image deep scanning (OS packages, secrets, misconfigs)
3. AI-specific code security patterns (prompt injection surfaces, insecure LLM usage)
4. Additional VLA rules covering cloud/DB/container attack surface
5. Unified terminal report aggregating all findings
6. `NuGuard Analysis audit` — one command that does everything
7. CI pipeline gate (exit codes, severity thresholds)

---

## New OSS Tool Integrations

### 1. Checkov (IaC misconfiguration)

- **What it checks:** Terraform, K8s YAML, CloudFormation, Bicep, GCP DM, GitHub Actions — ~1,000+ built-in CIS/NIST checks covering encryption, IAM least-privilege, network exposure, secret handling, logging.
- **Why not reinvent:** Checkov has 5+ years of cloud-specific rules maintained by the community. NuGuard Analysis's IaC adapters parse for AI-specific signals; Checkov covers the cloud hygiene baseline.
- **Integration model:** `checkov` toolbox plugin wraps the `checkov` CLI, passes the target directory, parses JSON output, maps findings to NuGuard Analysis severity levels, and emits a `ToolResult`.
- **Output mapping:**

  | Checkov severity | NuGuard Analysis severity |
  |---|---|
  | CRITICAL | CRITICAL |
  | HIGH | HIGH |
  | MEDIUM | MEDIUM |
  | LOW | LOW |

- **Graceful degradation:** If `checkov` binary is not found, emit a warning with install instructions and return `status="skipped"`.

### 2. Trivy (container + filesystem scanning)

- **What it checks:** OS package vulnerabilities in local Dockerfiles and built images, secrets embedded in layers, IaC misconfigs (complements Checkov), language package manifests.
- **Why Trivy over Grype here:** Grype is already used for dependency SBOMs. Trivy adds OS-layer vuln scanning, secret scanning in Dockerfiles/images, and config scanning — things Grype does not cover. They are complementary.
- **Integration model:** `trivy` toolbox plugin wraps the `trivy` CLI in `fs` mode (for local path, no daemon needed), parses JSON output, maps findings to NuGuard Analysis severity. Can also be invoked in `image` mode if a built image tag is supplied via config.
- **Modes exposed:**

  | Mode | Trigger | What scans |
  |---|---|---|
  | `fs` (default) | target directory | Dockerfile configs, manifests, embedded secrets |
  | `image` | `--config image=<ref>` | Built container image (OS + packages + secrets) |

- **Graceful degradation:** If `trivy` binary is not found, warn and skip. Grype remains active for dep vulns.

### 3. Semgrep (code security patterns)

- **What it checks:** Python and TypeScript code for AI-specific security anti-patterns using custom rules plus community rule packs.
- **Why Semgrep:** Language-agnostic, fast, rule-as-code (YAML), runs offline, first-class support for Python and TypeScript.
- **Integration model:** `semgrep` toolbox plugin wraps the `semgrep` CLI, targeting the scan directory with a bundled NuGuard Analysis rule pack (see new rules below) plus optionally `p/python` and `p/owasp-top-ten` from the registry.
- **Bundled rule pack location:** `src/NuGuard Analysis/toolbox/semgrep_rules/ai-security.yaml`
- **Graceful degradation:** If `semgrep` binary is not found, warn and skip. Built-in VLA rules still fire.

---

## New NuGuard Analysis Rules (NuGuard Analysis-006 through NuGuard Analysis-015)

All rules are deterministic, offline, derived from the SBOM graph — no new tool required to fire.

| Rule ID | Severity | Title | Detection Logic |
|---|---|---|---|
| **NuGuard Analysis-006** | HIGH | Unencrypted datastore containing PII/PHI | DATASTORE node with PII or PHI data classification AND `encryption_at_rest=false` or no encryption metadata |
| **NuGuard Analysis-007** | HIGH | Missing auth on external AI API endpoint | API_ENDPOINT nodes without an AUTH edge and no guardrail — catches unauthenticated model inference endpoints |
| **NuGuard Analysis-008** | MEDIUM | Agent with internet access but no output guardrail | AGENT node with outbound API_ENDPOINT edge and no GUARDRAIL node reachable in graph |
| **NuGuard Analysis-009** | HIGH | Overly permissive IAM role for AI workload | IAM node with wildcard action (`"*"`) or admin-level role attached to a DEPLOYMENT node that hosts an AGENT or MODEL |
| **NuGuard Analysis-010** | MEDIUM | No network policy for AI workload in K8s | DEPLOYMENT node with `iac_format=kubernetes` and no NetworkPolicy evidence in the same namespace |
| **NuGuard Analysis-011** | HIGH | LLM model weight loaded from untrusted registry | MODEL node where source URL is not `huggingface.co`, `ollama.ai`, or an allowlisted private registry — and has no checksum/digest evidence |
| **NuGuard Analysis-012** | MEDIUM | Container image using `latest` tag | CONTAINER_IMAGE node where `image_tag == "latest"` or tag is absent — prevents reproducible deployments |
| **NuGuard Analysis-013** | LOW | AI workload missing health check | DEPLOYMENT or CONTAINER_IMAGE node without `has_health_check=true` |
| **NuGuard Analysis-014** | MEDIUM | Multiple AI agents sharing a single datastore with no access isolation | 2+ AGENT nodes with edges to the same DATASTORE and no IAM node differentiating access |
| **NuGuard Analysis-015** | INFO | LLM provider not pinned to a specific model version | MODEL node where `model_name` ends in a floating alias (e.g., `gpt-4`, `claude-3-opus`) rather than a versioned/dated variant |

### Bundled Semgrep Rules (`ai-security.yaml`)

Rules targeting Python and TypeScript code, not already caught by SBOM-level checks:

| Rule ID | Severity | Pattern | Example |
|---|---|---|---|
| `NuGuard Analysis.prompt-injection-surface` | HIGH | User input flows directly into prompt template without sanitization | `prompt = f"You are... {user_input}"` |
| `NuGuard Analysis.llm-output-exec` | CRITICAL | LLM output passed to `eval()`, `exec()`, `subprocess`, or `child_process` | `exec(llm_response)` |
| `NuGuard Analysis.hardcoded-api-key` | CRITICAL | API key/token literal assigned to variable matching `*_key`, `*_token`, `*_secret` | `openai_api_key = "sk-..."` |
| `NuGuard Analysis.insecure-deserialization` | HIGH | `pickle.loads` or `yaml.load` applied to untrusted data (model weights, agent state) | `pickle.loads(agent_memory)` |
| `NuGuard Analysis.unvalidated-tool-call` | MEDIUM | Agent tool call result used directly without validation | `result = tool.run(query); db.insert(result)` |
| `NuGuard Analysis.missing-timeout-llm` | LOW | LLM client instantiation without `request_timeout` or `timeout` | `ChatOpenAI(model=...) # no timeout` |
| `NuGuard Analysis.world-readable-model-store` | MEDIUM | File permission `0o777` or `chmod 777` on model weight paths | `os.chmod(model_path, 0o777)` |

---

## New Command: `NuGuard Analysis audit`

A single command that replaces running `NuGuard Analysis scan` + multiple `NuGuard Analysis plugin run` invocations manually.

### Invocation

```bash
# Full audit, all checks, terminal + markdown + SARIF output
NuGuard Analysis audit ./my-ai-app --output-dir ./security-reports

# CI mode: fail on any HIGH or CRITICAL
NuGuard Analysis audit ./my-ai-app --fail-on HIGH --output-dir ./reports

# Skip tools not installed (default behavior)
NuGuard Analysis audit ./my-ai-app --skip-missing-tools

# Only run specific checks
NuGuard Analysis audit ./my-ai-app --checks iac,container,deps

# With container image scan
NuGuard Analysis audit ./my-ai-app --container-image myapp:latest
```

### Execution Sequence

```
NuGuard Analysis audit <path>
│
├─ Phase 1: SBOM generation
│   └─ NuGuard Analysis scan <path> → sbom.json (internal)
│
├─ Phase 2: AI structural checks (offline, always)
│   ├─ VLA rules NuGuard Analysis-001..015
│   └─ Atlas graph checks (ATLAS-NC-001..004)
│
├─ Phase 3: Dependency vulnerability scan
│   ├─ OSV (always, network)
│   └─ Grype (if installed)
│
├─ Phase 4: IaC misconfiguration (if checkov installed)
│   └─ Checkov against IaC files in target dir
│
├─ Phase 5: Container security (if trivy installed)
│   ├─ trivy fs <path> — Dockerfile + secret scanning
│   └─ trivy image <ref> — if --container-image provided
│
├─ Phase 6: Code security (if semgrep installed)
│   ├─ Bundled NuGuard Analysis AI rules
│   └─ p/python (optional, --semgrep-community)
│
└─ Phase 7: Report generation
    ├─ Terminal summary table (always)
    ├─ sbom.json (always)
    ├─ findings.sarif (always)
    ├─ report.md (always)
    └─ Exit code (0 = clean, 1 = findings at threshold, 2 = tool error)
```

### Output Directory Layout

```
./security-reports/
├── sbom.json          # Full NuGuard Analysis SBOM
├── findings.sarif     # Consolidated SARIF 2.1.0 (all tools)
├── report.md          # Full markdown report
└── checkov/           # Raw Checkov JSON (if run)
    └── results.json
```

### CI Exit Codes

| Code | Meaning |
|---|---|
| `0` | No findings at or above threshold (clean) |
| `1` | One or more findings at or above `--fail-on` threshold |
| `2` | Audit tool error (scan failed, invalid path, etc.) |

Default `--fail-on` is `HIGH` (configurable). `INFO` and `LOW` never trigger failures by default.

---

## New Plugin: `terminal` (Unified Terminal Report)

A new toolbox plugin that renders a structured, colored summary to stdout using only the stdlib (no `rich` dependency — plain ANSI codes to keep the install lightweight).

**Output sections:**
1. **Header** — scan target, timestamp, NuGuard Analysis version
2. **Finding summary** — table: Tool | CRITICAL | HIGH | MEDIUM | LOW | INFO
3. **Top findings** — top 10 by severity, with rule ID, title, affected component, file:line where available
4. **Tool status** — which tools ran, which were skipped (with install hint)
5. **Next steps** — links to docs for the most impactful finding categories

---

## Updated SARIF Output

The existing `sarif` plugin exports only VLA findings. The updated version must:

- Accept a `findings` list from all tools (VLA, Checkov, Trivy, Semgrep)
- Map each tool to its own SARIF `run` with its own `driver` (name, version, rules)
- Preserve `artifactLocation` (file path) and `region` (line numbers) where available
- Emit a single `findings.sarif` consumable by GitHub Code Scanning and VS Code SARIF Viewer

**Multi-run SARIF structure:**

```json
{
  "version": "2.1.0",
  "runs": [
    { "tool": { "driver": { "name": "NuGuard Analysis-vla", ... } }, "results": [...] },
    { "tool": { "driver": { "name": "checkov", ... } }, "results": [...] },
    { "tool": { "driver": { "name": "trivy", ... } }, "results": [...] },
    { "tool": { "driver": { "name": "semgrep", ... } }, "results": [...] }
  ]
}
```

---

## GitHub Actions Integration

### New Bundled Workflow Template

A ready-to-use workflow template at `docs/github-actions/NuGuard Analysis-audit.yml`:

```yaml
name: NuGuard Analysis Security Audit

on:
  push:
    branches: [main, develop]
  pull_request:

jobs:
  NuGuard Analysis-audit:
    runs-on: ubuntu-latest
    permissions:
      security-events: write   # SARIF upload
      contents: read

    steps:
      - uses: actions/checkout@v4

      - name: Install NuGuard Analysis + security tools
        run: |
          pip install NuGuard Analysis[toolbox]
          # Install optional tools (degrade gracefully if missing)
          pip install checkov
          curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
          pip install semgrep

      - name: Run NuGuard Analysis audit
        run: |
          NuGuard Analysis audit . \
            --fail-on HIGH \
            --output-dir security-reports

      - name: Upload SARIF to GitHub Code Scanning
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: security-reports/findings.sarif

      - name: Upload audit artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: NuGuard Analysis-security-reports
          path: security-reports/
```

### Updated Existing GitHub Actions Adapter

The existing `GitHubActionsAdapter` detects OIDC configuration. In Phase 1 it gains:

- Detection of `ACTIONS_RUNNER_DEBUG` secret (leaks debug output publicly)
- Detection of `pull_request_target` with `${{ github.event.pull_request... }}` — common PWSH injection vector
- Detection of workflows writing to `GITHUB_ENV` from untrusted input — env injection pattern

These map to new NuGuard Analysis-rule IDs (NuGuard Analysis-016 through NuGuard Analysis-018) and fire without any external tool.

---

## Implementation Milestones

### M1 — New Plugins (Checkov, Trivy, Semgrep)

**Deliverables:**
- `src/NuGuard Analysis/toolbox/plugins/checkov_scanner.py` — wraps `checkov -d <path> -o json`, parses results
- `src/NuGuard Analysis/toolbox/plugins/trivy_scanner.py` — wraps `trivy fs <path> --format json`, parses results
- `src/NuGuard Analysis/toolbox/plugins/semgrep_scanner.py` — wraps `semgrep --config <rules> <path> --json`, parses results
- `src/NuGuard Analysis/toolbox/semgrep_rules/ai-security.yaml` — bundled Semgrep rule pack (7 rules)
- Register all three in `_PLUGIN_REGISTRY` in `cli.py`
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
- `tests/test_toolbox/test_checkov_plugin.py` — mock subprocess, assert finding shape
- `tests/test_toolbox/test_trivy_plugin.py` — mock subprocess, assert finding shape
- `tests/test_toolbox/test_semgrep_plugin.py` — mock subprocess, assert rule hit

---

### M2 — New VLA Rules (NuGuard Analysis-006 through NuGuard Analysis-018)

**Deliverables:**
- Extend `src/NuGuard Analysis/toolbox/plugins/vulnerability.py` with rules NuGuard Analysis-006 through NuGuard Analysis-018
- Each rule: `rule_id`, `severity`, `title`, `description`, `remediation`, `affected_components`
- Regression tests in `tests/test_toolbox/test_vulnerability_rules.py` (extend existing)
- New test fixtures where needed (minimal — prefer extending existing fixture apps)

**Key implementation notes:**
- NuGuard Analysis-009 (overly permissive IAM): traverse `edges` for DEPLOYMENT → IAM relationships; check IAM node `permissions` list for `"*"` or `"admin"`.
- NuGuard Analysis-011 (untrusted model registry): check `NODE.metadata.source_url` against allowlist; default allowlist in config, overridable via `--config trusted_registries=["registry.example.com"]`.
- NuGuard Analysis-014 (shared datastore, no IAM isolation): graph traversal — find all AGENT nodes with edges to the same DATASTORE node, check if distinct IAM nodes gate each path.

---

### M3 — `NuGuard Analysis audit` Command + Terminal Plugin

**Deliverables:**
- New `audit` subcommand in `src/NuGuard Analysis/cli.py`
- `src/NuGuard Analysis/toolbox/plugins/terminal_reporter.py` — ANSI terminal summary (no `rich` dependency)
- `src/NuGuard Analysis/toolbox/plugins/sarif_merged.py` — multi-run SARIF aggregator (extends existing `sarif_exporter.py`)
- `src/NuGuard Analysis/audit.py` — orchestration logic (keep out of `cli.py` for testability)
- `docs/github-actions/NuGuard Analysis-audit.yml` — template workflow

**New CLI flags for `audit`:**

| Flag | Purpose | Default |
|---|---|---|
| `--output-dir` | Directory for all output files | `./NuGuard Analysis-reports` |
| `--fail-on` | Minimum severity that triggers exit code 1 | `HIGH` |
| `--checks` | Comma-separated subset: `vla,deps,iac,container,code` | all |
| `--skip-missing-tools` | Warn instead of error if checkov/trivy/semgrep missing | `true` |
| `--container-image` | Image ref for `trivy image` scan | None |
| `--semgrep-community` | Also run `p/python` and `p/owasp-top-ten` rule packs | `false` |
| `--checkov-frameworks` | Comma-separated Checkov frameworks | `all` |

**Tests:**
- `tests/test_audit.py` — integration test using a test fixture directory with mocked tool outputs; assert output files created, exit code correct

---

### M4 — Enhanced GitHub Actions Adapter

**Deliverables:**
- Extend `GitHubActionsAdapter` in `src/NuGuard Analysis/adapters/iac.py` with 3 new security signals
- New VLA rules NuGuard Analysis-016 through NuGuard Analysis-018 in `vulnerability.py`
- Regression tests using fixture workflow YAML files

**New detection patterns:**

```python
# NuGuard Analysis-016: pull_request_target with untrusted context
PATTERN_PR_TARGET_INJECTION = re.compile(
    r"pull_request_target.*\$\{\{.*github\.event\.pull_request\.", re.DOTALL
)

# NuGuard Analysis-017: GITHUB_ENV write from untrusted input
PATTERN_GITHUB_ENV_INJECTION = re.compile(
    r'echo\s+.*\$\{\{.*\}\}.*>>\s*\$GITHUB_ENV'
)

# NuGuard Analysis-018: ACTIONS_RUNNER_DEBUG secret exposed
PATTERN_DEBUG_SECRET = re.compile(r'ACTIONS_RUNNER_DEBUG')
```

---

### M5 — Documentation

**Deliverables:**
- `docs/unified-security.md` — user guide for `NuGuard Analysis audit`, all new checks, tool requirements
- Update `docs/cli-reference.md` — add `audit` command, all new flags
- Update `docs/getting-started.md` — add quick-start for audit workflow
- Update `README.md` — update the feature overview section
- Update `docs/CHANGELOG.md`

---

## Architecture Diagram

```
NuGuard Analysis audit <path>
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│                    AuditOrchestrator                     │
│  (src/NuGuard Analysis/audit.py)                                     │
└───────┬────────┬────────┬────────┬────────┬─────────────┘
        │        │        │        │        │
        ▼        ▼        ▼        ▼        ▼
  AiSbomExtractor  VLA Rules  Checkov   Trivy    Semgrep
  (existing)    (extended)  (plugin) (plugin) (plugin)
        │        │        │        │        │
        └────────┴────────┴────────┴────────┘
                          │
                   FindingAggregator
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
  TerminalReporter   MergedSarif    MarkdownExporter
  (new plugin)       (updated)      (existing, extended)
```

---

## Dependency Changes

### New optional extras

```toml
# pyproject.toml additions
[project.optional-dependencies]
audit = [
    "NuGuard Analysis[toolbox]",
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

### New `NuGuard Analysis[audit]` install instruction

```bash
pip install "NuGuard Analysis[toolbox]"
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

- `NuGuard Analysis audit ./my-ai-app` produces terminal output, `report.md`, `findings.sarif`, and `sbom.json` from a single command.
- All new VLA rules (NuGuard Analysis-006 through NuGuard Analysis-018) have at least one passing and one failing test case.
- Checkov, Trivy, and Semgrep plugins all degrade gracefully (warn + skip) when the tool binary is absent.
- The bundled GitHub Actions template runs clean on a sample AI repo with no false-positive failures.
- Exit code behavior is correct: `0` for clean, `1` for findings at threshold, `2` for tool error.
- No new mandatory runtime pip dependencies (all three new integrations are optional CLI wrappers).
- `pytest -m "not smoke"` passes with all new tests.
