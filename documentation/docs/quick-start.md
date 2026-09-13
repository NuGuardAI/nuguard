<h1 align="center">NuGuard Quick Start</h1>

<p align="center">
  <strong>AI application security suite: AI-SBOM generation, static analysis, cognitive policy, behavior testing, and red-teaming — for AI-powered and agentic applications.</strong>
</p>

<p align="center">
  See the <a href="../../.github/README.md">README</a> for the attack catalog and OWASP coverage overview.
</p>

<p align="center">
  <a href="#key-capabilities">Key capabilities</a> ·
  <a href="#requirements">Requirements</a> ·
  <a href="#installation">Installation</a> ·
  <a href="#cli-surface">CLI surface</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#configuration">Configuration</a>
</p>

---

## Key Capabilities

NuGuard reads your AI application's source code, builds a structured graph of every AI
component (agents, models, tools, guardrails, datastores, MCP servers), and derives
everything downstream — analysis, policy checks, and attack scenarios — from that graph
instead of generic payload libraries.

| Capability | Command | What it does |
|---|---|---|
| **AI-SBOM Generation** | `nuguard sbom generate` | Statically scans Python, TypeScript, Go, and C# source (15+ AI frameworks), plus Azure Bicep and other IaC, into a structured AI Bill of Materials |
| **Static Analysis** | `nuguard analyze` | Structural + supply-chain risk analysis on an AI-SBOM — no running app needed |
| **Cognitive Policy** | `nuguard policy` | Lints a Markdown policy document, cross-checks it against the SBOM, and assesses compliance (OWASP LLM Top 10, NIST AI RMF, EU AI Act) |
| **Behavior Testing** | `nuguard behavior` | Verifies a live app behaves as intended against its Cognitive Policy |
| **Red-Team** | `nuguard redteam` | Dynamic adversarial testing (prompt injection, data exfiltration, tool abuse, and more) against a live target |
| **Unified Scan** | `nuguard scan` | Chains any combination of the above in one command |
| **Claude MCP Plugin** | `pip install "nuguard[mcp]"` | Exposes the full pipeline as tools inside Claude Desktop / Claude Code — see the [Plugin Guide](plugin-guide.md) |

Run any capability standalone, or chain them with
`nuguard scan --steps sbom,analyze,policy,redteam --policy cognitive_policy.md --target <url>`.

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

## CLI Surface

<img src="assets/cli-surface.svg" alt="nuguard --help output. Core commands: init, sbom, analyze, policy, behavior, redteam, scan, target. Supporting commands: seed, validate, report, findings, replay." width="1024">

Run `nuguard <command> --help` for the full flag reference, or see [`cli-reference.md`](cli-reference.md).

## Quick Start

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/quickstart-flow-dark.svg">
    <img src="assets/quickstart-flow-light.svg" alt="Quick start flow: 1 SBOM (sbom generate), 2 Analyze (analyze), 3 Behavior (behavior), 4 Red-team (redteam), 5 All-in-one (scan, runs steps 1-4 together). Each step is runnable on its own." width="900">
  </picture>
</p>

Pick a workflow. Each guide has a copy-paste-able command and the settings you're most likely to change.

### 📋 Running a Static Analysis

Structural and supply-chain risk from an AI-SBOM — no live target needed.

[![Read the Static Analysis Guide](https://img.shields.io/badge/→_Read_the_Static_Analysis_Guide-1f6feb?style=for-the-badge)](static-analysis-guide.md#quick-start)

### 🧪 Running a Behavior Test

Verify your app behaves as intended against its Cognitive Policy.

[![Read the Behavior Guide](https://img.shields.io/badge/→_Read_the_Behavior_Guide-8957e5?style=for-the-badge)](behavior-guide.md#quick-start)

### ⚔️ Running a Red Team Test

Adversarial attack scenarios against a live target.

[![Read the Red-Team Guide](https://img.shields.io/badge/→_Read_the_Red--Team_Guide-da3633?style=for-the-badge)](redteam-guide.md#quick-start)

### Run Everything at Once

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

### 📖 Need the full flag reference?

Every command, every flag, every default — covered in the CLI reference doc.

[![Read the CLI Reference](https://img.shields.io/badge/→_Read_the_CLI_Reference-111111?style=for-the-badge)](cli-reference.md)
