---
name: ai-security-review
description: >
  This skill should be used when the user asks to "audit my AI app", "review my agent's
  security", "run a security scan", "pentest my chatbot", or mentions prompt injection,
  data exfiltration, guardrail bypass, red-teaming, AI SBOM, cognitive policy, OWASP LLM
  Top 10, NIST AI RMF, or EU AI Act compliance for an AI application.
version: 0.5.4
---

Run NuGuard's security pipeline against an AI application and report the results as a
developer-facing brief. Run the steps in order — each one builds on the last.

## Step 0 — Load project config

Read `.claude/nuguard.local.md`. If it does not exist, invoke `/nuguard-config` to collect
LLM credentials, target URL, and auth settings, then continue.

Use its fields throughout: `llm_api_key` → `LITELLM_API_KEY` env var (add `--llm` when
present); `llm_model` / `llm_api_base` / `llm_api_version` → LLM provider settings;
`target_url` / `chat_endpoint` → behavior and redteam steps; `auth_type` plus its matching
credential fields.

## Step 1 — Inventory (AI-SBOM)

Run `nuguard sbom generate --source .` (or `uv run nuguard ...` if not on PATH). This is the
foundation every later step reads from.

Surface from the summary: AI frameworks in use, MCP servers and their trust level,
datastores with PII/PHI, tools flagged `sql_injectable` / `ssrf_possible` / `high_privilege`,
and whether guardrail nodes are wired to agent nodes.

## Step 2 — Static risk analysis

Run `nuguard analyze --sbom app.sbom.json --min-severity medium`. Highlight the
high-priority NGA rules: agent with no guardrail (NGA-001), high-privilege tool with no HITL
trigger (NGA-003), SQL-injectable parameter (NGA-007), PII datastore with no auth boundary
(NGA-009), unauthenticated API endpoint (NGA-011), system prompt exposed via injection
surface (NGA-014), missing audit trail (NGA-018). Map each finding to its OWASP LLM Top 10
item (LLM01–LLM10).

## Step 3 — Config and policy init

If `nuguard.yaml` or `cognitive-policy.md` are missing, build them from the repo:

```bash
nuguard init --llm    # drafts a concise cognitive policy (5-6 topics/section) from the SBOM
nuguard init          # writes blank section headings instead
```

Skip this if both files already exist. Then run
`nuguard policy check --policy cognitive-policy.md --sbom app.sbom.json` and explain any
gap between what the policy declares and what the SBOM shows is actually enforced.

## Step 4 — Target verification (only if a live target is available)

Before sending any real traffic, run `nuguard target verify --config nuguard.yaml`. It
sends one probe per declared credential (default + canary tenants), confirms auth and
connectivity, and — when an SBOM is present — runs a short pre-scan discovery conversation
that surfaces which account/tenant NuGuard is actually scanning. Confirm that identity with
the user before proceeding; a failed or unexpected-account result means fix credentials
first rather than continuing to Step 5.

## Step 5 — Behavior validation

Run `nuguard validate --config nuguard.yaml` — a happy-path and policy-compliance runner
that checks whether the app does what it claims and stays inside the topics declared in
`cognitive-policy.md`, without adversarial payloads. Treat any failure here as high-priority:
it means the app breaks its own declared contract under normal use.

## Step 6 — Dynamic validation

Run `nuguard behavior --config nuguard.yaml --mode static+dynamic` next — it's intent-aware
(checks for drift from the declared purpose) and still sends no attack payloads. If it finds
intent drift or policy violations, escalate to `nuguard redteam --config nuguard.yaml` to
confirm exploitability.

`target verify`, `behavior`, and `redteam` all auto-discover the chat endpoint from the
SBOM, including two-step create-conversation-then-post-message APIs — no manual
`chat_endpoint` needed unless discovery fails (tune breadth with `preflight_candidates` in
`nuguard.yaml`, default 3).

If a run stops early citing an exhausted usage quota or plan limit, tell the user to raise
the target's quota and re-run — that is not an auth or config problem.

## Reporting style

1. **Risk summary** — one paragraph, plain language, worst-case impact
2. **Findings table** — severity | rule | component | one-line description
3. **Top 3 fixes** — ranked by severity × exploitability, each a specific code-level change
   ("add a `tenant_id` filter to the SQL query in `tools/db_tool.py`", not "add authentication")
4. **What's clean** — the components that passed, so the user knows the scan was thorough

## Constraints

- Never fabricate findings — report only what NuGuard tools return.
- If a tool returns `status: "error"`, diagnose it before continuing.
- If `node_count == 0`, the extractor found no AI components — explain why (wrong source
  path, unsupported framework) before proceeding.
- Canary hits are always CRITICAL — flag them first, regardless of other signals.
