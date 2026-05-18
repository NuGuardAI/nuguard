---
name: AI Application Security Review
description: >
  Activate when the user asks to audit, review, scan, or assess the security of an AI
  application, agent, chatbot, or LLM-powered system. Also activate when the user mentions prompt injection, data exfiltration, guardrail bypass, red-teaming, AI SBOM, cognitive policy, OWASP LLM Top 10, NIST AI RMF, or EU AI Act compliance.
version: 0.4.8
---

You are conducting an AI application security review using NuGuard's pipeline.

## Workflow

Run steps in order. Each step builds on the previous one.

### Step 0 — Load project config

Read `.claude/nuguard.local.md` if it exists. Extract these fields from the YAML frontmatter
and use them as defaults throughout:

- `llm_api_key` → pass `llm=true` on generate/analyze calls when present
- `llm_model`, `llm_api_base`, `llm_api_version` → LLM provider settings
- `target_url`, `chat_endpoint` → used for behavior and redteam steps
- `auth_type` + matching credential fields (`auth_token`, `auth_api_key_*`, `auth_username`, `auth_password`)

If the file does not exist, suggest the user run `/nuguard-config` to set up credentials,
then continue with any values provided in the conversation.

### Step 1 — Inventory (AI-SBOM)

Use the `nuguard_sbom_generate` MCP tool to scan the application source. The SBOM is
the foundation: every subsequent step works from the components detected here.

Key things to surface from the SBOM summary:
- Which AI frameworks are in use (LangChain, OpenAI Agents SDK, CrewAI, etc.)
- Whether MCP servers are present (and whether they are trusted or untrusted)
- Datastores with PII/PHI classification
- Tools with `sql_injectable`, `ssrf_possible`, or `high_privilege` metadata
- Whether guardrail nodes exist and are wired to agent nodes

### Step 2 — Static Risk Analysis

Use `nuguard_analyze` on the generated SBOM. Focus your interpretation on:

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
in the project directory. Use `nuguard_init` to create them if they are missing:

```
nuguard_init(project_dir="<project_dir>")
```

`nuguard_init` creates:
- `nuguard.yaml` — config file pre-filled with the detected SBOM path, source directory,
  and any target URL the user has provided
- `cognitive-policy.md` — starter policy document with sensible defaults the user can
  refine to match their application's intended behavior
- `canary.example.json` — template for seeding canary values before red-team runs

If `nuguard.yaml` or `cognitive-policy.md` already exist, skip this step (pass
`force=false`, the default).

Once both files exist, run `nuguard_policy_check` with the policy and SBOM paths and the
relevant compliance `--framework`. Explain gaps in plain language — what the policy
declares vs. what the SBOM shows is actually enforced.

### Step 4 — Dynamic Validation (if live target available)

If the user provides a target URL:
- Run `nuguard_behavior` in `static+dynamic` mode first (faster, no attack payloads)
- If behavior finds intent drift or policy violations, escalate to `nuguard_redteam`
  with `profile="full"` to confirm exploitability

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
