# Publishing

This repo includes GitHub Actions workflows for Trusted Publishing to TestPyPI and PyPI:

- [publish-testpypi.yml](../.github/workflows/publish-testpypi.yml)
- [publish-pypi.yml](../.github/workflows/publish-pypi.yml)

Before the workflows can publish, configure Trusted Publishers in TestPyPI and PyPI for the `nuguard` project with:

- owner/org: `NuGuardAI`
- repository: `nuguard`
- workflow file: `publish-testpypi.yml` or `publish-pypi.yml`
- environment: `testpypi` or `pypi`

Recommended release flow:

1. Run the TestPyPI workflow manually from GitHub Actions.
2. Verify the package install and CLI behavior from TestPyPI.
3. Create a GitHub release to trigger the PyPI publish workflow.

## Pre-Publish Sanity Checks

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
