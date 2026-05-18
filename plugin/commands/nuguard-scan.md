---
name: nuguard-scan
description: Full NuGuard security scan — SBOM generation → static analysis → findings report
---

Run a complete NuGuard security scan on the current project and present a structured findings report.

Steps:
1. **Check for nuguard.yaml** — if not present, call `nuguard_init` first with `project_dir="."` so the user has a config file.

2. **Generate the AI-SBOM** — call `nuguard_sbom_generate` with `source="."` and `output="app.sbom.json"`.
   - Report: number of nodes detected (agents, models, tools, datastores, guardrails) and edge count.
   - If `node_count == 0`, warn the user and stop — there are no AI components to analyse.

3. **Static analysis** — call `nuguard_analyze` with `sbom="app.sbom.json"` and `min_severity="medium"`.
   - Use `nga_only=true` unless the user explicitly asked for external tool scans.

4. **Present findings** — group by severity (critical → high → medium), showing for each:
   - Rule ID and title
   - Affected component
   - One-sentence remediation

5. **Summary line** — e.g. "Found 3 findings: 0 critical · 2 high · 1 medium."

If the user passed `--full`, pass `nga_only=false` to enable all external scanners (Grype, Checkov, Trivy, Semgrep).
If the user passed `--policy PATH`, pass `policy=PATH` to `nuguard_analyze`.
If the user passed `--min-severity LEVEL`, use that value instead of `"medium"`.
