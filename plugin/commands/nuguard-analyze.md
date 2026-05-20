---
name: nuguard-analyze
description: Static risk analysis on an AI-SBOM — NGA rules, MITRE ATLAS, CVE scans
allowed-tools: ["Bash"]
---

Run static risk analysis on an AI-SBOM and explain the findings.

## Steps

1. **Detect nuguard** — check `which nuguard 2>/dev/null`. Use `nuguard` if on PATH, otherwise `uv run nuguard`.

2. **Resolve SBOM path** — use `--sbom PATH` if provided, otherwise default to `app.sbom.json` in the current directory.
   If neither exists, tell the user to run `/nuguard-sbom` first.

3. **Build the command** and run via Bash:
   ```bash
   nuguard analyze --sbom app.sbom.json --min-severity medium
   ```
   Flag mapping:
   - `--min-severity LEVEL` (default `medium`)
   - `--nga` if `--nga-only` flag passed (skips OSV/Grype/Checkov/Trivy/Semgrep)
   - `--no-atlas` if `--no-atlas` flag passed
   - `--config PATH` if `--config` supplied
   - `--format markdown` (default; use `--format json` for machine-readable output)

4. **For each finding**, explain:
   - What the rule detected and why it matters
   - The specific component affected
   - A concrete remediation step (not generic advice)
   - The MITRE ATLAS technique mapping if present

5. **Severity summary table** at the end.

Available flags: `--sbom PATH`, `--min-severity LEVEL`, `--nga-only`, `--no-atlas`, `--config PATH`, `--format FORMAT`
