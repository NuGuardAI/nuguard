# NuGuard Quick Start

This guide gets you to a starter behavioral policy, a first SBOM, a static analysis report, a policy check, and a red-team run.

## Prerequisites

- Python 3.12+

## Install

Install the published package:

```bash
pip install nuguard
```

Check the CLI:

```bash
nuguard --help
```

## 1. Initialize With `nuguard init`

Create `nuguard.yaml`, `cognitive-policy.md`, and `canary.example.json` with sensible defaults:

```bash
nuguard init --target http://localhost:8080
```

Pass `--llm` to have the LLM draft a concise `cognitive-policy.md` from your app's context
instead of writing a blank template (requires `LITELLM_API_KEY`):

```bash
nuguard init --target http://localhost:8080 --llm
```

To overwrite existing files:

```bash
nuguard init --force
```

## 2. Generate an AI-SBOM

Generate from the current repository:

```bash
nuguard sbom generate --source . --output app.sbom.json
```

Generate from a Git repository:

```bash
nuguard sbom generate \
  --from-repo https://github.com/org/repo \
  --ref main \
  --output app.sbom.json
```

## 3. Run Static Analysis

```bash
nuguard analyze --sbom app.sbom.json --format markdown
```

Useful variants:

```bash
nuguard analyze --sbom app.sbom.json --format json
nuguard analyze --config nuguard.yaml --format sarif
nuguard analyze --sbom app.sbom.json --format json --format markdown --output reports/analyze
```

## 5. Run the Behavior Validation

Run the behavior validation (static and dynamic tests) based on the cognitive policy. Dynamic tests will run against the live application. Make sure to configure the target application URL and any necessary credentials in `nuguard.yaml` before running.

```bash
nuguard behavior \
  --config nuguard.yaml \
  --format markdown \
  --output reports/behavior.md
```

## 6. Red-Team a Live AI App

For red-team canaries, start from the tracked example file (optional step but useful to check for accurate data exfiltration validation):

```bash
cp canary.example.json canary.json
```

Run against a live application with the canary file for validation:

```bash
nuguard redteam \
  --config nuguard.yaml \
  --output reports/redteam.md \
  --format markdown
```

## 7. Run from Claude (Plugin)

NuGuard integrates directly into Claude Code and Claude Desktop. Install the plugin once and run the full pipeline — SBOM, analysis, behavior, and red-team — from the chat interface.

```bash
claude plugin add NuGuardAI/nuguard
```

See the [Plugin Guide](plugin-guide.md) for Claude Code slash commands, agents, and Claude Desktop / npx setup.
