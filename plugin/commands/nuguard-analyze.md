---
name: nuguard-analyze
description: Static risk analysis on an AI-SBOM — NGA rules, MITRE ATLAS, CVE scans
---

Run static risk analysis on an AI-SBOM and explain the findings.

Steps:
1. **Resolve SBOM path** — use the path the user provided as an argument, or default to `app.sbom.json` in the current directory. If neither exists, tell the user to run `/nuguard-scan` first.

2. **Call `nuguard_analyze`** with:
   - `sbom` = resolved path
   - `min_severity` = user-supplied `--min-severity` or `"medium"`
   - `nga_only` = `true` unless `--full` flag is present
   - `enable_atlas` = `true` unless `--no-atlas` flag
   - `llm` = `true` if `--llm` flag is present

3. **For each finding**, explain:
   - What the rule detected and why it matters
   - The specific SBOM component affected
   - A concrete remediation step (not generic advice)
   - The MITRE ATLAS technique mapping if present

4. **Severity summary table** at the end.

Available flags: `--sbom PATH`, `--min-severity LEVEL`, `--full`, `--no-atlas`, `--llm`, `--verbose`
