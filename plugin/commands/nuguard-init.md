---
name: nuguard-init
description: Initialise nuguard.yaml, canary.example.json and cognitive-policy.md in the current project
allowed-tools: ["Bash"]
---

Initialise NuGuard configuration for this project.

## Steps

1. **Detect nuguard** — check `which nuguard 2>/dev/null`. Use `nuguard` if on PATH, otherwise `uv run nuguard`.

2. **Run via Bash**:
   ```bash
   nuguard init
   ```
   Flag mapping:
   - `--target URL` if the user supplied a target URL
   - `--source PATH` if the user supplied a source directory
   - `--force` if the user passed `--force` (overwrites existing files)
   - `--path PATH` to write `nuguard.yaml` to a specific location (default: `./nuguard.yaml`)

3. **Report** which files were **created** and which were **skipped** (already existed).

4. If `nuguard.yaml` was created, show the user the immediate next steps:
   - Fill in `llm.api_key` (or set `LITELLM_API_KEY`)
   - Set `redteam.target` / `behavior.target` to their app URL
   - Run `/nuguard-scan` to start the analysis

5. If files were skipped, remind the user they can pass `--force` to overwrite.

The command creates up to three files:
- `nuguard.yaml` — main config (target URL, SBOM path, auth, scan settings)
- `canary.example.json` — template for seeding canary values before red-team
- `cognitive-policy.md` — blank policy template with required section headings
