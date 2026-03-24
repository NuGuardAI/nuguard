# NuGuard OSS

NuGuard is an open source AI application security CLI. It can generate an AI-focused SBOM from source code, run static security analysis, validate cognitive policy documents, and red-team a live AI app with scenario-driven adversarial testing.

## What It Does

- Generate an AI-SBOM from a local codebase or Git repo
- Analyze the SBOM for structural AI security risks and dependency issues
- Cross-check a cognitive policy against the SBOM
- Red-team a running AI application with prompt injection, tool abuse, data exfiltration, and related attack scenarios
- Export findings in text, JSON, Markdown, and SARIF-oriented workflows

## Current CLI Surface

Implemented and usable today:

- `nuguard sbom`
- `nuguard analyze`
- `nuguard scan`
- `nuguard policy`
- `nuguard redteam`

Present but still stubbed / not yet implemented:

- `nuguard seed`
- `nuguard report`
- `nuguard findings`
- `nuguard replay`

## Requirements

- Python 3.12+
- `uv` for the recommended local workflow

Optional external tools used by some analysis paths:

- `grype`
- `checkov`
- `trivy`
- `semgrep`

If these tools are not installed, the corresponding checks can be skipped or may report as unavailable depending on the command path.

## Installation

The steps below describe how to set up a local development environment. For production use, install the package from PyPI with: pip install nuguard

```bash
uv sync --dev
```

Run the CLI with:

```bash
uv run nuguard --help
```

Or, from the virtual environment:

```bash
. .venv/bin/activate
nuguard --help
```

## Quick Start

### 1. Generate an AI-SBOM

```bash
uv run nuguard sbom generate --source . --output app.sbom.json
```

You can also scan a remote repository:

```bash
uv run nuguard sbom generate \
  --from-repo https://github.com/org/repo \
  --ref main \
  --output app.sbom.json
```

### 2. Run Static Analysis

```bash
uv run nuguard analyze --sbom app.sbom.json --format markdown
```

Typical outputs:

- `markdown` for human review
- `json` for automation
- `sarif` for code scanning pipelines

### 3. Validate or Check a Cognitive Policy

Validate policy structure:

```bash
uv run nuguard policy validate --file cognitive-policy.md
```

Cross-check policy against the SBOM:

```bash
uv run nuguard policy check \
  --policy cognitive-policy.md \
  --sbom app.sbom.json
```

### 4. Red-Team a Live App

```bash
uv run nuguard redteam \
  --sbom app.sbom.json \
  --target http://localhost:3000 \
  --format json
```

For richer red-team coverage, you can also provide:

- a cognitive policy with `--policy`
- canary values with `--canary`
- a config file with `--config`

### 5. Run the Unified Pipeline

```bash
uv run nuguard scan \
  --source . \
  --output-dir nuguard-reports
```

This is the easiest way to run SBOM generation plus static analysis in one pass.

## Configuration

NuGuard supports project configuration through `nuguard.yaml`. A ready-to-edit example lives at [`nuguard.yaml.example`](/workspaces/nuguard-oss/nuguard.yaml.example).

Key areas in the example config:

- `sbom`: existing SBOM path
- `source`: source directory for generation
- `policy`: cognitive policy path
- `llm`: model settings for LLM-assisted features
- `redteam`: target URL, endpoint, canary file, profiles, scenario filters, and guided conversation settings
- `analyze`: minimum severity threshold
- `database`: SQLite or Postgres-backed storage settings
- `output`: output format and failure threshold

CLI flags take precedence over `nuguard.yaml`, which takes precedence over environment variables and built-in defaults.

## Red-Team Canaries

NuGuard can watch for seeded canary values during dynamic testing to produce high-confidence exfiltration findings. Start from [`canary.example.json`](/workspaces/nuguard-oss/canary.example.json), create your local `canary.json`, seed those values into the target system, then point `nuguard redteam` at that file with `--canary`.

More detail is available in [`docs/redteam-engine.md`](/workspaces/nuguard-oss/docs/redteam-engine.md).

## Common Commands

```bash
uv run nuguard --help
uv run nuguard sbom --help
uv run nuguard analyze --help
uv run nuguard policy --help
uv run nuguard redteam --help
uv run nuguard scan --help
```

## Development

Install dev dependencies:

```bash
make dev
```

Run tests:

```bash
make test
```

Run linting and type checks:

```bash
make lint
```

Format the codebase:

```bash
make fmt
```

## Publishing

This repo includes GitHub Actions workflows for Trusted Publishing to TestPyPI and PyPI:

- [publish-testpypi.yml](/workspaces/nuguard-oss/.github/workflows/publish-testpypi.yml)
- [publish-pypi.yml](/workspaces/nuguard-oss/.github/workflows/publish-pypi.yml)

Before the workflows can publish, configure Trusted Publishers in TestPyPI and PyPI for the `nuguard` project with:

- owner/org: `NuGuardAI`
- repository: `nuguard-oss`
- workflow file: `publish-testpypi.yml` or `publish-pypi.yml`
- environment: `testpypi` or `pypi`

Recommended release flow:

1. Run the TestPyPI workflow manually from GitHub Actions.
2. Verify the package install and CLI behavior from TestPyPI.
3. Create a GitHub release to trigger the PyPI publish workflow.

## Repo Notes

- The repository currently contains example outputs and benchmark fixtures under `tests/output/`
- Some red-team and benchmark tests are opt-in and gated by environment variables
- LLM-assisted features depend on provider credentials being available via environment variables

## License

No license file is currently present in this repository. Add one before treating the project as redistributable open source.
