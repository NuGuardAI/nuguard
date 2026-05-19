---
name: nuguard-redteam
description: Adversarial red-team testing — prompt injection, data exfiltration, privilege escalation, MCP toxic-flow
allowed-tools: ["Bash"]
---

Run adversarial red-team testing against a live AI application and produce an actionable findings report.

## Steps

1. **Detect nuguard** — check `which nuguard 2>/dev/null`. Use `nuguard` if on PATH, otherwise `uv run nuguard`.

2. **Prerequisites check**:
   - Confirm `nuguard.yaml` exists (or `--config PATH` was supplied). If not, tell the user to run `/nuguard-init` first.
   - Confirm an SBOM exists at the path in `nuguard.yaml` or `app.sbom.json`. If not, tell the user to run `/nuguard-sbom` first.
   - Confirm the target app is running and reachable at `redteam.target` in `nuguard.yaml`.

3. **Run via Bash**:
   ```bash
   nuguard redteam --config nuguard.yaml
   ```
   Flag mapping:
   - `--config PATH` (required; default `nuguard.yaml`)
   - `--sbom PATH` overrides SBOM path from config
   - `--target URL` overrides `redteam.target` in config
   - `--policy PATH` for Cognitive Policy check
   - `--fail-on LEVEL` (default `high`)

4. **For each confirmed finding**, explain:
   - Attack goal and scenario type (e.g. `DATA_EXFILTRATION · cross-tenant PII leak`)
   - The exact payload or conversation that triggered it
   - A quote from the model response as evidence
   - OWASP LLM Top 10 / MITRE ATLAS reference
   - Concrete remediation: specific code or config change to close the vulnerability

5. **Summary** — total findings by severity, which scenario families fired, which were clean.

6. If the command times out, suggest increasing `redteam.scenario_timeout` in `nuguard.yaml` or using `nuguard redteam run --profile ci`.

Available flags: `--config PATH`, `--sbom PATH`, `--target URL`, `--policy PATH`, `--fail-on LEVEL`
