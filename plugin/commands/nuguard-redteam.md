---
name: nuguard-redteam
description: Adversarial red-team testing — prompt injection, data exfiltration, privilege escalation, MCP toxic-flow
---

Run adversarial red-team testing against a live AI application and produce an actionable findings report.

Steps:
1. **Prerequisites check**:
   - Confirm `nuguard.yaml` exists (or `--config PATH` was supplied). If not, tell the user to run `/nuguard-init` first.
   - Confirm an SBOM exists (defaults to `sbom` path in nuguard.yaml or `app.sbom.json`). If not, tell the user to run `/nuguard-scan` first.
   - Remind the user their target app must be running and reachable.
   - Remind the user that `NUGUARD_REDTEAM_LLM_MODEL` must be set for guided conversations.

2. **Call `nuguard_redteam`** with:
   - `config_path` = resolved config path
   - `profile` = user-supplied `--profile` or `"ci"`
   - `sbom` = user-supplied `--sbom PATH`
   - `target` = user-supplied `--target URL`
   - `policy` = user-supplied `--policy PATH`
   - `guided` = `true` if `--guided` flag, `false` if `--no-guided`
   - `fail_on` = `"high"` unless `--fail-on` supplied

3. **For each confirmed finding**, explain:
   - Attack goal and scenario type (e.g. DATA_EXFILTRATION · cross-tenant PII leak)
   - The exact attack payload or conversation that triggered it
   - A quote from the model response as evidence
   - OWASP LLM Top 10 / MITRE ATLAS reference
   - Concrete remediation: what to change in the code or config to close the vulnerability

4. **Summary**: total findings by severity, which scenario families fired, and which were clean.

5. If `status == "timeout"`, tell the user to increase `timeout_seconds` or switch to `profile="ci"`.

Available flags: `--config PATH`, `--sbom PATH`, `--target URL`, `--policy PATH`, `--profile ci|full`, `--guided`, `--no-guided`, `--fail-on LEVEL`, `--scenarios LIST`
