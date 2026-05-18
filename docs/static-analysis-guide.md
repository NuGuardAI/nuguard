# NuGuard Static Analysis Guide

This guide explains NuGuard’s static analysis pipeline, how to run it, what each detector does, and how to install optional external tools such as Grype, Trivy, and Checkov.


## What Static Analysis Does

NuGuard static analysis works from an existing AI-SBOM. It does not need a running target application.

The implemented static pipeline includes:

1. AI Static specific structural rules (NGA rules)
2. OSV dependency CVE lookup
3. Grype vulnerability scan
4. Checkov IaC scan
5. Trivy container/filesystem scan
6. Semgrep AI-security scan
7. MITRE ATLAS native checks

The analyzer normalizes all findings into the shared `Finding` model and can render results as Markdown, JSON, or SARIF.

## Basic Workflow

### 1. Generate an SBOM

```bash
nuguard sbom generate --source . --output app.sbom.json
```

### 2. Run analysis

```bash
nuguard analyze --sbom app.sbom.json
```

### 3. Choose an output format

```bash
nuguard analyze --sbom app.sbom.json --format markdown
nuguard analyze --sbom app.sbom.json --format json
nuguard analyze --sbom app.sbom.json --format sarif
```

## `nuguard analyze`

This is the direct static-analysis command.

Examples:

```bash
nuguard analyze --sbom app.sbom.json
nuguard analyze --sbom app.sbom.json --source .
nuguard analyze --sbom app.sbom.json --min-severity high
nuguard analyze --sbom app.sbom.json --output report.md
nuguard analyze --sbom app.sbom.json --nga                   # NGA rules only (fast)
nuguard analyze --sbom app.sbom.json --config nuguard.yaml   # load settings from config
```

Useful options:

- `--sbom`: path to AI-SBOM JSON file; can be omitted when `sbom:` is set in `nuguard.yaml` via `--config`
- `--config`, `-c`: path to `nuguard.yaml`; loads `sbom`, `analyze.min_severity`, and `analyze.nga_only` (CLI flags override)
- `--nga`: run only NGA structural rules (NGA-001–018); disables all external tool scans
- `--format`: `markdown`, `json`, or `sarif`
- `--min-severity`: `critical`, `high`, `medium`, `low`, `info`
- `--source`: source directory used by Checkov, Trivy, and Semgrep
- `--atlas/--no-atlas`
- `--osv/--no-osv`
- `--grype/--no-grype`
- `--checkov/--no-checkov`
- `--trivy/--no-trivy`
- `--semgrep/--no-semgrep`
- `--llm`
- `--output`

## `nuguard scan`

If you want one command that first generates the SBOM and then runs analysis:

```bash
nuguard scan --source . --output-dir nuguard-reports
```

This is a convenient entry point when you do not already have `app.sbom.json`.

## Built-In vs Optional Detectors

### Built-in detectors

These do not require extra system tools:

- NGA structural rules
- OSV dependency lookups
- MITRE ATLAS native checks

### Optional external-tool detectors

These are enabled by default but only run if the tool is available:

- Grype
- Checkov
- Trivy
- Semgrep

If one of these tools is not installed, NuGuard will usually skip that detector instead of failing the whole run.

## What Each Detector Covers

### NGA structural rules

NuGuard-native deterministic rules for AI system structure and risk patterns.

Examples:

- missing guardrails
- unsafe tool access
- privilege paths
- injection surfaces
- data-handling gaps
- missing audit trails

### OSV

Dependency vulnerability lookup against the OSV ecosystem.

Useful for:

- Python package CVEs
- ecosystem-level advisory lookups

### Grype

Package and container vulnerability scanning.

Useful for:

- package inventory CVEs
- image/package-based security findings

### Checkov

Infrastructure-as-code scanning.

Useful for:

- Terraform
- CloudFormation
- Kubernetes manifests
- other supported IaC formats

### Trivy

Filesystem and container scanning.

Useful for:

- container images
- local filesystem/package scanning
- some secret/config findings depending on target and setup

### Semgrep

Pattern-based source scanning using bundled AI-security rules.

Useful for:

- insecure code patterns
- AI/agent application code smells
- custom source-level checks

### MITRE ATLAS native checks

Extra graph-style checks beyond the base NGA rules, with ATLAS technique mapping.

## Installing Optional Tools

NuGuard does not install Grype, Trivy, Checkov, or Semgrep automatically as system binaries. You install them separately if you want those detectors to run.

### Grype

Official install docs:

- https://github.com/anchore/grype

Common Linux/macOS install:

```bash
curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b /usr/local/bin
```

Verify:

```bash
grype version
```

### Trivy

Official install docs:

- https://github.com/aquasecurity/trivy

Common Linux/macOS install:

```bash
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
```

Verify:

```bash
trivy --version
```

### Checkov

Official install docs:

- https://www.checkov.io/

Install with pip:

```bash
pip install checkov
```

Verify:

```bash
checkov --version
```

### Semgrep

Official install docs:

- https://semgrep.dev/docs/

Install with pip:

```bash
pip install semgrep
```

Verify:

```bash
semgrep --version
```

## Recommended Setup

If you want full local static-analysis coverage:

```bash
pip install checkov semgrep
curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b /usr/local/bin
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
```

Then run:

```bash
nuguard analyze --sbom app.sbom.json --source .
```

## Severity Filtering

Use `--min-severity` to control what is shown:

```bash
nuguard analyze --sbom app.sbom.json --min-severity high
```

Accepted values:

- `critical`
- `high`
- `medium`
- `low`
- `info`

## Output Formats

### Markdown

Best for human review.

```bash
nuguard analyze --sbom app.sbom.json --format markdown
```

### JSON

Best for automation or downstream processing.

```bash
nuguard analyze --sbom app.sbom.json --format json
```

### SARIF

Best for code-scanning systems and security integrations.

```bash
nuguard analyze --sbom app.sbom.json --format sarif
```

## Source-Aware Scans

Pass `--source` when you want the source-based detectors to work well:

```bash
nuguard analyze --sbom app.sbom.json --source .
```

This especially helps:

- Checkov
- Trivy
- Semgrep

## LLM Enrichment

You can enable LLM enrichment in the ATLAS pass:

```bash
nuguard analyze --sbom app.sbom.json --llm
```

This requires a working model/API key setup through the repo’s LLM configuration path.

## Quick NGA-only scan

When you want fast structural feedback with no external tools, use `--nga`:

```bash
nuguard analyze --sbom app.sbom.json --nga
```

This runs only the 18 NGA structural rules (NGA-001–018) and returns within seconds.
All other scanners (OSV, Grype, Checkov, Trivy, Semgrep, ATLAS native checks) are
disabled. ATLAS technique annotations are still included on NGA findings — they are
produced inline by the NGA runner.

To make NGA-only mode the default in a project, set `analyze.nga_only: true` in
`nuguard.yaml`:

```yaml
analyze:
  nga_only: true
  min_severity: high
```

Then run with config:

```bash
nuguard analyze --sbom app.sbom.json --config nuguard.yaml
```

A `--min-severity` flag on the command line always overrides the config value.

## Config file support

`--config nuguard.yaml` lets you store per-project analysis defaults. Fields read
from the `analyze:` section of `nuguard.yaml`:

| Field | CLI equivalent | Description |
|-------|---------------|-------------|
| `min_severity` | `--min-severity` | Minimum severity threshold |
| `nga_only` | `--nga` | Run only NGA structural rules |

## Common Troubleshooting

### A detector is skipped

Check whether the tool is installed and on `PATH`:

```bash
which grype
which trivy
which checkov
which semgrep
```

### You want everything in one command

Use:

```bash
nuguard scan --source . --output-dir nuguard-reports
```

## Related Docs

- [docs/quick-start.md](/workspaces/nuguard-oss/docs/quick-start.md)
- [docs/cli-reference.md](/workspaces/nuguard-oss/docs/cli-reference.md)
- [docs/sbom-schema.md](/workspaces/nuguard-oss/docs/sbom-schema.md)
- [docs/troubleshooting.md](/workspaces/nuguard-oss/docs/troubleshooting.md)
