# NuGuard Quick Start

This guide gets you from a fresh checkout to starter project files, a first SBOM, a static analysis report, a policy check, and a red-team run.

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

## 1. Initialize Starter Project Files With `nuguard init`

Create starter files (`nuguard.yaml.example`, `canary.example.json`, and `cognitive_policy.md`):

```bash
nuguard init
```

The generated `cognitive_policy.md` is a starter template. Fill in at least one real policy section before expecting `nuguard policy validate` to pass.

To write it somewhere else:

```bash
nuguard init --dir ./docs
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

Current status: `scan` fully wires SBOM + analyze. Policy and redteam steps inside `scan` are placeholders, so use `nuguard policy ...` and `nuguard redteam ...` directly for those workflows.

## 6. Red-Team a Live AI App

Run against a local target:

```bash
nuguard redteam \
  --sbom app.sbom.json \
  --target http://localhost:3000
```

If your application exposes its chat endpoint somewhere other than `/chat`, copy `nuguard.yaml.example` to `nuguard.yaml`, set `redteam.target_endpoint`, and run:

```bash
nuguard redteam --config nuguard.yaml
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

