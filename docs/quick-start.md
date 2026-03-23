# NuGuard Quick Start

This guide gets you from a fresh checkout to a starter cognitive policy, a first SBOM, a static analysis report, a policy check, and a red-team run.

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

## 1. Initialize a Starter Cognitive Policy With `nuguard init`

Create a blank starter `cognitive_policy.md` with the recognized policy section headers:

```bash
nuguard init
```

To write it somewhere else:

```bash
nuguard init --path ./docs/cognitive_policy.md
```

To overwrite an existing file:

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
nuguard analyze --sbom app.sbom.json --format sarif
```

## 4. Validate or Check a Cognitive Policy

Validate the policy structure:

```bash
nuguard policy validate --file cognitive_policy.md
```

Cross-check the policy against the SBOM:

```bash
nuguard policy check \
  --policy cognitive_policy.md \
  --sbom app.sbom.json
```

## 5. Run the Unified Pipeline

If you want one command for SBOM generation plus static analysis:

```bash
nuguard scan \
  --source . \
  --output-dir nuguard-reports
```

## 6. Red-Team a Live AI App

Run against a local target:

```bash
nuguard redteam \
  --sbom app.sbom.json \
  --target http://localhost:3000
```

With canaries and JSON output:

```bash
nuguard redteam \
  --sbom app.sbom.json \
  --target http://localhost:3000 \
  --canary ./canary.json \
  --format json
```

For red-team canaries, start from the tracked example file:

```bash
cp canary.example.json canary.json
```

## 7. Use Project Config

NuGuard can read defaults from `nuguard.yaml`. Start from:

```bash
cp nuguard.yaml.example nuguard.yaml
```

Then use commands with fewer flags, for example:

```bash
nuguard policy check
nuguard redteam
```

