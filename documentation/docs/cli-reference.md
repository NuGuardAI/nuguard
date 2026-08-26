# NuGuard CLI Reference

NuGuard ships a Typer-based CLI covering the full AI application security pipeline: SBOM generation, static analysis, policy compliance, behavioral testing, and dynamic red-team testing.

Every command accepts `--help` for its live, in-terminal flag list — this doc is the browsable version, kept in sync with the source.

**Severity legend**, used throughout this doc and in NuGuard's own terminal output:

🔴 `critical`&nbsp;&nbsp;&nbsp;🟠 `high`&nbsp;&nbsp;&nbsp;🟡 `medium`&nbsp;&nbsp;&nbsp;🟢 `low`&nbsp;&nbsp;&nbsp;ℹ️ `info`

---

## Command Index

Jump straight to a command. Grouped by what you're trying to do.

**🚀 Core Pipeline**

| | Command | What it does |
|---|---|---|
| 🧱 | [`nuguard init`](#nuguard-init) | Scaffold a `nuguard.yaml` with auto-detected defaults |
| 📦 | [`nuguard sbom`](#nuguard-sbom) | Generate, validate, and export an AI-SBOM |
| 🔍 | [`nuguard analyze`](#nuguard-analyze) | Static risk analysis from an AI-SBOM — no running app needed |
| 🔁 | [`nuguard scan`](#nuguard-scan) | Unified pipeline: SBOM → analyze, with optional policy/redteam |

**📜 Policy & Compliance**

| | Command | What it does |
|---|---|---|
| 📜 | [`nuguard policy`](#nuguard-policy) | Lint a Cognitive Policy, compile it, and check it against an SBOM or compliance framework |
| ✅ | [`nuguard validate`](#nuguard-validate) | Happy-path capability probes and boundary/policy compliance checks against a live app |

**🎯 Live Testing**

| | Command | What it does |
|---|---|---|
| 🎭 | [`nuguard behavior`](#nuguard-behavior) | Intent-aware behavioral analysis against a live AI application |
| ⚔️ | [`nuguard redteam`](#nuguard-redteam) | Dynamic adversarial testing against a live AI application |
| 🔌 | [`nuguard target`](#nuguard-target) | Verify target connectivity and authentication before scanning |

**🧪 Test-Run Management** ![planned](https://img.shields.io/badge/status-planned-6e7681?style=flat-square)

Not implemented yet — see [Planned Commands](#planned-commands) for what's coming.

| | Command | What it does |
|---|---|---|
| 🌱 | `nuguard seed` | Seed canary data into the target before a scan |
| 📄 | `nuguard report` | Regenerate a report for a completed test run |
| 🔎 | `nuguard findings` | List findings from a completed test run |
| ⏮️ | `nuguard replay` | Deterministically replay a completed test run |

---

<details open>
<summary id="nuguard-init">🧱 <strong><code>nuguard init</code></strong> — scaffold a nuguard.yaml</summary>

<br>

Generates a `nuguard.yaml` config file with sensible defaults for the current project. Auto-detects existing SBOM files, Cognitive Policy docs, and canary seeds, and pre-fills paths so the file is ready to use with minimal editing.

Also creates companion starter files (`canary.example.json`, `cognitive-policy.md`) if they do not already exist.

```bash
nuguard init                                    # write ./nuguard.yaml
nuguard init --path ./config/nuguard.yaml       # write to a specific path
nuguard init --target http://localhost:8080
nuguard init --target http://localhost:8080 --source ./src --force
nuguard init --target http://localhost:8080 --llm   # LLM-drafted cognitive policy
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--path` | `-p` | `./nuguard.yaml` | Output path for `nuguard.yaml`. Accepts a file path or a directory (writes `nuguard.yaml` inside it) |
| `--target` | `-t` | `http://localhost:8080` | URL of the running AI application — sets `target.url` |
| `--source` | `-s` | `./` | Source code directory for SBOM generation — sets `source:` in the config |
| `--force` | `-f` | `false` | Overwrite files that already exist |
| `--llm` / `--no-llm` | — | `false` | Use an LLM to draft `cognitive-policy.md` with concise, app-specific defaults (5–6 allowed and restricted topics each). Requires `LITELLM_API_KEY`. Without this flag a blank template is written instead |
| `--dir` | `-d` | — | *(Legacy, hidden)* Directory to write all starter files into. Prefer `--path` |

> ✨ **Auto-detection:** if an existing SBOM (`*.sbom.json`), policy (`cognitive-policy.md`, `cognitive_policy.md`, `policy.md`, `nuguard-policy.md`), or canary (`canary.json`) is found in the target directory, the generated `nuguard.yaml` pre-fills those paths and the post-init "Next steps" list skips the corresponding steps.

</details>

<details>
<summary id="nuguard-sbom">📦 <strong><code>nuguard sbom</code></strong> — generate, validate, and export an AI-SBOM</summary>

<br>

SBOM generation, validation, and management.

### Subcommands

| Subcommand | Description |
|---|---|
| 🟣 `generate` *(default)* | Scan source or a remote repo and produce an AI-SBOM JSON |
| 🟣 `validate` | Validate a JSON file against the bundled AI-SBOM schema |
| 🟣 `register` | Register an SBOM in the local database (`~/.nuguard/nuguard.db`) |
| 🟣 `show` | Display a registered SBOM by ID |
| 🟣 `schema` | Print the bundled `aibom.schema.json` to stdout |
| 🟣 `plugin` | Run a toolbox plugin or list available plugins |

#### 🟣 `nuguard sbom generate`

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
| `--token` | `$GH_TOKEN` / `$GITHUB_TOKEN` | GitHub token for private repos |
| `--output`, `-o` | `app.sbom.json` | Output file |
| `--llm` / `--no-llm` | off | Enable LLM enrichment of SBOM nodes |
| `--format`, `-f` | `json` | `json` \| `cyclonedx` \| `cyclonedx-ext` \| `markdown` — written alongside the JSON SBOM, not instead of it |
| `--config` | `./nuguard.yaml` | Config file path — supplies `sbom_generation.llm`, `llm.model`, and `llm.api_key` even when `--source` is passed directly |

Either `--source` or `--from-repo` is required (or `source:` in `nuguard.yaml`).

#### 🟣 `nuguard sbom validate`

```bash
nuguard sbom validate --file app.sbom.json
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--file` | `-f` | **required** | SBOM JSON file to validate against the bundled schema |

#### 🟣 `nuguard sbom register` / `nuguard sbom show`

Register an SBOM in NuGuard's local SQLite database (`~/.nuguard/nuguard.db`) and retrieve it later by ID.

```bash
nuguard sbom register --file app.sbom.json
nuguard sbom show --sbom-id <id-printed-by-register>
```

| Command | Flag | Description |
|---|---|---|
| `register` | `--file`, `-f` | SBOM JSON file to register |
| `show` | `--sbom-id` | ID of a previously registered SBOM |

#### 🟣 `nuguard sbom schema`

Print the bundled `aibom.schema.json` JSON Schema to stdout — no flags.

```bash
nuguard sbom schema > aibom.schema.json
```

#### 🟣 `nuguard sbom plugin`

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
| `cyclonedx_export` | CycloneDX 1.6 JSON | Standard BOM with `nuguard:*` properties and optional VEX vulnerabilities |
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

</details>

<details>
<summary id="nuguard-analyze">🔍 <strong><code>nuguard analyze</code></strong> — static risk analysis, no live app needed</summary>

<br>

Static risk analysis from an AI-SBOM — **no running application required.**

Runs up to seven scanners in sequence. External scanners are silently skipped when their binary is absent or there's nothing for them to scan:

| Scanner | Checks | Requires |
|---|---|:---:|
| **NGA** structural rules | AI-specific structural risks (NGA-001–018) | *(built in)* |
| **Supply-chain** threat pack | Lifecycle scripts, CI/CD publish paths, AI-agent config poisoning (NGA-SC-001–025) | *(built in)* |
| **OSV** | Dependency CVE lookup | *(built in)* |
| **Grype** | Package / container CVEs | `grype` on PATH |
| **Checkov** | Infrastructure-as-code misconfigurations | `checkov` on PATH |
| **Trivy** | Container / filesystem vulnerabilities | `trivy` on PATH |
| **Semgrep** | AI-security source-code patterns | `semgrep` on PATH |

NGA rules always run. Use `--nga` to run **only** NGA rules and skip everything else, including the MITRE ATLAS annotation pass.

```bash
nuguard analyze --sbom app.sbom.json
nuguard analyze --sbom app.sbom.json --nga                              # NGA rules only (fast)
nuguard analyze --sbom app.sbom.json --config nuguard.yaml              # load min_severity from config
nuguard analyze --sbom app.sbom.json --format sarif --output results.sarif
nuguard analyze --sbom app.sbom.json --source . --llm
nuguard analyze --sbom app.sbom.json --no-grype --no-trivy --min-severity high
nuguard analyze --sbom app.sbom.json --supply-chain-profile full --source .
nuguard analyze --sbom app.sbom.json --format json --format markdown --output reports/analyze
```

| Flag | Default | Description |
|---|---|---|
| `--sbom` | **required** | Path to AI-SBOM JSON |
| `--config`, `-c` | — | Path to `nuguard.yaml`; supplies `min_severity` and `nga_only` defaults (CLI flags override) |
| `--nga` | off | NGA structural rules only (NGA-001–018); disables every other scanner |
| `--format`, `-f` | `markdown` | `markdown` \| `sarif` \| `json` (repeat flag or use comma-separated values for multiple outputs) |
| `--min-severity` | `medium` | Minimum severity to include: `critical` \| `high` \| `medium` \| `low` \| `info` |
| `--source`, `-s` | — | Source directory for supply-chain, Checkov, Trivy, and Semgrep scans |
| `--atlas` / `--no-atlas` | on | MITRE ATLAS native graph checks |
| `--osv` / `--no-osv` | on | OSV dependency CVE scan |
| `--grype` / `--no-grype` | on | Grype CVE scan (requires `grype` on PATH) |
| `--grype-timeout` | `180` (sec) | Per-invocation timeout for Grype |
| `--grype-retries` | `3` | Retry attempts when Grype times out |
| `--checkov` / `--no-checkov` | on | Checkov IaC scan (requires `checkov` on PATH) |
| `--trivy` / `--no-trivy` | on | Trivy container/fs scan (requires `trivy` on PATH) |
| `--semgrep` / `--no-semgrep` | on | Semgrep AI-security rules (requires `semgrep` on PATH) |
| `--supply-chain` / `--no-supply-chain` | on | Supply-chain threat pack (NGA-SC-001–025) |
| `--supply-chain-profile` | `standard` | `ci` \| `standard` \| `full` — `full` adds git-history checks |
| `--supply-chain-verify` | `off` | Artifact registry verification: `off` \| `warn` \| `fail` |
| `--llm` | off | LLM enrichment in the ATLAS annotation pass |
| `--verbose`, `-v` | off | Show all 18 NGA rules (pass *and* fail) with evidence for why each passed |
| `--output`, `-o` | stdout | Write report to this file. Required when multiple formats are requested; base path expands to per-format files (for example `analyze.json`, `analyze.md`) |
| `--policy` | — | *Reserved* — accepted but not yet wired to a policy check in this command; use [`nuguard policy check`](#nuguard-policy) instead |

**Source path resolution for Checkov:** When `--source` is provided, IaC file paths found in SBOM nodes are resolved relative to that directory. If no IaC nodes exist, Checkov scans the entire source directory. The SBOM `target` field is used as a fallback when `--source` is not set.

**Exit codes**

| Code | Meaning |
|:---:|---|
| `0` | No findings at or above `--min-severity` |
| `1` | One or more findings |
| `2` | Analysis error (SBOM missing, unreadable, or fails schema validation) |

</details>

<details>
<summary id="nuguard-scan">🔁 <strong><code>nuguard scan</code></strong> — the unified pipeline, everything in one command</summary>

<br>

Unified pipeline that chains SBOM generation, static analysis, and optional policy and red-team validation into a single command. The default step list is `sbom,analyze`; add `policy` and `redteam` explicitly when you want those validations to run.

```bash
nuguard scan --source .
nuguard scan --source . --steps sbom,analyze
nuguard scan --source . --steps sbom,analyze,policy,redteam --policy cognitive_policy.md --target http://localhost:3000
nuguard scan --source . --llm --output-dir reports/
nuguard scan --source . --supply-chain-profile full
```

| Flag | Default | Description |
|---|---|---|
| `--source`, `-s` | `.` | Application source directory |
| `--output-dir`, `-o` | `nuguard-reports` | Directory for all output artifacts (`report.md`, `findings.json`, `findings.sarif`) |
| `--steps` | `sbom,analyze` | Comma-separated subset: `sbom,analyze,policy,redteam` |
| `--policy` | — | Cognitive Policy Markdown path (required for the `policy` step; used by `redteam` when supplied) |
| `--target` | — | Live app URL for the `redteam` step; if omitted, NuGuard falls back to an SBOM deployment URL when available |
| `--container-image` | — | Container image ref for Trivy image scan (e.g. `myapp:latest`) |
| `--fail-on` | `high` | Severity that triggers exit code `1`: `critical` \| `high` \| `medium` \| `low` |
| `--llm` | off | LLM enrichment in the ATLAS annotation pass |
| `--no-atlas` | — | Skip ATLAS pass |
| `--no-osv` | — | Skip OSV scan |
| `--no-grype` | — | Skip Grype scan |
| `--no-checkov` | — | Skip Checkov scan |
| `--no-trivy` | — | Skip Trivy scan |
| `--no-semgrep` | — | Skip Semgrep scan |
| `--no-supply-chain` | — | Skip the supply-chain threat pack |
| `--supply-chain-profile` | `standard` | `ci` \| `standard` \| `full` |
| `--supply-chain-verify` | `off` | `off` \| `warn` \| `fail` |

> 💡 **Gotcha:** supplying `--policy` or `--target` does *not* change the step list by itself. Add `--steps sbom,analyze,policy,redteam` to actually run those steps.

**Exit codes**

| Code | Meaning |
|:---:|---|
| `0` | Clean |
| `1` | Findings at or above `--fail-on` |
| `2` | Any critical finding — always non-zero, regardless of `--fail-on` |
| `3` | Scan error (SBOM generation failure, analysis crash, etc.) |

</details>

<details>
<summary id="nuguard-policy">📜 <strong><code>nuguard policy</code></strong> — lint, compile, and check a Cognitive Policy</summary>

<br>

Cognitive policy linting, SBOM cross-checking, and compliance assessment. Cognitive Policies are human-readable Markdown documents that define guardrails for AI application behavior, architecture, and components — usable for documentation, internal governance, or as enforceable policies in CI or runtime gates. `nuguard init` creates a `cognitive-policy.md` template with common sections.

### Subcommands

| Subcommand | Description |
|---|---|
| 🟣 `compile` | Compile a policy Markdown file into structured JSON controls |
| 🟣 `validate` | Lint a policy file for completeness and common mistakes |
| 🟣 `check` | Cross-check a policy against an SBOM and/or a compliance framework |
| 🟣 `show` | Display a stored policy by ID *(not yet functional — see below)* |

#### 🟣 `nuguard policy compile`

Compile a Cognitive Policy Markdown document into a structured JSON controls file. The compiled JSON is consumed by `policy check` and `behavior` for faster, LLM-optional assessment.

```bash
nuguard policy compile --policy cognitive_policy.md --output policy.controls.json
nuguard policy compile --policy policy.md --llm --output policy.controls.json
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--policy` | `-p` | from `nuguard.yaml` | Cognitive Policy Markdown file |
| `--config` | `-c` | `./nuguard.yaml` | Config file path |
| `--llm` / `--no-llm` | — | off | LLM-assisted compilation — extracts nuanced constraints that rule-based parsing may miss |
| `--output` | `-o` | `<policy>.json` alongside the `.md` file | Destination JSON file for compiled controls |

#### 🟣 `nuguard policy validate`

Lint a Cognitive Policy Markdown file for completeness and common mistakes.

```bash
nuguard policy validate --file cognitive_policy.md
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--file` | `-f` | **required** | Cognitive Policy Markdown file |

**Exit codes**

| Code | Meaning |
|:---:|---|
| `0` | No issues |
| `1` | Warnings only |
| `2` | At least one error-level issue |

#### 🟣 `nuguard policy check`

Cross-check policy against SBOM, run a compliance framework assessment, or both.

```bash
# Policy vs SBOM gap analysis
nuguard policy check --policy policy.md --sbom app.sbom.json

# Compliance framework assessment
nuguard policy check --sbom app.sbom.json --framework owasp-llm-top10

# Combined with LLM enrichment
nuguard policy check --policy policy.md --sbom app.sbom.json \
  --framework owasp-llm-top10 --llm

# JSON + Markdown in one run
nuguard policy check --policy policy.md --sbom app.sbom.json \
  --format json --format markdown --output reports/policy-check

# Read paths from nuguard.yaml
nuguard policy check
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--policy` | `-p` | — | Cognitive Policy Markdown file |
| `--sbom` | — | — | AI-SBOM JSON to cross-check against |
| `--config` | — | `./nuguard.yaml` | Config file path |
| `--framework` | — | — | `owasp-llm-top10` \| `nist-ai-rmf` \| `eu-ai-act` |
| `--controls` | — | — | Custom controls JSON file |
| `--format` | — | `text` | `text` \| `json` \| `markdown` (repeat flag or use comma-separated values for multiple outputs) |
| `--output` | `-o` | stdout | Write the assessment report to this file. Required when multiple formats are requested |
| `--llm` / `--no-llm` | — | off | LLM fallback for controls that can't be assessed from SBOM alone |
| `--verbose` | `-v` / `-V` | off | Show all controls, including PASS results with evidence |

#### 🟣 `nuguard policy show` ![not yet functional](https://img.shields.io/badge/status-not_yet_functional-d1242f?style=flat-square)

> ⚠️ `nuguard policy show --policy-id <id>` always reports "not found" — there is currently no write path that saves a policy for later lookup by ID. Use `nuguard policy compile` and read the output file directly instead.

</details>

<details>
<summary id="nuguard-validate">✅ <strong><code>nuguard validate</code></strong> — happy-path and policy-compliance smoke test</summary>

<br>

Validate AI application happy-path behaviour and cognitive policy compliance against a live target. Runs capability probes, happy-path simulations, boundary assertions, and per-turn policy evaluations, then reports a **capability map** (which tools were exercised, whether each call stayed policy-compliant) alongside any findings.

Controlled by `nuguard.yaml`'s `validate.workflows` list — leave it empty to run all five:

| Workflow | What it does |
|---|---|
| `capability_probe` | Confirms each declared tool/capability actually responds |
| `happy_path` | Runs a scripted core-user-journey conversation |
| `policy_compliance` | Per-turn Cognitive Policy compliance check |
| `agent_routing` | Confirms multi-agent delegation routes the way the SBOM declares |
| `boundary_assertion` | Checks that declared refusals hold — see below |

`boundary_assertion` checks are declared explicitly in `nuguard.yaml` under `validate.boundary_assertions`, each with a `name`, an expectation of `refused`, and a `forbid_pattern` the response must not match.

This is a faster, more deterministic sibling to [`nuguard behavior`](#nuguard-behavior) — useful as a quick pass/fail smoke test in CI, where `behavior`'s richer LLM-judged intent alignment isn't needed on every run.

```bash
nuguard validate
nuguard validate --target http://localhost:8000 --policy ./policy.md
nuguard validate -c ./nuguard.yaml --output results.json --fail-on critical
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--config` | `-c` | `./nuguard.yaml` | Config file path |
| `--target` | — | from `nuguard.yaml` (`validate.target`) | Override the target URL |
| `--policy` | — | from `nuguard.yaml` | Cognitive Policy Markdown path |
| `--canary` | — | from `nuguard.yaml` | Path to `canary.json` seed file |
| `--output` | `-o` | stdout | Write findings to this file. Required when multiple formats are requested |
| `--format` | `-f` | `text` | `text` \| `json` \| `markdown` (repeat flag or use comma-separated values for multiple outputs) |
| `--fail-on` | — | `high` | Exit non-zero when any finding meets this severity: `critical` \| `high` \| `medium` \| `low` |
| `--baseline` | — | — | Path to a previous `CapabilityMap` JSON for regression detection |
| `--verbose` / `--no-verbose` | `-v` / `-V` | off | Print detailed per-turn traces |

**Exit codes**

| Code | Meaning |
|:---:|---|
| `0` | Clean |
| `1` | `validate.target` is not set |
| `2` | A finding meets `--fail-on` (or an invalid `--format` was given) |
| `3` | Unexpected runtime error |

</details>

<details>
<summary id="nuguard-behavior">🎭 <strong><code>nuguard behavior</code></strong> — intent-aware behavioral analysis</summary>

<br>

Intent-aware behavioral analysis against a live AI application. Runs static SBOM-policy alignment checks (no running app required) and/or dynamic behavior testing with per-turn LLM judging across five dimensions.

See [behavior-guide.md](./behavior-guide.md) for a full description of the engine.

```bash
# Static + dynamic (default)
nuguard behavior --config nuguard.yaml

# Static-only alignment checks
nuguard behavior --static

# Dynamic-only with override target
nuguard behavior --dynamic --target http://localhost:8090

# Markdown report to file
nuguard behavior --policy ./policy.md --output ./behavior-report.md --format markdown

# JSON + Markdown in one run
nuguard behavior --config nuguard.yaml --format json --format markdown --output ./behavior-report

# CI gate, comparing against a previous run
nuguard behavior --mode static+dynamic --fail-on critical --compare-to ./last-run.json
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--config` | `-c` | `./nuguard.yaml` | Config file path |
| `--mode` | `-m` | `static+dynamic` | `static` \| `dynamic` \| `static+dynamic` |
| `--static` | — | off | Shorthand for `--mode static` |
| `--dynamic` | — | off | Shorthand for `--mode dynamic` |
| `--target` | — | from `nuguard.yaml` | Override `behavior.target` URL |
| `--policy` | — | from `nuguard.yaml` | Cognitive Policy Markdown path |
| `--intent` | — | from SBOM | Override app intent (one-line description) |
| `--canary` | — | from `nuguard.yaml` | Path to `canary.json` seed file |
| `--output` | `-o` | stdout | Write report to this file. Required when multiple formats are requested; base path expands to per-format files |
| `--format` | `-f` | `text` | `text` \| `json` \| `markdown` (repeat flag or use comma-separated values for multiple outputs) |
| `--fail-on` | — | `high` | Exit code `1` when any finding meets this severity: `critical` \| `high` \| `medium` \| `low` |
| `--baseline` | — | — | Path to a previous `BehaviorAnalysisResult` JSON for regression detection |
| `--compare-to` | — | — | Path to a previous behavior report JSON, checked for run-profile comparability before diffing |
| `--strict-report` | — | off | Fail if Markdown report validation finds structural issues |
| `--verbose` / `--no-verbose` | `-v` / `-V` | off | Print detailed per-turn traces |

**Static checks (BA-001 – BA-008)** — deterministic SBOM × policy cross-checks; no running application or LLM required. Covers missing system-prompt controls, unenforced topic boundaries, over-permissioned tool edges, missing rate-limit controls, and more.

**Dynamic judging** — every response is scored 1–5 on five dimensions:

`intent_alignment` · `behavioral_compliance` · `component_correctness` · `data_handling` · `escalation_compliance`

| Score | Verdict |
|:---:|---|
| ≥ 3.5 | ✅ PASS |
| ≥ 2.0 | 🟡 PARTIAL |
| < 2.0 | ❌ FAIL |

**Exit codes**

| Code | Meaning |
|:---:|---|
| `0` / `1` | Per the severity-threshold logic above |
| `2` | Config or setup error |
| `3` | Unexpected runtime error |

</details>

<details>
<summary id="nuguard-redteam">⚔️ <strong><code>nuguard redteam</code></strong> — dynamic adversarial testing</summary>

<br>

Dynamic adversarial testing against a live AI application. Reads the AI-SBOM to derive an attack surface, generates and executes scenarios, and produces structured findings with OWASP/MITRE mappings.

See [redteam-guide.md](./redteam-guide.md) for a complete description of how the engine works.

### Subcommands

| Subcommand | Description |
|---|---|
| 🟣 *(default, no subcommand name)* | Run the red-team scan |
| 🟣 `catalog-export` | Export the built-in scenario catalog to YAML for customization |

#### 🟣 `nuguard redteam` (scan)

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
  --scenarios prompt-driven-threat,data-exfiltration

# Scan with a custom scenario catalog
nuguard redteam --sbom app.sbom.json --target http://localhost:8000 \
  --catalog ./my-catalog.yaml --profile full

# CI gate — SARIF output, fail on high+
nuguard redteam --sbom app.sbom.json --target $APP_URL \
  --profile ci -f sarif -o results.sarif --fail-on high

# JSON + Markdown in one run
nuguard redteam --sbom app.sbom.json --target $APP_URL \
  --format json --format markdown --output results/redteam
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--config` | `-c` | `./nuguard.yaml` | Config file path |
| `--sbom` | — | from `nuguard.yaml` | AI-SBOM JSON path |
| `--target` | — | SBOM discovery | Live application URL (`http://host:port`) |
| `--source` | — | — | App source directory — used for `.env` loading and `--launch` |
| `--launch` / `--no-launch` | — | off | Auto-start the app from the SBOM startup command; stop it after the scan. Requires `--source` |
| `--policy` | — | from `nuguard.yaml` | Cognitive Policy Markdown path |
| `--canary` | — | from `nuguard.yaml` | Canary JSON file path |
| `--catalog` | — | built-in catalog | Path to a custom scenario catalog YAML. Replaces the built-in catalog. Generate with `nuguard redteam catalog-export` |
| `--profile` | — | `ci` | `ci` (high-signal only) or `full` (all scenarios) |
| `--scenarios` | — | all | Comma-separated filter: `prompt-driven-threat`, `policy-violation`, `data-exfiltration`, `privilege-escalation`, `tool-abuse`, `mcp-toxic-flow`, `api-attack`, `agentic-trust-abuse`, `recon-inference`. Stable catalog IDs (e.g. `D01,C03`) also work |
| `--min-impact-score` | — | `0.0` | Exclude scenarios below this pre-score [0–10] |
| `--guided` / `--no-guided` | — | on when a redteam LLM is configured | Adaptive multi-turn guided conversations (TAP + PAIR) |
| `--guided-max-turns` | — | `12` | Max turns per guided conversation |
| `--guided-concurrency` | — | `3` | Parallel guided conversations |
| `--format` | `-f` | `text` | `text` \| `json` \| `markdown` \| `sarif` (repeat flag or use comma-separated values for multiple outputs) |
| `--output` | `-o` | — | Write findings to this file. Required when multiple formats are requested; base path expands to per-format files |
| `--fail-on` | — | `high` | Exit code `2` if any finding meets this severity |
| `--verbose` / `--no-verbose` | `-v` / `-V` | off | Print detailed per-turn traces |

#### 🟣 `nuguard redteam catalog-export`

Export the full built-in scenario catalog to a YAML file for review or customization. The exported file can be edited and passed back with `--catalog` to replace the built-in catalog for a scan.

```bash
# Write to a file
nuguard redteam catalog-export --output my-catalog.yaml

# Print to stdout (pipe to less or grep)
nuguard redteam catalog-export
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--output` | `-o` | stdout | Write the YAML to this path instead of printing |

Common edits after exporting:

- Set `enabled: false` on a scenario to skip it entirely.
- Lower `base_impact` below the profile threshold (e.g. `< 5.0` for `--profile ci`) to exclude it from fast scans without disabling it globally.
- Edit `expected_control` or `success_signal` to match your application's specific behavior.

See [Customizing the Catalog](./redteam-guide.md#customizing-the-catalog) in the redteam guide for a full walkthrough.

> 🎯 **Target URL resolution order:** `--target` flag → `redteam.target` in `nuguard.yaml` → SBOM-discovered URLs (local → staging → production) → error.

**LLM configuration** (no CLI flags — use env vars or `nuguard.yaml`):

| Env var | YAML key | Description |
|---|---|---|
| `NUGUARD_REDTEAM_LLM_MODEL` | `redteam.llm.model` | Attack payload generation — must be an uncensored model |
| `NUGUARD_REDTEAM_LLM_API_KEY` | `redteam.llm.api_key` | API key for the redteam LLM |
| `NUGUARD_REDTEAM_EVAL_LLM_MODEL` | `redteam.eval_llm.model` | Response evaluation and report generation |
| `NUGUARD_REDTEAM_EVAL_LLM_API_KEY` | `redteam.eval_llm.api_key` | API key for the eval LLM |

**Advanced YAML-only options** (set in `nuguard.yaml`, no CLI flag):

| YAML key | Default | Description |
|---|---|---|
| `redteam.catalog_path` | — | Path to a custom scenario catalog YAML (same as `--catalog`). Relative paths resolved from the config file directory |
| `redteam.strict_outcome` | `false` | When ≥ 80% of events are 5xx/network errors, report `inconclusive_target_errors` instead of `no_findings` |
| `redteam.emit_pytest` | `false` | Generate pytest regression tests for HIT findings (severity ≥ medium) |
| `redteam.emit_pytest_dir` | `tests/redteam` | Directory to write generated pytest files |
| `redteam.tree_breadth` | `0` (off) | TAP: number of tactic variants to branch per depth level |
| `redteam.tree_max_depth` | `0` (off) | TAP: maximum tree depth before pruning non-improving paths |

</details>

<details>
<summary id="nuguard-target">🔌 <strong><code>nuguard target</code></strong> — verify connectivity before a scan</summary>

<br>

Verify connectivity and authentication against a live AI application before running a full scan. Prints a status table covering identity, auth type, HTTP status, response time, and any error details.

### 🟣 `nuguard target verify`

```bash
# Verify using nuguard.yaml
nuguard target verify --config nuguard.yaml

# Quick spot-check with inline options
nuguard target verify --target http://localhost:8000 \
  --endpoint /chat \
  --auth-header "Authorization: Bearer $TOKEN"

# Verify all tenant tokens in a canary file
nuguard target verify --config nuguard.yaml --canary canary.json

# With an SBOM: enables endpoint auto-discovery + a short pre-scan
# conversation that confirms which user/account is being scanned
nuguard target verify --config nuguard.yaml --sbom app.sbom.json
```

| Flag | Default | Description |
|---|---|---|
| `--config`, `-c` | `./nuguard.yaml` | Config file path |
| `--target` | from `nuguard.yaml` | Base URL of the target application |
| `--endpoint` | from `nuguard.yaml` | Chat endpoint path (e.g. `/chat`, `/api/v1/agent`) |
| `--auth-header` | from `nuguard.yaml` | Auth header string (e.g. `"Authorization: Bearer $TOKEN"`) |
| `--canary` | from `nuguard.yaml` | Path to `canary.json` — verifies each tenant session token can authenticate |
| `--sbom` | from `nuguard.yaml` | AI-SBOM JSON — enables the same chat-endpoint auto-discovery and pre-scan account/golden-data discovery used by `behavior` and `redteam` |
| `--discovery-max-turns` | `redteam.discovery_max_turns`, or `3` | Max pre-scan discovery turns (only relevant with `--sbom`) |
| `--skip-discovery` / `--no-skip-discovery` | off | Skip the pre-scan account/golden-data discovery conversation |

> ✅ Run `nuguard target verify` before `nuguard redteam`, `nuguard behavior`, or `nuguard validate` to catch misconfigured endpoints, expired tokens, or firewall blocks early. Exits non-zero if any non-skipped credential fails.

</details>

---

## Planned Commands

`nuguard seed`, `nuguard report`, `nuguard findings`, and `nuguard replay` are registered in the CLI and appear in `nuguard --help`, but each is currently a stub: running any of them prints `not yet implemented` and exits with code `3`. They're listed here so you know what they're *for* — not because they work today.

<details>
<summary>What each one will do</summary>

<br>

| Command | Intended purpose | Flags already wired |
|---|---|---|
| `nuguard seed` | Seed canary data into the target application before red-team testing, independent of running a full scan | `--target`, `--seed-file`, `--output-canary` |
| `nuguard report` | Regenerate a `markdown` \| `sarif` \| `json` report for a completed test run by ID, without re-running the scan | `--test-id`, `--format` |
| `nuguard findings` | List findings from a completed test run, filterable by severity | `--test-id`, `--severity` |
| `nuguard replay` | Deterministically replay a completed test run's recorded traces against a (possibly different) target | `--test-id`, `--target` |

For now, `--canary` on `redteam`/`behavior`/`validate` covers the seeding use case (seed the values into your app yourself, then pass the file), and `--output`/`--format` on each command covers ad-hoc reporting.

</details>

---

## Configuration File

All CLI flags can be set in `nuguard.yaml`. Run `nuguard init` to generate one with auto-detected defaults.

```bash
nuguard init                          # writes ./nuguard.yaml
nuguard init --target http://localhost:8080 --source ./src
```

Priority order: **CLI flags > nuguard.yaml > environment variables > built-in defaults**

Secrets are never stored directly — use `${ENV_VAR}` interpolation:

```yaml
# ─── Target application — shared by behavior and redteam ───────────────────
target:
  url: https://your-app.example.com

  # Authentication — set once; both behavior and redteam inherit it.
  # Override per-command with behavior.auth or redteam.auth when needed.
  auth:
    # Option A: Login flow (preferred when the app exposes a /login endpoint)
    # type: login_flow
    # login_flow:
    #   endpoint: /api/login
    #   payload:
    #     username: ${APP_USERNAME}
    #     password: ${APP_PASSWORD}
    #   token_response_key: access_token
    #   token_header: "Authorization: Bearer"

    # Option B: Static Bearer token
    # type: bearer
    # header: "Authorization: Bearer ${TARGET_TOKEN}"

    # Option C: API key in a custom header
    # type: api_key
    # header: "X-API-Key: ${TARGET_API_KEY}"

    # Option D: HTTP Basic Auth
    # type: basic
    # username: ${APP_USERNAME}
    # password: ${APP_PASSWORD}

    # Option E: No authentication (open endpoints, local dev)
    # type: none

redteam:
  llm:
    api_key: ${NUGUARD_REDTEAM_LLM_API_KEY}
```

See [`nuguard.yaml.example`](../../nuguard.yaml.example) for the full annotated reference, including `behavior:`, `redteam:`, `analyze:`, and `output:` sections.

---

## Getting Help

```bash
nuguard --help
nuguard init --help
nuguard sbom --help
nuguard sbom generate --help
nuguard analyze --help
nuguard scan --help
nuguard policy --help
nuguard policy check --help
nuguard validate --help
nuguard behavior --help
nuguard redteam --help
nuguard target verify --help
```
