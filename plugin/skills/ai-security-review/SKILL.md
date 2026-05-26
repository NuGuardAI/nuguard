---
name: ai-security-review
description: >
  Activate when the user asks to audit, review, scan, or assess the security of an AI
  application, agent, chatbot, or LLM-powered system. Also activate when the user mentions prompt injection, data exfiltration, guardrail bypass, red-teaming, AI SBOM, cognitive policy, OWASP LLM Top 10, NIST AI RMF, or EU AI Act compliance.
version: 0.5.3
---

You are conducting an AI application security review using NuGuard's pipeline.

## Workflow

Run steps in order. Each step builds on the previous one.

### Step 0 — Load project config

Read `.claude/nuguard.local.md`.

- If the file **does not exist**, invoke `/nuguard-config` to collect LLM credentials, target URL,
  and authentication settings before proceeding. Do not continue to Step 1 until the config exists.
- Extract these fields from the YAML frontmatter and use them throughout:
  - `llm_api_key` → inject as `LITELLM_API_KEY` env var on all CLI calls; add `--llm` flag when present
  - `llm_model`, `llm_api_base`, `llm_api_version` → LLM provider settings (already in `nuguard.yaml`)
  - `target_url`, `chat_endpoint` → used for behavior and redteam steps
  - `auth_type` + matching credential fields (`auth_token`, `auth_api_key_*`, `auth_username`, `auth_password`)

### Step 1 — Inventory (AI-SBOM)

Run `nuguard sbom generate --source .` via Bash (use `uv run nuguard` if `nuguard` is not on PATH).
The SBOM is the foundation: every subsequent step works from the components detected here.

Key things to surface from the SBOM summary:
- Which AI frameworks are in use (LangChain, OpenAI Agents SDK, CrewAI, etc.)
- Whether MCP servers are present (and whether they are trusted or untrusted)
- Datastores with PII/PHI classification
- Tools with `sql_injectable`, `ssrf_possible`, or `high_privilege` metadata
- Whether guardrail nodes exist and are wired to agent nodes

### Step 2 — Static Risk Analysis

Run `nuguard analyze --sbom app.sbom.json --min-severity medium` via Bash. Focus your interpretation on:

**High-priority NGA rules to highlight:**
- NGA-001: Agent without any guardrail node
- NGA-003: High-privilege tool without HITL trigger
- NGA-007: SQL-injectable tool parameter
- NGA-009: PII datastore without auth boundary
- NGA-011: Unauthenticated API endpoint on agent graph
- NGA-014: System prompt accessible via injection surface
- NGA-018: No audit trail node in graph

For each finding, map it to the OWASP LLM Top 10 item it corresponds to (LLM01–LLM10).

### Step 3 — Config and Policy Initialization

Before running the policy check, ensure `nuguard.yaml` and `cognitive-policy.md` exist
in the project directory. If missing, run via Bash:

```bash
# With LLM available: drafts a concise cognitive policy (5–6 topics per section)
nuguard init --llm

# Without LLM: writes blank section headings for manual fill-in
nuguard init
```

`nuguard init` creates:
- `nuguard.yaml` — config file pre-filled with the detected SBOM path, source directory,
  and any target URL the user has provided
- `cognitive-policy.md` — when `--llm` is passed, a concise LLM-drafted policy with
  5–6 allowed and restricted topics tailored to the application; otherwise blank headings
- `canary.example.json` — template for seeding canary values before red-team runs

If `nuguard.yaml` or `cognitive-policy.md` already exist, skip this step (pass
`force=false`, the default).

Once both files exist, run `nuguard policy check --policy cognitive-policy.md --sbom app.sbom.json` via Bash.
Explain gaps in plain language — what the policy declares vs. what the SBOM shows is actually enforced.

### Step 4 — Dynamic Validation (if live target available)

If the user provides a target URL:
- Run `nuguard behavior --config nuguard.yaml --mode static+dynamic` via Bash first (faster, no attack payloads)
- If behavior finds intent drift or policy violations, escalate to `nuguard redteam --config nuguard.yaml` to confirm exploitability

## Reporting Style

Present findings as a developer-facing security brief:
1. **Risk summary** — one paragraph, plain language, worst-case impact
2. **Findings table** — severity | rule | component | one-line description
3. **Top 3 fixes** — ranked by severity × exploitability, each with a specific code-level
   change (not "add authentication" but "add a `tenant_id` filter to the SQL query in
   `tools/db_tool.py` before execution")
4. **What's clean** — briefly note the components that passed so the user knows the
   scan was thorough

## Important Constraints

- Never fabricate findings. Only report what NuGuard tools return.
- If a tool returns `status: "error"`, diagnose the error before continuing.
- If `node_count == 0` from the SBOM, the extractor found no AI components — tell the
  user why (wrong source path, unsupported framework, etc.) before proceeding.
- Canary hits are always CRITICAL regardless of other signals. Flag them first.
