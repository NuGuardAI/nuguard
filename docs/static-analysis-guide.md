# NuGuard Static Analysis Guide

NuGuard static analysis works from an AI-SBOM document and does not require a running target application. It produces normalized `Finding` objects that render as Markdown, JSON, or SARIF.

The full analysis pipeline runs:

1. **NGA structural rules** (NGA-001–NGA-018) — NuGuard-native AI security rules
2. **Supply Chain Threat Pack** (NGA-SC-001–NGA-SC-025) — NuGuard-native supply-chain rules
3. **MITRE ATLAS native checks** (ATLAS-NC-001–ATLAS-NC-004) — graph-based checks with ATLAS technique mapping
4. **OSV dependency lookup** — ecosystem CVE lookup against the OSV database
5. **Semgrep AI-security rules** — pattern-based source scanning using bundled AI-security rules
6. Third-party scanners (Grype, Trivy, Checkov) — optional; degrade gracefully when not installed

---

## Quick Start

### 1. Generate an SBOM

```bash
nuguard sbom generate --source . --output app.sbom.json
```

### 2. Run analysis

```bash
nuguard analyze --sbom app.sbom.json
```

### 3. One-command pipeline

```bash
nuguard scan --source . --output-dir nuguard-reports
```

`nuguard scan` generates the SBOM and runs analysis in one step. To include policy and red-team steps:

```bash
nuguard scan --source . --steps sbom,analyze,policy,redteam \
  --policy cognitive_policy.md --target http://localhost:3000 \
  --output-dir nuguard-reports
```

---

## `nuguard analyze` Reference

```bash
nuguard analyze --sbom app.sbom.json
nuguard analyze --sbom app.sbom.json --source .
nuguard analyze --sbom app.sbom.json --min-severity high
nuguard analyze --sbom app.sbom.json --output report.md
nuguard analyze --sbom app.sbom.json --nga                   # NGA rules only (fast)
nuguard analyze --sbom app.sbom.json --no-supply-chain       # skip supply-chain pass
nuguard analyze --sbom app.sbom.json --supply-chain-profile full  # enable entropy+git checks
nuguard analyze --sbom app.sbom.json --config nuguard.yaml   # load per-project settings
```

Key flags:

| Flag | Description |
|---|---|
| `--sbom` | Path to AI-SBOM JSON file (or set `sbom:` in `nuguard.yaml`) |
| `--source` | Source directory for file-based scanners |
| `--nga` | Run only NGA-001–NGA-018 (no supply chain, no ATLAS, no external tools) |
| `--no-supply-chain` | Skip the supply-chain threat pass |
| `--supply-chain-profile` | `ci` \| `standard` \| `full` (default: `standard`); `full` adds git-history checks |
| `--supply-chain-verify` | `off` \| `warn` \| `fail` — artifact registry verification (default: `off`) |
| `--min-severity` | `critical`, `high`, `medium`, `low`, `info` |
| `--format` | `markdown`, `json`, or `sarif` (repeat flag or use comma-separated values for multiple outputs) |
| `--llm` | Enable LLM enrichment for the ATLAS pass |
| `--atlas` / `--no-atlas` | Toggle ATLAS native checks |
| `--osv` / `--no-osv` | Toggle OSV dependency lookup |
| `--semgrep` / `--no-semgrep` | Toggle Semgrep |
| `--output` | Write report to a file. Required when multiple formats are requested; base path expands to per-format files |

---

## NGA Structural Rules (NGA-001–NGA-018)

NuGuard-native, deterministic rules that reason over the SBOM graph. No external tools required. Each rule maps to one or more MITRE ATLAS techniques.

| Rule | Severity | Title | What it checks |
|---|---|---|---|
| NGA-001 | CRITICAL | PII/PHI data handled by external LLM providers | `DATASTORE` data-classification fields × `MODEL` nodes with external providers |
| NGA-002 | HIGH | Insufficient guardrail coverage | `MODEL`/`AGENT` nodes and `AGENT→API_ENDPOINT` edges vs. `GUARDRAIL` nodes and `PROTECTS` edges |
| NGA-003 | HIGH | Secrets or credentials exposed in environment variables | `summary.env_var_keys` for secret-named or high-entropy values |
| NGA-004 | HIGH | Container runs as root | `CONTAINER_IMAGE.runs_as_root` flag |
| NGA-005 | HIGH | PII/PHI stored in unencrypted datastore | `DATASTORE.encryption_at_rest` × `data_classification` |
| NGA-006 | HIGH | API endpoint missing authentication | `API_ENDPOINT` nodes without an `AUTH` edge or `auth_required=true` |
| NGA-007 | HIGH | Overly permissive IAM role | `IAM.permissions` for wildcard (`*`) or admin-level grants |
| NGA-008 | HIGH | Model loaded from untrusted or unverified registry | `MODEL.source_url` and absence of `integrity_hash` / `checksum` |
| NGA-009 | HIGH | No audit logging configured | `instrumentation` fields and `summary.log_paths` |
| NGA-010 | HIGH | GitHub Actions pull_request_target injection risk | `summary.github_actions_content` and `workflow_security_findings` for `pull_request_target` patterns |
| NGA-011 | HIGH | GitHub Actions environment variable injection | Workflow YAML for unsanitised `${{ github.event.* }}` expansion in `env:` blocks |
| NGA-012 | HIGH | Agent pipeline lacks HITL approval for high-risk tool actions | `AGENT` nodes with irreversible `TOOL` edges for HITL gates |
| NGA-013 | MEDIUM | Kubernetes deployment missing NetworkPolicy | `DEPLOYMENT.has_network_policy` and `summary.k8s_network_policy_namespaces` |
| NGA-014 | MEDIUM | GitHub Actions runner debug mode enabled | `ACTIONS_RUNNER_DEBUG` or `ACTIONS_STEP_DEBUG` set to `true` in workflow env |
| NGA-015 | LOW | Container missing resource limits | `CONTAINER_IMAGE.has_resource_limits` |
| NGA-016 | LOW | Container image uses `latest` tag | `CONTAINER_IMAGE.image_tag` for unversioned tags |
| NGA-017 | LOW | Container missing health check | `CONTAINER_IMAGE.has_health_check` |
| NGA-018 | LOW | Shared datastore without IAM isolation | `DATASTORE` nodes accessible across `AGENT`/`TOOL` boundaries without `IAM` coverage |

ATLAS technique annotations appear on each NGA finding under an `atlas` block:

```json
{
  "rule_id": "NGA-006",
  "mitre_atlas_technique": "AML.T0051",
  "atlas": {
    "techniques": [
      {
        "technique_id": "AML.T0051",
        "technique_name": "LLM Prompt Injection",
        "atlas_url": "https://atlas.mitre.org/techniques/AML.T0051"
      }
    ]
  }
}
```

NGA-only mode (fast structural feedback, no external tools):

```bash
nuguard analyze --sbom app.sbom.json --nga
```

To make NGA-only mode the default in a project:

```yaml
# nuguard.yaml
analyze:
  nga_only: true
  min_severity: high
```

---

## Supply Chain Threat Pack (NGA-SC-001–NGA-SC-025)

NuGuard-native rules for AI supply-chain threats. Based on the Miasma/Shai-Hulud June 2026 campaign patterns. No external tools required. Activated automatically during `nuguard analyze` and `nuguard scan`.

Three attack families are covered:

**GitHub Actions publish-path risks** — risks on CI/CD pipelines that publish packages or models.

| Rule | Severity | Trigger |
|---|---|---|
| NGA-SC-001 | HIGH | Publish workflow uses OIDC with an unpinned third-party action |
| NGA-SC-002 | CRITICAL | `pull_request_target` or `workflow_dispatch` can reach a publish step or read secrets |
| NGA-SC-003 | HIGH | Job has `id-token: write` + `contents: write` and checks out mutable or untrusted code |
| NGA-SC-004 | HIGH | CI step runs `npm install -g`, `curl \| bash`, Bun, or a shell download without an integrity pin |
| NGA-SC-005 | CRITICAL | CI step reads `/proc/*/environ`, credential file paths, npm tokens, or cloud credentials |
| NGA-SC-006 | CRITICAL | Publish workflow makes a provenance claim that cannot be tied to a repo, ref, or SHA |

**Package lifecycle scripts** — npm/Python install and build hooks that execute code at install time.

| Rule | Severity | Trigger |
|---|---|---|
| NGA-SC-011 | CRITICAL | `postinstall`/`preinstall` script invokes a network download (curl, wget, node-fetch) |
| NGA-SC-012 | CRITICAL | Lifecycle script pipes to shell (`\| bash`, `\| sh`, `\| node`) |
| NGA-SC-013 | CRITICAL | Lifecycle script downloads and executes Bun (`npx bun`, `bun install`) |
| NGA-SC-014 | CRITICAL | Lifecycle script reads npm tokens, GitHub tokens, cloud credential paths, or `/proc/environ` |
| NGA-SC-015 | HIGH | `setup.py` or `pyproject.toml` build hook makes a network call (urllib, requests, httpx) |
| NGA-SC-016 | HIGH | Lifecycle script body contains `eval`, `Function(`, `atob(`, or a base64 blob |

**AI-agent and developer tool config poisoning** — rules targeting AI coding-agent configs committed to the repository, which execute via tools like Claude Code, Cursor, or Codex.

| Rule | Severity | Trigger |
|---|---|---|
| NGA-SC-007 | HIGH | AI-agent config grants broad shell permission (`Bash(*:*)` or equivalent) |
| NGA-SC-008 | MEDIUM | AI-agent config is repo-controlled and within scope for commit-level tampering |
| NGA-SC-009 | HIGH | `.mcp.json` or agent config references an external or untrusted MCP server |
| NGA-SC-010 | HIGH | AI-agent config enables auto-run or auto-approve mode at repo scope |

**Large payload and entropy checks** (standard and full profiles — SBOM-native, no source directory required):

These rules are evaluated directly from the AI-SBOM. File size and Shannon entropy are captured by the SBOM generator during scanning (`DevToolConfigAdapter`), and minified JS paths are recorded in `ScanSummary.minified_js_files`. No local clone is needed for these checks at analysis time.

| Rule | Severity | Trigger |
|---|---|---|
| NGA-SC-017 | HIGH | File >100 KB inside `.claude/`, `.cursor/`, `.codex/`, or `.gemini/` |
| NGA-SC-018 | HIGH | Shannon entropy > 6.5 bits/byte in a tool config or Markdown file |
| NGA-SC-019 | MEDIUM | Minified single-line JS above 5 KB in any config or doc file |

**Git commit heuristics** (profile `full` only — requires a local git repository with commit history):

| Rule | Severity | Trigger |
|---|---|---|
| NGA-SC-020 | MEDIUM | Commit claims dependency update but only modifies AI-agent config or workflow files |
| NGA-SC-021 | HIGH | `[skip ci]` flag on a security-sensitive file change (workflow, package script, AI config) |
| NGA-SC-022 | HIGH | Workflow or package publish config changed without a matching manifest or lockfile change |

**Dependency integrity**:

| Rule | Severity | Trigger |
|---|---|---|
| NGA-SC-023 | MEDIUM | Dependency declared via a mutable reference: unpinned git URL, tarball URL, or `file:` path |
| NGA-SC-024 | LOW | Declared package not found in any lockfile (potential phantom dependency) |
| NGA-SC-025 | CRITICAL | Package name matches a known-malicious IOC in a loaded threat-intel feed |

### Scan profiles

| Profile | Rules active | Use case |
|---|---|---|
| `ci` | SC-001, SC-002, SC-004, SC-005, SC-007, SC-009–014, SC-025 | Highest-signal checks; fast CI gates |
| `standard` | All CI rules + SC-003, SC-006, SC-008, SC-015–019, SC-023–024 (default) | Full SBOM-native scan; recommended for pre-merge and remote-repo analysis |
| `full` | All standard rules + SC-020–022 | Adds git-history heuristics; requires a local git repository |

```bash
nuguard analyze --sbom app.sbom.json --source . --supply-chain-profile full  # adds SC-020..022 git-history checks
```

### Artifact registry verification (Phase 3, optional)

When `--supply-chain-verify warn` or `--supply-chain-verify fail` is set, NuGuard fetches registry metadata (PyPI JSON API, npm registry) and compares declared lifecycle scripts against the published artifact, without installing anything. Findings use rule IDs `NGA-SC-A01` (artifact/source lifecycle drift) and `NGA-SC-A02` (no provenance attestation).

```bash
nuguard analyze --sbom app.sbom.json --source . --supply-chain-verify warn
```

### Threat-intel feeds

NuGuard ships a bundled feed for the Miasma/Shai-Hulud June 2026 campaign (`nuguard/threat_intel/miasma_2026_06.yaml`). Additional feed YAML files placed in the `nuguard/threat_intel/` directory are automatically loaded. Each feed is a YAML file with a `feed_id` field and lists of `known_malicious_packages`, `suspicious_lifecycle_patterns`, and `suspicious_file_patterns`.

---

## MITRE ATLAS Native Checks (ATLAS-NC-001–ATLAS-NC-004)

Graph-based checks that reason over the SBOM node-edge graph and emit findings with MITRE ATLAS technique annotations.

| Check | Title | ATLAS Techniques |
|---|---|---|
| ATLAS-NC-001 | External ML model without integrity verification | AML.T0010, AML.T0048 |
| ATLAS-NC-002 | Writable datastore reachable by unguarded model/agent | AML.T0020 |
| ATLAS-NC-003 | Model artifact reachable from deployment without auth | AML.T0035 |
| ATLAS-NC-004 | Agent or tool with outbound external API capability | AML.T0036, AML.T0024 |

These run after the NGA pass and share the same `Finding` output model.

Optional LLM enrichment adds narrative context to ATLAS findings:

```bash
nuguard analyze --sbom app.sbom.json --llm
```

---

## OSV Dependency Lookup

NuGuard queries the [OSV.dev](https://osv.dev) API for each package in the SBOM `deps` array. Findings use `advisory_id` values from the OSV ecosystem (e.g. `GHSA-*`, `CVE-*`, `PYSEC-*`).

This check runs in-process and requires no additional tools. Disable it with `--no-osv`.

---

## Semgrep AI-Security Rules

NuGuard ships two bundled Semgrep rule files:

- `nuguard/analysis/plugins/semgrep_rules/ai-security.yaml` — AI application code patterns (prompt injection surfaces, insecure tool parameters, LLM API misuse)
- `nuguard/analysis/plugins/semgrep_rules/supply-chain.yaml` — Supply-chain patterns (npm lifecycle scripts, GitHub Actions injection, obfuscated eval, Claude settings wildcard)

Semgrep must be installed (`pip install semgrep`) for these rules to run. The bundled rules run separately from any system-wide Semgrep configuration. Disable with `--no-semgrep`.

---

## Third-Party Scanners

Grype, Trivy, and Checkov are optional. NuGuard skips them gracefully if not installed.

| Tool | What it adds | Install |
|---|---|---|
| Grype | Package/container CVEs | `curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh \| sh -s -- -b /usr/local/bin` |
| Trivy | Container image and filesystem scanning | `curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \| sh -s -- -b /usr/local/bin` |
| Checkov | Terraform, CloudFormation, and Kubernetes IaC | `pip install checkov` |

Enable or disable each scanner individually:

```bash
nuguard analyze --sbom app.sbom.json --no-grype --no-trivy --no-checkov
```

---

## Severity Filtering

```bash
nuguard analyze --sbom app.sbom.json --min-severity high
```

Accepted values: `critical`, `high`, `medium`, `low`, `info`.

---

## Output Formats

| Format | Best for | Command |
|---|---|---|
| `markdown` | Human review, GitHub comments | `nuguard analyze --sbom app.sbom.json --format markdown` |
| `json` | Automation, downstream processing | `nuguard analyze --sbom app.sbom.json --format json` |
| `sarif` | GitHub code scanning, SIEM integrations | `nuguard analyze --sbom app.sbom.json --format sarif` |

---

## Config File

`--config nuguard.yaml` loads per-project analysis defaults. Relevant `analyze:` fields:

| Field | CLI equivalent | Description |
|---|---|---|
| `min_severity` | `--min-severity` | Minimum severity threshold |
| `nga_only` | `--nga` | Run only NGA-001–NGA-018 |
| `supply_chain_profile` | `--supply-chain-profile` | `ci` \| `standard` \| `full` |
| `supply_chain_verify_artifacts` | `--supply-chain-verify` | `off` \| `warn` \| `fail` |

CLI flags always override config file values.

---

## Related Docs

- [docs/quick-start.md](quick-start.md)
- [docs/cli-reference.md](cli-reference.md)
- [docs/sbom-schema.md](sbom-schema.md)
- [docs/troubleshooting.md](troubleshooting.md)
