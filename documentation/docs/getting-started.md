<h1 align="center">Getting Started</h1>

<p align="center">
  <strong>Requirements, installation, the CLI surface, a quick-start walkthrough, and the configuration reference for NuGuard.</strong>
</p>

<p align="center">
  See the <a href="../../.github/README.md">project README</a> for what NuGuard does and the attack catalog / OWASP coverage overview.
</p>

<p align="center">
  <a href="#cli-surface">CLI surface</a> ·
  <a href="#requirements">Requirements</a> ·
  <a href="#installation">Installation</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#configuration">Configuration</a>
</p>

---

## CLI Surface

<img src="assets/cli-surface.svg" alt="nuguard --help output. Core commands: init, sbom, analyze, policy, behavior, redteam, scan, target. Supporting commands: seed, validate, report, findings, replay." width="1024">

Run `nuguard <command> --help` for the full flag reference, or see [`cli-reference.md`](cli-reference.md).

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

### Claude Code plugin

Follow [`plugin-guide.md`](plugin-guide.md) to set up the NuGuard plugin for Claude Code and run commands like `/nuguard-sbom`, `/nuguard-analyze`, and `/nuguard-redteam` directly from a conversation.

## Quick Start

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/quickstart-flow-dark.svg">
    <img src="assets/quickstart-flow-light.svg" alt="Quick start flow: 1 SBOM (sbom generate), 2 Analyze (analyze), 3 Behavior (behavior), 4 Red-team (redteam), 5 All-in-one (scan, runs steps 1-4 together). Each step is runnable on its own." width="900">
  </picture>
</p>

### 1. Generate an AI-SBOM

<img src="assets/quickstart-1-sbom.svg" alt="nuguard sbom generate --source . --output app.sbom.json. Or scan a remote repository: nuguard sbom generate --from-repo https://github.com/org/repo --ref main --output app.sbom.json" width="760">

### 2. Run Static Analysis

<img src="assets/quickstart-2-analyze.svg" alt="nuguard analyze --sbom app.sbom.json --format markdown. Formats: markdown for human review, json for automation, sarif for code scanning pipelines." width="760">

### 3. Behavioral Testing

<img src="assets/quickstart-3-behavior.svg" alt="nuguard behavior --sbom app.sbom.json --target http://localhost:3000 --format markdown" width="760">

### 4. Red-Team a Live App

<img src="assets/quickstart-4-redteam.svg" alt="nuguard redteam --config nuguard.yaml --output reports/redteam.md --format markdown. Richer coverage: --policy for cognitive policy, --canary for canary values, --config for an alternate config file." width="760">

### 5. Run the Unified Pipeline

<img src="assets/quickstart-5-scan.svg" alt="nuguard scan --source . --output-dir nuguard-reports (default: SBOM plus static analysis). To opt in to policy and red-team: nuguard scan --source . --steps sbom,analyze,policy,redteam --policy cognitive_policy.md --target http://localhost:3000 --output-dir nuguard-reports" width="760">

## Configuration

NuGuard supports project configuration through `nuguard.yaml`. Scaffold one with `nuguard init`, or start from the ready-to-edit example at [`nuguard.yaml.example`](../../nuguard.yaml.example).

| Section | Controls |
|---|---|
| `sbom` | Existing SBOM path |
| `source` | Source directory for generation |
| `policy` | Cognitive policy path |
| `target` | Live app URL, endpoint path, request/response payload shape, and auth (bearer / API key / basic / login-flow) — shared by `behavior` and `redteam`, set once |
| `llm` | Model settings for LLM-assisted features |
| `sbom_generation` | Toggle LLM enrichment of SBOM nodes (`llm: true`), requires `LITELLM_API_KEY` |
| `behavior` | Target URL, endpoint, and test profile settings |
| `redteam` | Target URL, endpoint, canary file, profiles, scenario filters, guided conversation settings, and finding trigger controls (`finding_triggers.*`) |
| `analyze` | Minimum severity threshold |
| `database` | SQLite or Postgres-backed storage settings |
| `output` | Output format and failure threshold |

CLI flags take precedence over `nuguard.yaml`, which takes precedence over environment variables and built-in defaults.

<table>
<tr><td>

### 📖 Need the full flag reference?

Every command, every flag, every default — covered in the CLI reference doc.

[![Read the CLI Reference](https://img.shields.io/badge/→_Read_the_CLI_Reference-111111?style=for-the-badge)](cli-reference.md)

</td></tr>
</table>
