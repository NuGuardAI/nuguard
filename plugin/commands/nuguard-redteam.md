---
name: nuguard-redteam
description: Adversarial red-team testing — prompt injection, data exfiltration, privilege escalation, MCP toxic-flow
allowed-tools: ["Read", "Bash"]
---

Run adversarial red-team testing against a live AI application and produce an actionable findings report.

## Steps

### 0. Load project config

Read `.claude/nuguard.local.md`.

- If the file **does not exist**, invoke `/nuguard-config` to collect LLM credentials and target
  settings before proceeding. Do not continue to Step 1 until the config exists.
- Extract from the frontmatter: `llm_api_key`, `llm_model`, `target_url`, `auth_type`, and auth
  credential fields. These are used to inject the API key and verify the target is configured.

### 1. Detect nuguard

Check `which nuguard 2>/dev/null`. Use `nuguard` if on PATH, otherwise `uv run nuguard`.

### 2. Prerequisites check

- Confirm `nuguard.yaml` exists (or `--config PATH` was supplied). If not, invoke `/nuguard-init`
  to generate it before continuing.
- Confirm an SBOM exists at the path in `nuguard.yaml` or `app.sbom.json`. If not, tell the user
  to run `/nuguard-sbom` first.
- Confirm the target app is running and reachable at `redteam.target` in `nuguard.yaml` (or
  `target_url` from the config). If the URL in the config is still a placeholder, ask the user
  to update it before proceeding.

### 3. Run via Bash

```bash
LITELLM_API_KEY=<llm_api_key> nuguard redteam --config nuguard.yaml
```

Omit `LITELLM_API_KEY=...` if no API key is in the config.

Flag mapping:
- `--config PATH` (required; default `nuguard.yaml`)
- `--sbom PATH` overrides SBOM path from config
- `--target URL` overrides `redteam.target` in config
- `--policy PATH` for Cognitive Policy check
- `--fail-on LEVEL` (default `high`)

### 4. For each confirmed finding, explain

- Attack goal and scenario type (e.g. `DATA_EXFILTRATION · cross-tenant PII leak`)
- The exact payload or conversation that triggered it
- A quote from the model response as evidence
- OWASP LLM Top 10 / MITRE ATLAS reference
- Concrete remediation: specific code or config change to close the vulnerability

### 5. Summary

Total findings by severity, which scenario families fired, which were clean.

### 6. Timeout guidance

If the command times out, suggest increasing `redteam.scenario_timeout` in `nuguard.yaml`
or using `nuguard redteam --profile ci`.

Available flags: `--config PATH`, `--sbom PATH`, `--target URL`, `--policy PATH`, `--fail-on LEVEL`
