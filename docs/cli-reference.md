# NuGuard CLI Reference

NuGuard ships a Typer-based CLI for SBOM generation, static analysis, policy checks, and dynamic red-team testing.

## Top-Level Commands

```text
nuguard init
nuguard sbom
nuguard analyze
nuguard scan
nuguard policy
nuguard redteam
nuguard seed
nuguard report
nuguard findings
nuguard replay
```

## `nuguard init`

Creates a starter cognitive policy Markdown file with section headers only.

Default output:

- `./cognitive_policy.md`
- `./nuguard.yaml.example` (example config file for setting defaults)
- `./canary.example.json` (canary template for red-team testing)

Examples:

```bash
nuguard init
nuguard init --path ./cognitive_policy.md
nuguard init --force
```

Notable options:

- `--path`: custom output path for the starter policy file
- `--force`: overwrite existing files without confirmation

## `nuguard sbom`

SBOM generation, validation, and management.

Common usage:

```bash
nuguard sbom generate --source . --output app.sbom.json
nuguard sbom generate --from-repo https://github.com/org/repo --ref main --output app.sbom.json
nuguard sbom validate --file app.sbom.json
nuguard sbom schema
nuguard sbom --help
```

Notable options:

- `--source`: local source directory
- `--from-repo`: remote Git repository URL
- `--output`: generated SBOM path
- `--format`: `json`, `cyclonedx`, or `markdown`
- `--llm`: enable LLM enrichment
- `--config`: read defaults from `nuguard.yaml`
- `--token`: GitHub token for private repos or API rate limits

## `nuguard analyze`

Runs static risk analysis from an existing AI-SBOM.

Examples:

```bash
nuguard analyze --sbom app.sbom.json
nuguard analyze --sbom app.sbom.json --format json
nuguard analyze --sbom app.sbom.json --format sarif
nuguard analyze --sbom app.sbom.json --source .
```

Notable options:

- `--sbom`: required SBOM JSON path
- `--format`: `markdown`, `json`, or `sarif`
- `--min-severity`: `critical`, `high`, `medium`, `low`, `info`
- `--source`: source tree for Checkov, Trivy, and Semgrep
- `--atlas`, `--osv`, `--grype`, `--checkov`, `--trivy`, `--semgrep`
- `--llm`: enable LLM enrichment in the ATLAS pass
- `--output`: write report to a file

## `nuguard scan`

Runs the unified static pipeline: SBOM generation, analysis, optional policy, and optional redteam.

Examples:

```bash
nuguard scan --source . --output-dir nuguard-reports
nuguard scan --source . --steps sbom,analyze
nuguard scan --source . --policy cognitive_policy.md --target http://localhost:3000
```

Notable options:

- `--source`: application source directory
- `--output-dir`: destination for generated artifacts
- `--steps`: subset of `sbom,analyze,policy,redteam`
- `--policy`: cognitive policy path
- `--target`: live app URL for redteam
- `--fail-on`: severity threshold for non-zero exit

## `nuguard policy`

Policy linting, policy-to-SBOM checks, and compliance assessment.

Examples:

```bash
nuguard policy validate --file cognitive_policy.md
nuguard policy check --policy cognitive_policy.md --sbom app.sbom.json
nuguard policy check --sbom app.sbom.json --framework owasp-llm-top10
nuguard policy check --config nuguard.yaml
```

Subcommands:

- `validate`
- `check`

Notable options for `check`:

- `--policy`
- `--sbom`
- `--config`
- `--framework`
- `--controls`
- `--format text|json`
- `--llm`

## `nuguard redteam`

Runs dynamic adversarial testing against a live AI application.

Examples:

```bash
nuguard redteam --sbom app.sbom.json --target http://localhost:3000
nuguard redteam --sbom app.sbom.json --target http://localhost:3000 --canary ./canary.json
nuguard redteam --sbom app.sbom.json --target http://localhost:3000 --format json
nuguard redteam --config nuguard.yaml
```

Notable options:

- `--sbom`: required unless configured in `nuguard.yaml`
- `--target`: live application URL
- `--source`: app source directory
- `--policy`: cognitive policy path
- `--canary`: canary JSON file
- `--profile`: `ci` or `full`
- `--scenarios`: comma-separated scenario filter
- `--min-impact-score`
- `--format`: `text`, `json`, or `sarif`
- `--fail-on`
- `--guided`
- `--guided-max-turns`
- `--guided-concurrency`

## Stub Commands (coming in a future release)

These commands currently exist but are not implemented end-to-end:

### `nuguard seed`

Intended for seeding canary data into the target app before red-team runs.

### `nuguard report`

Intended for generating reports for completed red-team runs.

### `nuguard findings`

Intended for listing findings from completed red-team runs.

### `nuguard replay`

Intended for deterministic replay of completed red-team runs.

## Help

For command-specific details:

```bash
nuguard --help
nuguard sbom --help
nuguard analyze --help
nuguard scan --help
nuguard policy --help
nuguard redteam --help
```
