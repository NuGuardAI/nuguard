# NuGuard MCP Server

NuGuard's MCP server exposes AI security tools — SBOM generation, static analysis, behavioral testing, and adversarial red-teaming — directly to Claude. Ask Claude to audit your AI application and it will orchestrate the full pipeline, interpret findings, and recommend fixes without you leaving the chat.

---

## Installation

### Smithery (recommended)

The easiest way to install NuGuard for Claude Code or Claude Desktop:

```bash
smithery mcp add NuGuardAI/nuguard
```

Or open the listing at [smithery.ai/servers/NuGuardAI/nuguard](https://smithery.ai/servers/NuGuardAI/nuguard) and click **Connect**.

During install, Smithery asks for three optional settings:

| Setting | Description |
|---|---|
| `litellm_api_key` | API key for LLM-enriched analysis (Gemini, OpenAI, Anthropic, etc.) |
| `nuguard_config_path` | Absolute path to a `nuguard.yaml` config — sets the default for all tool calls |
| `redteam_llm_model` | LiteLLM model for red-team payload generation (e.g. `openai/gpt-4o`) |

Secrets are passed as environment variables to the `nuguard-mcp` process and never travel through Claude's context.

---

### Manual (Claude Desktop)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "nuguard": {
      "command": "uvx",
      "args": ["--from", "nuguard[mcp]", "nuguard-mcp"],
      "env": {
        "LITELLM_API_KEY": "your-api-key-here",
        "NUGUARD_DEFAULT_CONFIG": "/absolute/path/to/nuguard.yaml"
      }
    }
  }
}
```

Restart Claude Desktop after saving. The NuGuard tools appear in the tools panel (hammer icon).

---

### Manual (Claude Code)

Add a `.mcp.json` file at your project root:

```json
{
  "mcpServers": {
    "nuguard": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "nuguard[mcp]", "nuguard-mcp"]
    }
  }
}
```

Set environment variables in your shell or `.env`:

```bash
export LITELLM_API_KEY=your-api-key-here
export NUGUARD_DEFAULT_CONFIG=/absolute/path/to/nuguard.yaml
```

---

## Available Tools

### `nuguard_init`

Initialize a `nuguard.yaml` config file in a project directory. Also creates `canary.example.json` and `cognitive-policy.md`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_dir` | string | required | Directory to initialize |
| `target_url` | string | — | URL of the running AI application |
| `source_dir` | string | — | Source code directory for SBOM generation |
| `force` | bool | `false` | Overwrite existing files |

---

### `nuguard_sbom_generate`

Generate an AI Bill of Materials from source code or a git repository.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `source` | string | — | Local source directory |
| `from_repo` | string | — | Git repository URL |
| `ref` | string | `"main"` | Branch / tag / commit (with `from_repo`) |
| `output` | string | `"app.sbom.json"` | Output file path |
| `llm` | bool | `false` | Enable LLM enrichment |
| `config_path` | string | — | Path to `nuguard.yaml` |

Either `source` or `from_repo` must be provided.

---

### `nuguard_analyze`

Run static risk analysis on an AI-SBOM.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sbom` | string | required | Path to AI-SBOM JSON |
| `min_severity` | string | `"medium"` | `critical` \| `high` \| `medium` \| `low` \| `info` |
| `enable_atlas` | bool | `true` | MITRE ATLAS mapping |
| `enable_osv` | bool | `true` | OSV CVE scan |
| `enable_grype` | bool | `true` | Grype CVE scan |
| `enable_checkov` | bool | `true` | Checkov IaC scan |
| `enable_trivy` | bool | `true` | Trivy container scan |
| `enable_semgrep` | bool | `true` | Semgrep AI rules |
| `llm` | bool | `false` | LLM enrichment in ATLAS pass |
| `source` | string | — | Source directory for Checkov / Trivy / Semgrep |
| `config_path` | string | — | Path to `nuguard.yaml` |

---

### `nuguard_scan`

Run the full unified security scan pipeline: SBOM → analyze → policy → red-team.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `source` | string | `"."` | AI application source directory |
| `output_dir` | string | `"nuguard-reports"` | Directory for output files |
| `fail_on` | string | `"high"` | Severity threshold for non-zero exit |
| `steps` | string | `"sbom,analyze"` | Comma-separated steps: `sbom,analyze,policy,redteam` |
| `policy` | string | — | Path to Cognitive Policy Markdown |
| `target` | string | — | Live app URL (required for `redteam` step) |
| `llm` | bool | `false` | LLM enrichment |
| `config_path` | string | — | Path to `nuguard.yaml` |

Returns artifact paths (`sbom.json`, `findings.json`, `findings.sarif`, `report.md`) and a severity summary.

---

### `nuguard_behavior`

Run intent-aware behavioral testing against a live AI application.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `config_path` | string | required | Path to `nuguard.yaml` |
| `mode` | string | `"static+dynamic"` | `static` \| `dynamic` \| `static+dynamic` |
| `target` | string | — | Override `behavior.target` URL |
| `policy` | string | — | Path to Cognitive Policy Markdown |
| `output` | string | — | Write report to this file |
| `fail_on` | string | `"high"` | Severity threshold |

Static mode checks SBOM–policy alignment without hitting the live app. Dynamic mode sends probe conversations and judges each turn for intent drift, policy violations, and data leakage.

---

### `nuguard_redteam`

Run adversarial red-team testing against a live AI application.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `config_path` | string | required | Path to `nuguard.yaml` |
| `sbom` | string | — | Override SBOM path |
| `target` | string | — | Override target URL |
| `policy` | string | — | Path to Cognitive Policy Markdown |
| `profile` | string | `"ci"` | `ci` (fast, high-signal) \| `full` (comprehensive) |
| `scenarios` | string | — | Comma-separated scenario types to run |
| `output` | string | — | Write findings JSON to this file |
| `fail_on` | string | `"high"` | Severity threshold |
| `timeout_seconds` | int | `900` | Red-team scans can be long-running |

Requires a running target app and an LLM configured for attack payload generation (`NUGUARD_REDTEAM_LLM_MODEL`).

---

### `nuguard_policy_check`

Cross-check a Cognitive Policy against an AI-SBOM and run compliance assessments (OWASP LLM Top 10, NIST AI RMF, EU AI Act).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `policy` | string | — | Path to Cognitive Policy Markdown |
| `sbom` | string | — | Path to AI-SBOM JSON |
| `config_path` | string | — | Path to `nuguard.yaml` |
| `framework` | string | — | `owasp-llm-top10` \| `nist-ai-rmf` \| `eu-ai-act` |
| `output` | string | — | Write compliance report to this file |
| `verbose` | bool | `false` | Show all controls with evidence |

---

## Example

**User:** I've built a customer service AI agent at `~/projects/cs-bot`. Can you do a security audit?

**Claude** runs the full pipeline:

1. `nuguard_init(project_dir="~/projects/cs-bot", target_url="http://localhost:8080")`  
   → Creates `nuguard.yaml`, `cognitive-policy.md`, `canary.example.json`

2. `nuguard_sbom_generate(source="~/projects/cs-bot", output="app.sbom.json")`  
   → 14 nodes: 1 agent, gpt-4o model, PostgreSQL + email + web-search tools, 1 guardrail, 1 PII-classified datastore

3. `nuguard_analyze(sbom="app.sbom.json", min_severity="medium")`  
   → **HIGH** SQL-injectable PostgreSQL tool · **HIGH** PII datastore with no auth boundary · **MEDIUM** email tool with no HITL gate

4. `nuguard_redteam(config_path="nuguard.yaml", profile="ci")`  
   → **CRITICAL** cross-tenant PII leak confirmed · **HIGH** partial system prompt extracted via indirect injection

**Claude** summarizes findings and provides prioritized remediation steps.

---

## Environment Variables

| Variable | Description |
|---|---|
| `LITELLM_API_KEY` | API key for LLM-enriched analysis — accepts Gemini, OpenAI, Anthropic, and any LiteLLM-compatible provider |
| `NUGUARD_DEFAULT_CONFIG` | Absolute path to `nuguard.yaml` — used as the default `config_path` for all tool calls |
| `NUGUARD_REDTEAM_LLM_MODEL` | LiteLLM model string for red-team payload generation (e.g. `openai/gpt-4o`, `gemini/gemini-2.0-flash`) |
| `NUGUARD_REDTEAM_LLM_API_KEY` | API key specifically for the red-team LLM (if different from `LITELLM_API_KEY`) |

Set `NUGUARD_DEFAULT_CONFIG` to avoid repeating `config_path` in every tool call:

```bash
export NUGUARD_DEFAULT_CONFIG=/home/me/projects/cs-bot/nuguard.yaml
```

---

## Troubleshooting

**Tools don't appear in Claude Desktop**  
Restart Claude Desktop after editing `claude_desktop_config.json`. On macOS, Claude Desktop does not inherit shell PATH — use the absolute path to `uvx` if needed:
```json
{ "command": "/usr/local/bin/uvx" }
```

**`nuguard_redteam` times out**  
The default timeout is 900 s. For large `profile=full` scans pass `timeout_seconds=1800`. Also check `redteam.request_timeout` in `nuguard.yaml` — it should match your app's response SLA.

**`status: "error"` with no findings**  
Check the `stderr` field in the response. Common causes: `sbom` path doesn't exist, `config_path` points to a directory, or `LITELLM_API_KEY` is not set when `llm=true`.

**Grype / Checkov / Trivy not running**  
These are optional. Install them or disable them per-call:
```
nuguard_analyze(sbom="...", enable_grype=false, enable_trivy=false)
```
