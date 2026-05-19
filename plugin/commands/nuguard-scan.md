---
name: nuguard-scan
description: Full NuGuard security scan — SBOM generation → static analysis → findings report
allowed-tools: ["Bash"]
---

Run a complete NuGuard security scan on the current project and present a structured findings report.

## Steps

1. **Detect nuguard** — check `which nuguard 2>/dev/null`. Use `nuguard` if on PATH, otherwise `uv run nuguard`.

2. **Check for nuguard.yaml** — if not present, run `nuguard init` first so the user has a config file.

3. **Run the unified scan** via Bash:
   ```bash
   nuguard scan --source . --steps sbom,analyze
   ```
   Flag mapping:
   - `--source PATH` (default `.`)
   - `--steps sbom,analyze` (default; add `policy,redteam` if user explicitly requests them)
   - `--policy PATH` if `--policy` was supplied
   - `--target URL` if `--target` was supplied (required for `redteam` step)
   - `--min-severity LEVEL` (default `medium`)
   - `--fail-on LEVEL` (default `high`)
   - `--llm` if user passed `--llm`
   - `--config PATH` if `--config` was supplied
   - `--full` → set `--steps sbom,analyze,policy,redteam`

4. **Present findings** — group by severity (critical → high → medium). For each finding:
   - Rule ID and title
   - Affected component
   - One-sentence remediation

5. **Summary line** — e.g. `Found 3 findings: 0 critical · 2 high · 1 medium.`

6. **Report artefacts** — list the output files written (`app.sbom.json`, `findings.json`, `findings.sarif`, `report.md`).

Available flags: `--source PATH`, `--steps LIST`, `--policy PATH`, `--target URL`, `--min-severity LEVEL`, `--fail-on LEVEL`, `--llm`, `--full`, `--config PATH`
