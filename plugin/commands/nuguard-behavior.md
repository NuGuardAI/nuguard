---
name: nuguard-behavior
description: Intent-aware behavioral testing against a running AI application
allowed-tools: ["Read", "Bash"]
---

Run behavioral testing against a live AI application endpoint and interpret the results.

## Steps

### 0. Load project config

Read `.claude/nuguard.local.md`.

- If the file **does not exist**, invoke `/nuguard-config` to collect LLM credentials and target
  settings before proceeding. Do not continue to Step 1 until the config exists.
- Extract from the frontmatter: `llm_api_key`, `llm_model`, `target_url`, `auth_type`, and auth
  credential fields. These are used to build the CLI environment and to ensure `nuguard.yaml`
  has the correct values.

### 1. Detect nuguard

Check `which nuguard 2>/dev/null`. Use `nuguard` if on PATH, otherwise `uv run nuguard`.

### 2. Resolve config

Use `--config PATH` if provided, otherwise look for `nuguard.yaml` in the current directory.
If `nuguard.yaml` does not exist, invoke `/nuguard-init` to generate it (it will be pre-filled
from `.claude/nuguard.local.md` automatically). Do not continue until `nuguard.yaml` exists.

### 3. Run via Bash

```bash
LITELLM_API_KEY=<llm_api_key> nuguard behavior --config nuguard.yaml --mode static+dynamic
```

Omit `LITELLM_API_KEY=...` if no API key is in the config.

Flag mapping:
- `--mode MODE` (default `static+dynamic`; options: `static`, `dynamic`, `static+dynamic`)
- `--target URL` overrides `behavior.target` in nuguard.yaml
- `--policy PATH` for Cognitive Policy check
- `--fail-on LEVEL` (default `high`)
- `--output PATH` to write the report to a file

### 4. Interpret results

- **Static findings** — SBOM–policy alignment gaps (no live traffic needed)
- **Dynamic findings** — per-turn intent drift, policy violations, data leakage
- For each finding: what was tested, what was observed, why it matters

### 5. Diagnose errors

If the command exits non-zero, check stderr and help the user diagnose the issue
(target unreachable, missing auth config, API key invalid, etc.).

Available flags: `--config PATH`, `--mode static|dynamic|static+dynamic`, `--target URL`, `--policy PATH`, `--fail-on LEVEL`, `--output PATH`
