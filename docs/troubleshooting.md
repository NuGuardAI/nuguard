# NuGuard Troubleshooting Guide

This guide covers common setup, CLI, scanning, packaging, and publishing issues when working with NuGuard.

## Installation

## `nuguard: command not found`

If you installed from PyPI:

```bash
pip install nuguard
python -m pip show nuguard
```

Then verify your Python scripts directory is on `PATH`, or run:

```bash
python -m nuguard.cli.main --help
```

If you are working from source:

```bash
uv sync --dev
uv run nuguard --help
```

## `pip install nuguard` fails

Check:

- Python version is `3.12+`
- `pip` is up to date
- your environment can build/install the declared dependencies

Useful commands:

```bash
python --version
python -m pip install --upgrade pip
```

## CLI Basics

## `nuguard init` will not overwrite an existing file

This is expected. Use:

```bash
nuguard init --force
```

## `nuguard --help` works but a subcommand fails immediately

Check whether the command is currently a stub.

Currently stubbed commands:

- `nuguard seed`
- `nuguard report`
- `nuguard findings`
- `nuguard replay`

## SBOM Generation

## `Error: --source is required` or no SBOM is produced

For local scanning, pass a real source directory:

```bash
nuguard sbom generate --source . --output app.sbom.json
```

For remote scanning:

```bash
nuguard sbom generate --from-repo https://github.com/org/repo --ref main --output app.sbom.json
```

## Private repo scan fails

Provide a GitHub token:

```bash
nuguard sbom generate \
  --from-repo https://github.com/org/private-repo \
  --token "$GH_TOKEN" \
  --output app.sbom.json
```

NuGuard also checks `GH_TOKEN` and `GITHUB_TOKEN`.

## SBOM validation fails

Validate the file directly:

```bash
nuguard sbom validate --file app.sbom.json
```

If you need a minimal known-good example, compare against:

- [docs/sample-sbom.json](/workspaces/nuguard-oss/docs/sample-sbom.json)
- [docs/sbom-schema.md](/workspaces/nuguard-oss/docs/sbom-schema.md)

## Static Analysis

## `nuguard analyze` says the SBOM file is missing

Make sure the file exists and the path is correct:

```bash
ls -l app.sbom.json
nuguard analyze --sbom app.sbom.json
```

## External scanners are skipped

This is common and not always fatal.

Some analysis steps require external tools:

- `grype`
- `checkov`
- `trivy`
- `semgrep`

If they are missing, NuGuard may skip those checks and continue.

## `make lint` fails before typechecking

This repo’s `make lint` runs Ruff first and mypy second:

```bash
make lint
```

If Ruff fails, mypy will not run. To run typechecking directly:

```bash
mypy nuguard/
```

## Policy

## `nuguard policy validate` reports that the policy is empty

A policy with only headers and no content is structurally valid as a starter file, but the validator may report that all sections are empty.

Start from:

```bash
nuguard init
```

Then fill in sections such as:

- `Allowed Topics`
- `Restricted Topics`
- `Restricted Actions`
- `HITL Triggers`
- `Data Classification`
- `Rate Limits`

## `nuguard policy check` says no `--sbom` or `--policy` was provided

Either pass them explicitly:

```bash
nuguard policy check --policy cognitive_policy.md --sbom app.sbom.json
```

Or configure them in `nuguard.yaml`.

## Red-Team

## `nuguard redteam` says no target URL is available

Pass `--target` explicitly:

```bash
nuguard redteam --sbom app.sbom.json --target http://localhost:3000
```

Or configure `redteam.target` in `nuguard.yaml`.

## Guided redteam or LLM-enriched scenarios are not working

NuGuard may use two separate LLM roles during red-team runs:

- a `redteam` LLM for generating adversarial prompts and guided attack turns
- an `eval` LLM for judging ambiguous responses

### Redteam LLM requirements

The redteam LLM should not have a content filter or guardrail layer that blocks adversarial-security prompts. If it does, prompt generation and guided attack flows may fail or become ineffective.

In practice, the redteam model should:

- accept adversarial or security-testing prompts
- not be wrapped in safety middleware that refuses jailbreak, exfiltration, or abuse-oriented test content
- not inject provider-side guardrails that rewrite or suppress attack payloads

### Eval LLM requirements

The eval LLM is less strict. Any generally capable LLM can work here because it is used to assess whether a target response likely represents a violation or leak.

### What to check

- `redteam.llm.model` is set when you want guided conversations or LLM-enriched scenarios
- the redteam LLM API key is configured
- the chosen redteam model is not safety-blocking your test prompts
- `redteam.eval_llm.model` is set if you want LLM-based response evaluation

Example configuration:

```yaml
redteam:
  llm:
    model: openai/gpt-4.1
    api_key: ${NUGUARD_REDTEAM_LLM_API_KEY}

  eval_llm:
    model: openai/gpt-4.1-mini
    api_key: ${NUGUARD_REDTEAM_EVAL_LLM_API_KEY}
```

If guided scenarios are unexpectedly absent, check whether a redteam LLM was configured at all. Without one, NuGuard will fall back to non-guided scenario execution only.

## Red-team run hangs or times out

Possible causes:

- target app is not running
- target app is slow
- wrong chat endpoint
- auth header is missing

Things to check:

- target URL is reachable
- `redteam.target_endpoint` matches the app
- required auth is configured
- request timeout is large enough

Example config knobs:

```yaml
redteam:
  target: http://localhost:3000
  target_endpoint: /chat
  request_timeout: 120
```

## Canaries are not detected

Check:

- `canary.json` matches the actual seeded values
- the target app can really retrieve the seeded data
- you passed `--canary ./canary.json`

Start from:

```bash
cp canary.example.json canary.json
```

## Packaging

## Benchmarks or tests are ending up in build artifacts

Build locally and inspect the outputs:

```bash
uv build
tar -tzf dist/*.tar.gz | sed -n '1,200p'
```

This repo’s packaging config excludes:

- `/tests`
- `/nuguard/**/tests`
- repo-local clutter like `/.claude`, `/tmp`, and `/dist`

If artifact contents drift, inspect:

- [pyproject.toml](/workspaces/nuguard-oss/pyproject.toml)

## GitHub Actions and Trusted Publishing

## `workflow not found on the default branch`

GitHub cannot dispatch a workflow until that workflow file exists on the repo’s default branch.

If a workflow only exists on a feature branch:

1. merge it into `main`
2. then dispatch it

## `HTTP 403: Resource not accessible by integration`

This often means `gh` is using an integration-backed `GITHUB_TOKEN` instead of a PAT with `workflow` scope.

Try:

```bash
unset GITHUB_TOKEN
gh auth switch --user YOUR_GITHUB_USER
gh auth status
gh workflow run publish-testpypi.yml --ref main
```

## Trusted Publisher publish fails

Check the publisher registration in PyPI or TestPyPI:

- owner/org matches the GitHub org
- repo matches the GitHub repo
- workflow filename matches exactly
- environment matches exactly
- project name matches exactly

For this repo, expected values are:

- project: `nuguard`
- repo: `nuguard-oss`

## Git Issues

## `fatal: Unable to create .git/index.lock`

This usually means a previous git process left behind a lock file or another git process is still active.

Check for active git processes first. If none are running, remove the stale lock and retry.

Be careful not to remove the lock while another git operation is genuinely active.

## More References

- [docs/quick-start.md](/workspaces/nuguard-oss/docs/quick-start.md)
- [docs/cli-reference.md](/workspaces/nuguard-oss/docs/cli-reference.md)
- [docs/sbom-schema.md](/workspaces/nuguard-oss/docs/sbom-schema.md)
- [README.md](/workspaces/nuguard-oss/README.md)
