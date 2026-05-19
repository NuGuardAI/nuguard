---
name: nuguard-behavior
description: Intent-aware behavioral testing against a running AI application
allowed-tools: ["Bash"]
---

Run behavioral testing against a live AI application endpoint and interpret the results.

## Steps

1. **Detect nuguard** — check `which nuguard 2>/dev/null`. Use `nuguard` if on PATH, otherwise `uv run nuguard`.

2. **Resolve config** — use `--config PATH` if provided, otherwise look for `nuguard.yaml` in the current directory.
   If neither exists, tell the user to run `/nuguard-init` and fill in `target.url` before continuing.

3. **Run via Bash**:
   ```bash
   nuguard behavior --config nuguard.yaml --mode static+dynamic
   ```
   Flag mapping:
   - `--mode MODE` (default `static+dynamic`; options: `static`, `dynamic`, `static+dynamic`)
   - `--target URL` overrides `behavior.target` in nuguard.yaml
   - `--policy PATH` for Cognitive Policy check
   - `--fail-on LEVEL` (default `high`)
   - `--output PATH` to write the report to a file

4. **Interpret results**:
   - **Static findings** — SBOM–policy alignment gaps (no live traffic needed)
   - **Dynamic findings** — per-turn intent drift, policy violations, data leakage
   - For each finding: what was tested, what was observed, why it matters

5. If the command exits non-zero, check stderr and help the user diagnose the issue (target unreachable, missing auth config, etc.).

Available flags: `--config PATH`, `--mode static|dynamic|static+dynamic`, `--target URL`, `--policy PATH`, `--fail-on LEVEL`, `--output PATH`
