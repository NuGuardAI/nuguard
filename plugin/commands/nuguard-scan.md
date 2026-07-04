---
name: nuguard-scan
description: NuGuard unified scan — SBOM generation → static analysis, with optional policy/red-team validation
allowed-tools: ["Read", "Bash"]
---

Run a NuGuard security scan on the current project and present a structured findings report. The default CLI path runs SBOM generation plus static analysis; add `policy,redteam` to `--steps` for the full validation path.

## Steps

### 0. Load project config

Read `.claude/nuguard.local.md`.

- If the file **does not exist**, invoke `/nuguard-config` to collect LLM credentials and target
  settings before proceeding. Do not continue to Step 1 until the config exists.
- Extract from the frontmatter: `llm_api_key`, `llm_model`, `target_url`, and auth fields.
  `llm_api_key` is injected as `LITELLM_API_KEY` when running CLI commands below.

### 1. Detect nuguard

Check `which uv 2>/dev/null`. If `uv` is present, use `uv run nuguard`. Otherwise check `which nuguard 2>/dev/null`; if on PATH use `nuguard`. If neither is available, run `pip install nuguard` first.

### 2. Check for nuguard.yaml

If `nuguard.yaml` is not present, invoke `/nuguard-init` to generate and pre-fill it from the
project config before continuing.

### 3. Run the unified scan via Bash

```bash
LITELLM_API_KEY=<llm_api_key> nuguard scan --source . --steps sbom,analyze --llm
```

Omit `LITELLM_API_KEY=...` and `--llm` if no API key is in the config.

Flag mapping:
- `--source PATH` (default `.`)
- `--steps sbom,analyze` (default; use `sbom,analyze,policy,redteam` if the user explicitly requests full validation)
- `--llm` — enable LLM enrichment (add when `llm_api_key` is set)
- `--policy PATH` if `--policy` was supplied (required for the `policy` step)
- `--target URL` if `--target` was supplied (used by the `redteam` step; SBOM deployment URL may be used as fallback)
- `--fail-on LEVEL` (default `high`)
- `--full` → translate to `--steps sbom,analyze,policy,redteam`; do not pass `--full` through to the CLI

### 4. Present findings

Group by severity (critical → high → medium). For each finding:
- Rule ID and title
- Affected component
- One-sentence remediation

### 5. Summary line

E.g. `Found 3 findings: 0 critical · 2 high · 1 medium.`

### 6. Report artefacts

List the output files written (`sbom.json`, `findings.json`, `findings.sarif`, `report.md`).

Available flags: `--source PATH`, `--steps LIST`, `--policy PATH`, `--target URL`, `--fail-on LEVEL`, `--llm`, `--full`
