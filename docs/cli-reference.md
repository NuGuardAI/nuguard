# NuGuard CLI Reference

NuGuard ships a Typer-based CLI covering the full AI application security pipeline: SBOM generation, static analysis, policy compliance, and dynamic red-team testing.

## Top-Level Commands

```text
nuguard init        Create starter project files
nuguard sbom        SBOM generation, validation, and management
nuguard analyze     Static risk analysis from an AI-SBOM
nuguard scan        Unified pipeline: SBOM → analyze → policy → redteam
nuguard policy      Cognitive policy linting and compliance assessment
nuguard redteam     Dynamic adversarial testing against a live AI application

```

---

## `nuguard init`

Creates three starter project files in the current directory (or `--dir`).

| File | Purpose |
|---|---|
| `nuguard.yaml.example` | Fully annotated config template — copy to `nuguard.yaml` |
| `canary.example.json` | Canary seed template for red-team exfiltration detection |
| `cognitive_policy.md` | Blank Cognitive Policy with section headers |

```bash
nuguard init
nuguard init --dir ./my-project
nuguard init --force          # overwrite existing files
```

| Flag | Default | Description |
|---|---|---|
| `--dir`, `-d` | `.` | Directory to write files into |
| `--force` | `false` | Overwrite files that already exist |

---

## `nuguard sbom`

SBOM generation, validation, and management.

### Subcommands

| Subcommand | Description |
|---|---|
| `generate` | Scan source or a remote repo and produce an AI-SBOM JSON |
| `validate` | Validate a JSON file against the bundled AI-SBOM schema |
| `register` | Register an SBOM in the local database (`~/.nuguard/nuguard.db`) |
| `show` | Display a registered SBOM by ID |
| `schema` | Print the bundled `aibom.schema.json` to stdout |
| `plugin` | Run a toolbox plugin or list available plugins |

### `nuguard sbom generate`

```bash
nuguard sbom generate --source ./my-app --output app.sbom.json
nuguard sbom generate --from-repo https://github.com/org/repo --ref main
nuguard sbom generate --source . --llm --format cyclonedx
```

| Flag | Default | Description |
|---|---|---|
| `--source`, `-s` | — | Local source directory to scan |
| `--from-repo` | — | Remote Git URL to clone and scan |
| `--ref` | `main` | Branch, tag, or commit for `--from-repo` |
| `--token` | `$GH_TOKEN` | GitHub token for private repos |
| `--output`, `-o` | `app.sbom.json` | Output file |
| `--llm` / `--no-llm` | off | Enable LLM enrichment of SBOM nodes |
| `--format`, `-f` | `json` | `json` \| `cyclonedx` \| `cyclonedx-ext` \| `markdown` |
| `--config` | `./nuguard.yaml` | Config file path |

### `nuguard sbom validate`

```bash
nuguard sbom validate --file app.sbom.json
```

### `nuguard sbom plugin`

Run a toolbox plugin against an existing SBOM or list available plugins.

```bash
nuguard sbom plugin list

# SARIF findings export
nuguard sbom plugin run sarif_export --sbom app.sbom.json --output results.sarif

# SPDX 3.0.1 JSON-LD export
nuguard sbom plugin run spdx_export --sbom app.sbom.json --output app.spdx.json

# CycloneDX 1.6 standard BOM
nuguard sbom plugin run cyclonedx_export --sbom app.sbom.json --output app.cdx.json

# CycloneDX 1.6 with AI-specific extensions
nuguard sbom plugin run cyclonedx_ext_export --sbom app.sbom.json --output app.cdx-ext.json

# Markdown report
nuguard sbom plugin run markdown_export --sbom app.sbom.json
```

| Plugin name | Output | Description |
|---|---|---|
| `cyclonedx_export` | CycloneDX 1.6 JSON | Standard BOM with xelo:* properties and optional VEX vulnerabilities |
| `cyclonedx_ext_export` | CycloneDX 1.6 JSON | Extended BOM with `modelCard`, `services`, `compositions`, and `nuguard:*` properties |
| `spdx_export` | SPDX 3.0.1 JSON-LD | SPDX 3.0.1 export with `ai_AIPackage`, `dataset_Dataset`, and relationship graph |
| `sarif_export` | SARIF 2.1.0 JSON | Vulnerability findings export for GitHub Code Scanning |
| `markdown_export` | Markdown text | Human-readable SBOM report |
| `dependency_analyze` | JSON | Dependency breakdown and freshness analysis |
| `license_check` | JSON | Dependency licence compliance check |
| `vulnerability` | JSON | Structural + CVE scan (providers: `vela-rules`, `osv`, `grype`, `all`) |

| Flag | Default | Description |
|---|---|---|
| `--sbom` | **required** | SBOM JSON file to pass to the plugin |
| `--output`, `-o` | stdout | Write plugin output to this file |
| `--format`, `-f` | `json` | Format hint passed to the plugin (`json` \| `markdown`) |

---

## `nuguard analyze`

Static risk analysis from an AI-SBOM — no running application required.

Runs up to six scanners in sequence: NuGuard AI structural rules (NGA), OSV CVE lookup, Grype, Checkov (IaC), Trivy (containers), and Semgrep (source). Each scanner is silently skipped when its binary is absent or has nothing to scan.

NGA rules are always on and produce findings in the "NGA" family designed to identify structural risks in the AI stack of the application.

```bash
nuguard analyze --sbom app.sbom.json
nuguard analyze --sbom app.sbom.json --format sarif --output results.sarif
nuguard analyze --sbom app.sbom.json --source . --llm
nuguard analyze --sbom app.sbom.json --no-grype --no-trivy --min-severity high
```

| Flag | Default | Description |
|---|---|---|
| `--sbom` | **required** | Path to AI-SBOM JSON |
| `--format`, `-f` | `markdown` | `markdown` \| `sarif` \| `json` |
| `--min-severity` | `medium` | Minimum severity to include: `critical` \| `high` \| `medium` \| `low` \| `info` |
| `--source`, `-s` | — | Source directory for Checkov / Trivy / Semgrep path resolution |
| `--atlas` / `--no-atlas` | on | MITRE ATLAS native graph checks (NGA-001–019) |
| `--osv` / `--no-osv` | on | OSV dependency CVE scan |
| `--grype` / `--no-grype` | on | Grype CVE scan (requires `grype` on PATH) |
| `--checkov` / `--no-checkov` | on | Checkov IaC scan (requires `checkov` on PATH) |
| `--trivy` / `--no-trivy` | on | Trivy container/fs scan (requires `trivy` on PATH) |
| `--semgrep` / `--no-semgrep` | on | Semgrep AI-security rules (requires `semgrep` on PATH) |
| `--llm` | off | LLM enrichment in the ATLAS annotation pass |
| `--output`, `-o` | stdout | Write report to this file |

**Source path resolution for Checkov:** When `--source` is provided, IaC file paths found in SBOM nodes are resolved relative to that directory. If no IaC nodes exist, Checkov scans the entire source directory. The SBOM `target` field is used as a fallback when `--source` is not set.

---

## `nuguard scan`

Unified pipeline that chains SBOM generation, static analysis, policy check, and optionally red-team into a single command.

```bash
nuguard scan --source .
nuguard scan --source . --steps sbom,analyze
nuguard scan --source . --policy cognitive_policy.md --target http://localhost:3000
nuguard scan --source . --llm --output-dir reports/
```

| Flag | Default | Description |
|---|---|---|
| `--source`, `-s` | `.` | Application source directory |
| `--output-dir`, `-o` | `nuguard-reports` | Directory for all output artifacts |
| `--steps` | `sbom,analyze` | Comma-separated subset: `sbom,analyze,policy,redteam` |
| `--policy` | — | Cognitive Policy Markdown path (required for `policy` / `redteam` steps) |
| `--target` | — | Live app URL for the `redteam` step |
| `--container-image` | — | Container image ref for Trivy image scan (e.g. `myapp:latest`) |
| `--fail-on` | `high` | Exit code 1 when any finding meets this severity |
| `--llm` | off | LLM enrichment in the ATLAS annotation pass |
| `--no-atlas` | — | Skip ATLAS pass |
| `--no-osv` | — | Skip OSV scan |
| `--no-grype` | — | Skip Grype scan |
| `--no-checkov` | — | Skip Checkov scan |
| `--no-trivy` | — | Skip Trivy scan |
| `--no-semgrep` | — | Skip Semgrep scan |

---

## `nuguard policy`

Cognitive policy linting, SBOM cross-checking, and compliance assessment.
Cognitive Policies are human-readable Markdown documents that define guardrails for AI application behavior, architecture, and components. They can be used for documentation, internal governance, or as enforceable policies in CI or runtime gates. `nuguard init` creates a `cognitive_policy.md` template with common sections.

### `nuguard policy validate`

Lint a Cognitive Policy Markdown file for completeness and common mistakes.

```bash
nuguard policy validate --file cognitive_policy.md
```

| Flag | Default | Description |
|---|---|---|
| `--file`, `-f` | **required** | Cognitive Policy Markdown file |

### `nuguard policy check`

Cross-check policy against SBOM, run a compliance framework assessment, or both.

```bash
# Policy vs SBOM gap analysis
nuguard policy check --policy policy.md --sbom app.sbom.json

# Compliance framework assessment
nuguard policy check --sbom app.sbom.json --framework owasp-llm-top10

# Combined with LLM enrichment
nuguard policy check --policy policy.md --sbom app.sbom.json \
  --framework owasp-llm-top10 --llm

# Read paths from nuguard.yaml
nuguard policy check
```

| Flag | Default | Description |
|---|---|---|
| `--policy`, `-p` | — | Cognitive Policy Markdown file |
| `--sbom` | — | AI-SBOM JSON to cross-check against |
| `--config` | `./nuguard.yaml` | Config file path |
| `--framework` | — | `owasp-llm-top10` \| `nist-ai-rmf` \| `eu-ai-act` |
| `--controls` | — | Custom controls JSON file |
| `--format` | `text` | `text` \| `json` |
| `--llm` / `--no-llm` | off | LLM fallback for controls that can't be assessed from SBOM alone |

### `nuguard policy show`

Display a stored cognitive policy by database ID.

---

## `nuguard redteam`

Dynamic adversarial testing against a live AI application. Reads the AI-SBOM to derive an attack surface, generates and executes scenarios, and produces structured findings with OWASP/MITRE mappings.

See [redteam-engine.md](./redteam-engine.md) for a complete description of how the engine works.

```bash
# Basic scan (app already running)
nuguard redteam --sbom app.sbom.json --target http://localhost:8000

# Full scan with policy enforcement and canary detection
nuguard redteam --sbom app.sbom.json --target http://localhost:8000 \
  --policy policy.md --canary canary.json --profile full

# Auto-launch the app then scan
nuguard redteam --sbom app.sbom.json --source ./my-app --launch

# Guided adaptive conversations (requires redteam LLM)
NUGUARD_REDTEAM_LLM_MODEL=openrouter/meta-llama/llama-3.3-70b-instruct \
NUGUARD_REDTEAM_LLM_API_KEY=sk-... \
nuguard redteam --sbom app.sbom.json --target http://localhost:8000 \
  --guided --guided-max-turns 15 --guided-concurrency 2

# Limit to specific attack families
nuguard redteam --sbom app.sbom.json --target http://localhost:8000 \
  --scenarios prompt-injection,data-exfiltration

# CI gate — SARIF output, fail on high+
nuguard redteam --sbom app.sbom.json --target $APP_URL \
  --profile ci --format sarif --output results.sarif --fail-on high
```

| Flag | Default | Description |
|---|---|---|
| `--sbom` | from `nuguard.yaml` | AI-SBOM JSON path |
| `--target` | SBOM discovery | Live application URL (`http://host:port`) |
| `--source` | — | App source directory — used for `.env` loading and `--launch` |
| `--launch` / `--no-launch` | off | Auto-start the app from the SBOM startup command; stop it after the scan. Requires `--source` |
| `--policy` | from `nuguard.yaml` | Cognitive Policy Markdown path |
| `--canary` | from `nuguard.yaml` | Canary JSON file path |
| `--profile` | `ci` | `ci` (impact ≥ 5.0 only) or `full` (all scenarios) |
| `--scenarios` | all | Comma-separated filter: `prompt-injection`, `tool-abuse`, `privilege-escalation`, `data-exfiltration`, `policy-violation`, `mcp-toxic-flow` |
| `--min-impact-score` | `0.0` | Exclude scenarios below this pre-score [0–10] |
| `--guided` / `--no-guided` | on when LLM set | Adaptive multi-turn guided conversations |
| `--guided-max-turns` | `12` | Max turns per guided conversation |
| `--guided-concurrency` | `3` | Parallel guided conversations |
| `--format` | `text` | `text` \| `json` \| `sarif` |
| `--output` | — | Write findings to this file |
| `--fail-on` | `high` | Exit code 2 if any finding meets this severity |
| `--config` | `./nuguard.yaml` | Config file path |

**Target URL resolution order:** `--target` flag → `redteam.target` in `nuguard.yaml` → SBOM-discovered URLs (local → staging → production) → error.

**LLM configuration** (no CLI flags — use env vars or `nuguard.yaml`):

| Env var | YAML key | Description |
|---|---|---|
| `NUGUARD_REDTEAM_LLM_MODEL` | `redteam.llm.model` | Attack payload generation — must be an uncensored model |
| `NUGUARD_REDTEAM_LLM_API_KEY` | `redteam.llm.api_key` | API key for the redteam LLM |
| `NUGUARD_REDTEAM_EVAL_LLM_MODEL` | `redteam.eval_llm.model` | Response evaluation and report generation |
| `NUGUARD_REDTEAM_EVAL_LLM_API_KEY` | `redteam.eval_llm.api_key` | API key for the eval LLM |

---

## Configuration File

All CLI flags can be set in `nuguard.yaml`. Run `nuguard init` to create an annotated template.

```bash
nuguard init            # writes nuguard.yaml.example
cp nuguard.yaml.example nuguard.yaml
```

Priority order: **CLI flags > nuguard.yaml > environment variables > built-in defaults**

Secrets are never stored directly — use `${ENV_VAR}` interpolation:

```yaml
redteam:
  auth_header: "Authorization: Bearer ${TARGET_TOKEN}"
  llm:
    api_key: ${NUGUARD_REDTEAM_LLM_API_KEY}
```

---

## Getting Help

```bash
nuguard --help
nuguard sbom --help
nuguard sbom generate --help
nuguard analyze --help
nuguard scan --help
nuguard policy --help
nuguard policy check --help
nuguard redteam --help
```
