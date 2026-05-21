---
name: nuguard-analyze
description: Static risk analysis on an AI-SBOM — NGA rules, MITRE ATLAS, CVE scans
allowed-tools: ["Read", "Bash"]
---

Run static risk analysis on an AI-SBOM and explain the findings.

## Steps

### 0. Load project config

Read `.claude/nuguard.local.md`.

- If the file **does not exist**, invoke `/nuguard-config` to collect LLM credentials and target
  settings before proceeding. Do not continue to Step 1 until the config exists.
- Extract `llm_api_key` and `llm_model` from the frontmatter. If `llm_api_key` is present,
  prepend `LITELLM_API_KEY=<value>` to the CLI command in Step 2 and add the `--llm` flag to
  enable LLM-enriched analysis.

### 1. Detect nuguard

Check `which nuguard 2>/dev/null`. Use `nuguard` if on PATH, otherwise `uv run nuguard`.

### 2. Resolve SBOM path

Use `--sbom PATH` if provided, otherwise default to `app.sbom.json` in the current directory.
If neither exists, tell the user to run `/nuguard-sbom` first.

### 3. Build the command and run via Bash

```bash
LITELLM_API_KEY=<llm_api_key> nuguard analyze --sbom app.sbom.json --min-severity medium --llm
```

Omit `LITELLM_API_KEY=...` and `--llm` if no API key is available in the config.

Flag mapping:
- `--min-severity LEVEL` (default `medium`)
- `--llm` — enable LLM-enriched descriptions (add when `llm_api_key` is set)
- `--nga` if `--nga` flag passed (skips OSV/Grype/Checkov/Trivy/Semgrep)
- `--no-atlas` if `--no-atlas` flag passed
- `--config PATH` if `--config` supplied
- `--format markdown` (default; use `--format json` for machine-readable output)

### 4. For each finding, explain

- What the rule detected and why it matters
- The specific component affected
- A concrete remediation step (not generic advice)
- The MITRE ATLAS technique mapping if present

### 5. Severity summary table at the end

Available flags: `--sbom PATH`, `--min-severity LEVEL`, `--nga`, `--no-atlas`, `--config PATH`, `--format FORMAT`
