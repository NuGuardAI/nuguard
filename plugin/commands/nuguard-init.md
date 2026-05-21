---
name: nuguard-init
description: Initialise nuguard.yaml, canary.example.json and cognitive-policy.md in the current project
allowed-tools: ["Read", "Write", "Bash"]
---

Initialise NuGuard configuration for this project.

## Steps

### 0. Load project config

Read `.claude/nuguard.local.md`.

- If the file **does not exist**, invoke `/nuguard-config` to collect LLM credentials and target
  settings before proceeding. Do not continue to Step 1 until the config exists.
- Extract from the frontmatter: `llm_model`, `llm_api_key`, `llm_api_base`, `llm_api_version`,
  `target_url`, `chat_endpoint`, `auth_type`, and auth credential fields.

### 1. Detect nuguard

Check `which nuguard 2>/dev/null`. Use `nuguard` if on PATH, otherwise `uv run nuguard`.

### 2. Run via Bash

```bash
nuguard init --target <target_url>
```

Flag mapping:
- `--target URL` — use `target_url` from the config (or from `--target` if the user supplied one)
- `--source PATH` if the user supplied a source directory
- `--force` if the user passed `--force` (overwrites existing files)
- `--path PATH` to write `nuguard.yaml` to a specific location (default: `./nuguard.yaml`)

Omit `--target` if no target URL is available.

### 3. Patch `nuguard.yaml` with config values

After `nuguard init` writes the skeleton `nuguard.yaml`, open it with the Read tool and patch
these fields using the Edit tool (only update values that exist in the config):

- **`llm.model`** → set to `llm_model` from config (default `gemini/gemini-2.0-flash`)
- **`target.url`** → set to `target_url` from config (if not already set by `--target`)
- **`target.endpoint`** → set to `chat_endpoint` if provided
- **`target.auth`** → write the auth block for the chosen `auth_type`:
  - `none` — leave commented out
  - `bearer_token` — `type: bearer` + `header: "Authorization: Bearer <token>"`
  - `api_key` — `type: api_key` + `header: "<header>: <value>"`
  - `basic` — `type: basic` with `username`/`password` using `${ENV_VAR}` syntax

Do **not** write the raw `llm_api_key` into `nuguard.yaml`. The CLI reads it from the
`LITELLM_API_KEY` environment variable, which NuGuard plugin commands inject automatically.

### 4. Report

Tell the user which files were **created** and which were **skipped** (already existed).

Show the immediate next steps:
- Run `/nuguard-sbom` to generate the AI bill of materials
- Run `/nuguard-scan` to start the full analysis pipeline

### 5. If files were skipped

Remind the user they can pass `--force` to overwrite.

The command creates up to three files:
- `nuguard.yaml` — main config (target URL, SBOM path, auth, scan settings)
- `canary.example.json` — template for seeding canary values before red-team
- `cognitive-policy.md` — blank policy template with required section headings
