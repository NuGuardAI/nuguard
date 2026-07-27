# NuGuard Open Source

NuGuard is an open source AI application security toolkit. Its goal is to provide the most extensive redteaming and behavioral validation of Agentic AI applications. 

With NuGuard, AI developers can focus on building their applications while NuGuard continuously tests and validates them against a wide range of security risks, including supply chain attacks, prompt injection, MCP tool misuse, API Attacks, data exfiltration, and more. NuGuard allows the developers to accelerate their security workflows and get to production faster with confidence.

A commercial version of NuGuard is available as a SaaS product with additional features and support, check it at http://nuguard.ai. 

## What It Does

- Generate an AI-SBOM from a local codebase or Git repo
- Analyze the SBOM for structural AI security risks and supply-chain issues
- Propose an AI behavioral policy based on the SBOM
- Perform static and runtime behavioral testing purpose built around the AI-SBOM and the behavioral policy
- Red-team a sandboxed AI application: most extensive and updated catalog of attack vectors plus custom-built scenarios. This includes prompt injection, tool abuse, data exfiltration, and related attack scenarios that exercise the various sub-agents, tools, and capabilities of the target system.
- Automated remediation suggestions for findings, including code snippets and policy adjustments
- Export findings in text, JSON, Markdown, and SARIF-oriented workflows

## Current CLI Surface

Implemented and usable today:

- `nuguard sbom`
- `nuguard analyze`
- `nuguard scan`
- `nuguard policy`
- `nuguard behavior`
- `nuguard redteam`

## Requirements

- Python 3.12+
- `uv` for the recommended local workflow

## Installation

**Python CLI:**

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

### Claude users can use plugin commands.
Follow the instructions in [docs/plugin-guide.md](docs/plugin-guide.md) to set up the NuGuard plugin for Claude and use it to run commands like `/nuguard-sbom`, `/nuguard-analyze`, and `/nuguard-redteam` directly from your conversations with Claude.

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

By default this runs SBOM generation plus static analysis in one pass. To include
policy and red-team validations, opt in to those steps and provide the required
inputs:

```bash
nuguard scan \
  --source . \
  --steps sbom,analyze,policy,redteam \
  --policy cognitive_policy.md \
  --target http://localhost:3000 \
  --output-dir nuguard-reports
```

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

### Pre-Publish Sanity Checks

Before publishing to TestPyPI or PyPI, run the quick multi-app sanity gate.

One-shot runner:

```bash
bash tests/apps/prepublish-sanity.sh
```

This runner performs:

- A fast repo smoke check (`nuguard --help` plus critical local tests)
- Behavior runs in dynamic mode with `intent_happy_path` workflow
- Redteam runs with `profile: ci`
- Artifact and quality gates (non-empty reports, non-zero executed scenarios, strict endpoint-source checks, and transport-error guardrails)

Prepublish config files used by the runner:

- `tests/apps/openai-cs-agents-demo/nuguard.prepublish.yaml`
- `tests/apps/Gemini-Auto-app/nuguard.prepublish.yaml`
- `tests/apps/pinnacle-bank-app/nuguard-azure.prepublish.yaml`

Run manually per app (if needed):

```bash
# OpenAI CS agents demo
uv run nuguard sbom generate --config tests/apps/openai-cs-agents-demo/nuguard.prepublish.yaml --format json -o tests/apps/openai-cs-agents-demo/openai-cs.sbom.json
uv run nuguard behavior --config tests/apps/openai-cs-agents-demo/nuguard.prepublish.yaml --mode dynamic --format json --format markdown --output tests/apps/openai-cs-agents-demo/reports/openai-cs-prepublish-behavior --verbose
uv run nuguard redteam --config tests/apps/openai-cs-agents-demo/nuguard.prepublish.yaml --format json --format markdown --output tests/apps/openai-cs-agents-demo/reports/openai-cs-prepublish-redteam --verbose

# Gemini Auto app
uv run nuguard sbom generate --config tests/apps/Gemini-Auto-app/nuguard.prepublish.yaml --format json -o tests/apps/Gemini-Auto-app/gemini-auto.sbom.json
uv run nuguard behavior --config tests/apps/Gemini-Auto-app/nuguard.prepublish.yaml --mode dynamic --format json --format markdown --output tests/apps/Gemini-Auto-app/reports/gemini-auto-prepublish-behavior --verbose
uv run nuguard redteam --config tests/apps/Gemini-Auto-app/nuguard.prepublish.yaml --format json --format markdown --output tests/apps/Gemini-Auto-app/reports/gemini-auto-prepublish-redteam --verbose

# Pinnacle Bank app
uv run nuguard sbom generate --config tests/apps/pinnacle-bank-app/nuguard-azure.prepublish.yaml --format json -o tests/apps/pinnacle-bank-app/pinnacle-bank.sbom.json
uv run nuguard behavior --config tests/apps/pinnacle-bank-app/nuguard-azure.prepublish.yaml --mode dynamic --format json --format markdown --output tests/apps/pinnacle-bank-app/reports/pinnacle-bank-prepublish-behavior --verbose
uv run nuguard redteam --config tests/apps/pinnacle-bank-app/nuguard-azure.prepublish.yaml --format json --format markdown --output tests/apps/pinnacle-bank-app/reports/pinnacle-bank-prepublish-redteam --verbose
```

Important:

- Do not use `|| true` in publish-gating runs.
- Exit code `2` can indicate findings or policy gates; treat it as a signal and rely on report-quality checks to decide pass/fail.

## Repo Notes

- The repository currently contains example outputs and benchmark fixtures under `tests/output/`
- Some red-team and benchmark tests are opt-in and gated by environment variables
- LLM-assisted features depend on provider credentials being available via environment variables

## License

License information is available in the [LICENSE](./LICENSE) file.
