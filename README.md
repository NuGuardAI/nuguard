# NuGuard Open Source

NuGuard is an open source AI application security CLI. It can generate an AI-focused SBOM from source code, run static security analysis, lint cognitive policy documents, test live AI app behavior, and red-team a live AI app with scenario-driven adversarial testing.

## What It Does

- Generate an AI-SBOM from a local codebase or Git repo
- Analyze the SBOM for structural AI security risks and dependency issues
- Cross-check a cognitive policy against the SBOM
- Perform static and dynamic behavioral testing against a live AI application endpoint
- Red-team a running AI application with custom-built scenarios based on the AI-SBOM and the cognitive policy. This includes prompt injection, tool abuse, data exfiltration, and related attack scenarios that exercise the various sub-agents, tools, and capabilities of the target system.
- Export findings in text, JSON, Markdown, and SARIF-oriented workflows

## Current CLI Surface

Implemented and usable today:

- `nuguard sbom`
- `nuguard analyze`
- `nuguard scan`
- `nuguard policy`
- `nuguard behavior`
- `nuguard redteam`

Present but still stubbed / not yet implemented:

- `nuguard seed`
- `nuguard report`

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

For production use, install the package from PyPI with: 

```bash
pip install nuguard
```

The steps below describe how to set up a local development environment. This is recommended if you want to run the latest code, contribute to the project, or run the CLI with LLM-assisted features that require local environment variable configuration.

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
nuguard sbom generate --source . --output app.sbom.json
```

You can also scan a remote repository:

```bash
nuguard sbom generate \
  --from-repo https://github.com/org/repo \
  --ref main \
  --output app.sbom.json
```

### 2. Run Static Analysis

```bash
nuguard analyze --sbom app.sbom.json --format markdown
```

Typical outputs:

- `markdown` for human review
- `json` for automation
- `sarif` for code scanning pipelines

### 3. Behavioral Testing

```bash
nuguard behavior \
  --sbom app.sbom.json \
  --target http://localhost:3000 \
  --format markdown
```

### 4. Red-Team a Live App

```bash
nuguard redteam \
  --config nuguard.yaml \
  --output reports/redteam.md \
  --format markdown
```

For richer red-team coverage, you can also provide:

- a cognitive policy with `--policy`
- canary values with `--canary`
- a config file with `--config`

### 5. Run the Unified Pipeline

```bash
nuguard scan \
  --source . \
  --output-dir nuguard-reports
```

This is the easiest way to run SBOM generation plus static analysis in one pass.

## Configuration

NuGuard supports project configuration through `nuguard.yaml`. A ready-to-edit example lives at [`nuguard.yaml.example`](nuguard.yaml.example).

Key areas in the example config:

- `sbom`: existing SBOM path
- `source`: source directory for generation
- `policy`: cognitive policy path
- `llm`: model settings for LLM-assisted features
- `behavior`: target URL, endpoint, and test profile settings for behavioral testing
- `redteam`: target URL, endpoint, canary file, profiles, scenario filters, guided conversation settings, and finding trigger controls (`finding_triggers.*`)
- `analyze`: minimum severity threshold
- `database`: SQLite or Postgres-backed storage settings
- `output`: output format and failure threshold

CLI flags take precedence over `nuguard.yaml`, which takes precedence over environment variables and built-in defaults.

## Red-Team Canaries

NuGuard can watch for seeded canary values during dynamic testing to produce high-confidence exfiltration findings. Start from [`canary.example.json`](canary.example.json), create your local `canary.json`, seed those values into the target system, then point `nuguard redteam` at that file with `--canary`.

More detail is available in [`docs/redteam-engine.md`](docs/redteam-engine.md).

## Common Commands

```bash
nuguard --help
nuguard sbom --help
nuguard analyze --help
nuguard policy --help
nuguard behavior --help
nuguard redteam --help
nuguard scan --help
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

- [publish-testpypi.yml](.github/workflows/publish-testpypi.yml)
- [publish-pypi.yml](.github/workflows/publish-pypi.yml)

Before the workflows can publish, configure Trusted Publishers in TestPyPI and PyPI for the `nuguard` project with:

- owner/org: `NuGuardAI`
- repository: `nuguard`
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

License information is available in the [LICENSE](./LICENSE) file.
