---
name: nuguard-behavior
description: Intent-aware behavioral testing against a running AI application
---

Run behavioral testing against a live AI application endpoint and interpret the results.

Steps:
1. **Resolve config** — use `--config PATH` argument if provided, otherwise look for `nuguard.yaml` in the current directory. If neither exists, tell the user to run `/nuguard-init` and fill in `target.url` before continuing.

2. **Call `nuguard_behavior`** with:
   - `config_path` = resolved config path
   - `mode` = user-supplied `--mode` or `"static+dynamic"`
   - `target` = user-supplied `--target` URL (overrides nuguard.yaml)
   - `policy` = user-supplied `--policy PATH`
   - `fail_on` = user-supplied `--fail-on` or `"high"`

3. **Interpret results**:
   - **Static findings** — SBOM–policy alignment gaps (no live traffic needed)
   - **Dynamic findings** — per-turn intent drift, policy violations, data leakage detected during probe conversations
   - For each finding: what was tested, what was observed, and why it matters

4. If `status == "error"`, check `stderr` in the response and help the user diagnose the issue (e.g. target unreachable, missing auth config).

Available flags: `--config PATH`, `--mode static|dynamic|static+dynamic`, `--target URL`, `--policy PATH`, `--fail-on LEVEL`
