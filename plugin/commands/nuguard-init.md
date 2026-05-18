---
name: nuguard-init
description: Initialise nuguard.yaml, canary.example.json and cognitive-policy.md in the current project
---

Initialise NuGuard configuration for this project.

Steps:
1. Call the `nuguard_init` MCP tool with `project_dir` set to the current working directory.
   - If the user supplied a `--target` URL argument, pass it as `target_url`.
   - If the user supplied a `--source` path, pass it as `source_dir`.
   - If the user passed `--force`, set `force=true`.
2. Report which files were **created** and which were **skipped** (already existed).
3. If `nuguard.yaml` was created, show the user the three immediate next steps printed in the tool response.
4. If any files were skipped, remind the user they can pass `--force` to overwrite them.

The tool creates three files in the project directory:
- `nuguard.yaml` — main config (target URL, SBOM path, auth, scan settings)
- `canary.example.json` — template for seeding canary values before a red-team scan
- `cognitive-policy.md` — blank policy template with required section headings
