# NuGuard MCP Plugin

The NuGuard MCP plugin exposes NuGuard's AI security capabilities — SBOM generation, static analysis, behavioral testing, and adversarial red-teaming — as tools that Claude can call directly in a conversation. You can ask Claude to audit your AI application and Claude will orchestrate the full NuGuard pipeline, interpret findings, and recommend remediations without you leaving the chat.

---

## Table of Contents

1. [Installation](#installation)
2. [Quick Setup](#quick-setup)
3. [Claude Desktop Setup](#claude-desktop-setup)
4. [Claude Code Plugin](#claude-code-plugin)
5. [Smithery (Claude Marketplace)](#smithery-claude-marketplace)
6. [Available Tools](#available-tools)
7. [Example Scenario](#example-scenario)
8. [Environment Variables](#environment-variables)
9. [Configuration File](#configuration-file)
10. [Troubleshooting](#troubleshooting)

---

## Installation

Choose the installation method that best fits your environment:

### Python (pip / uvx)

The MCP server is bundled in the `nuguard` package as the optional `mcp` extra.

```bash
pip install "nuguard[mcp]"
```

Verify the server starts cleanly:

```bash
nuguard-mcp --help
```

Or run directly without installing (using `uvx`):

```bash
uvx --from "nuguard[mcp]" nuguard-mcp
```

### Node.js (npm / npx)

A thin npm wrapper is available for environments where Node.js is preferred or where Smithery requires an npm-backed entry point. It delegates to `uvx` internally, so `uv` must be installed ([astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)).

```bash
# Run without installing (recommended for one-off use)
npx nuguard nuguard-mcp

# Or install globally
npm install -g nuguard
nuguard-mcp
```

Install `uv` if it is not already present:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Quick Setup

`scripts/register_mcp.py` registers `nuguard-mcp` with Claude Desktop **and** writes `.mcp.json` in the current directory (for Claude Code) without manual JSON editing.

```bash
# Register with defaults (reads LITELLM_API_KEY from environment)
python scripts/register_mcp.py

# Register with an explicit API key and config path
python scripts/register_mcp.py \
    --api-key sk-... \
    --config /absolute/path/to/nuguard.yaml \
    --redteam-model openai/gpt-4o

# Preview what would be written without modifying any files
python scripts/register_mcp.py --dry-run

# Remove the nuguard entry from all targets
python scripts/register_mcp.py --unregister
```

The script detects whether `nuguard-mcp` is on your `PATH` and uses it directly, falling back to `uvx --from "nuguard[mcp]" nuguard-mcp` if not. Restart Claude Desktop after running.

| Flag | Description |
|---|---|
| `--api-key KEY` | Embed `LITELLM_API_KEY` in the server `env` block |
| `--config PATH` | Embed `NUGUARD_DEFAULT_CONFIG` (default config for all tool calls) |
| `--redteam-model MODEL` | Embed `NUGUARD_REDTEAM_LLM_MODEL` (e.g. `openai/gpt-4o`) |
| `--dry-run` | Print changes without writing any files |
| `--unregister` | Remove the `nuguard` entry from all targets |
| `--skip-desktop` | Skip updating `claude_desktop_config.json` |
| `--skip-mcp-json` | Skip writing `.mcp.json` in the current directory |

---

## Claude Desktop Setup

Add NuGuard to Claude Desktop's MCP server list. Edit
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "nuguard": {
      "command": "nuguard-mcp",
      "args": [],
      "env": {
        "LITELLM_API_KEY": "your-api-key-here",
        "NUGUARD_DEFAULT_CONFIG": "/path/to/your/nuguard.yaml"
      }
    }
  }
}
```

Restart Claude Desktop. The NuGuard tools will appear in the tools panel (hammer icon).

### Using uvx instead of a local install

If you prefer not to install nuguard globally:

```json
{
  "mcpServers": {
    "nuguard": {
      "command": "uvx",
      "args": ["--from", "nuguard[mcp]", "nuguard-mcp"],
      "env": {
        "LITELLM_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

### Using npx (Node.js)

If Node.js and `uv` are available but Python is not on your PATH:

```json
{
  "mcpServers": {
    "nuguard": {
      "command": "npx",
      "args": ["nuguard", "nuguard-mcp"],
      "env": {
        "LITELLM_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

---

## Claude Code Plugin

The `plugin/` directory ships a ready-to-install Claude Code plugin. It wires up the MCP server and adds five slash commands, two auto-activating skills, and an autonomous security-auditor agent.

### Install the Plugin

```bash
claude plugin install /path/to/nuguard/plugin
```

Enable the plugin in Claude Code settings. Claude Code starts the `nuguard-mcp` server automatically when the plugin is active.

### Slash Commands

| Command | Description |
|---|---|
| `/nuguard-init` | Initialize `nuguard.yaml` in the current project |
| `/nuguard-scan` | SBOM generation + static analysis in one step |
| `/nuguard-analyze` | Static analysis on an existing SBOM |
| `/nuguard-behavior` | Behavioral testing against a live app |
| `/nuguard-redteam` | Adversarial red-team scan |

Available flags mirror the MCP tool parameters. Run `/nuguard-redteam --help` (or any command with `--help`) to see options.

### Auto-Activating Skills

Two skills activate automatically based on conversation context — you do not invoke them directly:

**AI Application Security Review** — activates when you ask Claude to audit, review, scan, or assess an AI application. Runs the full four-step pipeline (SBOM → analyze → policy → dynamic validation) and presents a developer-facing security brief with a findings table and prioritized fixes.

**AI SBOM Analysis** — activates when you open or mention a `.sbom.json` file, or ask about what AI components an application uses. Interprets node types, edges, and risk signals, and answers questions about your AI application's component graph.

### Security Auditor Agent

The `security-auditor` agent runs the complete NuGuard pipeline end-to-end without stopping for confirmation between steps. Trigger it by describing your goal:

> "Audit my AI app at `~/projects/my-agent`"  
> "Find vulnerabilities in my LangChain service"  
> "Run nuguard on this"

The agent calls `nuguard_init` → `nuguard_sbom_generate` → `nuguard_analyze` → (if a target URL is configured) `nuguard_behavior` → `nuguard_redteam`, then produces a structured report with an executive summary, findings table, and top priority fixes.

---

## Smithery (Claude Marketplace)

NuGuard is published on [Smithery](https://smithery.ai/server/NuGuardAI/nuguard) as an npm-backed MCP server. The Smithery listing launches `nuguard-mcp` via the `nuguard` npm package, which delegates to `uvx` internally — no manual Python setup required on the host.

### Install via Smithery CLI

```bash
# Add to Claude Code
smithery mcp add NuGuardAI/nuguard --client claude-code

# Add to Claude Desktop
smithery mcp add NuGuardAI/nuguard --client claude
```

### Install via Smithery UI

Open the [NuGuard listing](https://smithery.ai/server/NuGuardAI/nuguard) and click **Connect**. Configure the optional settings:

| Setting | Description |
|---|---|
| `litellm_api_key` | API key for LLM-enriched analysis (Gemini, OpenAI, etc.) |
| `nuguard_config_path` | Absolute path to a project `nuguard.yaml` (sets the default for all tool calls) |
| `redteam_llm_model` | LiteLLM model string for red-team payload generation |

Smithery passes these settings as environment variables to the `nuguard-mcp` process, so secrets never travel through Claude's context.

---

## Available Tools

All tools accept an optional `timeout_seconds` parameter to override the default per-tool timeout.

### `nuguard_init`

Initialize a `nuguard.yaml` config file in a project directory.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_dir` | string | required | Directory to initialize |
| `target_url` | string | — | URL of the running AI application |
| `source_dir` | string | — | Source code directory for SBOM generation |
| `force` | bool | `false` | Overwrite existing files |
| `timeout_seconds` | int | `30` | |

Creates `nuguard.yaml`, `canary.example.json`, and `cognitive-policy.md` with auto-detected defaults (existing SBOM files, project language).

---

### `nuguard_sbom_generate`

Generate an AI Bill of Materials from source code or a git repository.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `source` | string | — | Local source directory |
| `from_repo` | string | — | Git repository URL |
| `ref` | string | `"main"` | Branch / tag / commit (used with `from_repo`) |
| `output` | string | `"app.sbom.json"` | Output file path |
| `llm` | bool | `false` | Enable LLM enrichment |
| `config_path` | string | — | Path to `nuguard.yaml` |
| `timeout_seconds` | int | `300` | |

Returns the SBOM path, node/edge counts, and a component summary. Either `source` or `from_repo` must be provided.

---

### `nuguard_analyze`

Run static risk analysis on an AI-SBOM.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sbom` | string | required | Path to AI-SBOM JSON |
| `nga_only` | bool | `false` | Run only 18 NGA structural rules |
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
| `timeout_seconds` | int | `300` | |

Exit status: `"ok"` (no findings), `"findings"` (issues detected), `"error"` (crash).

---

### `nuguard_scan`

Run the full unified security scan pipeline.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `source` | string | `"."` | AI application source directory |
| `output_dir` | string | `"nuguard-reports"` | Directory for output files |
| `fail_on` | string | `"high"` | Severity threshold for non-zero exit |
| `steps` | string | `"sbom,analyze"` | Comma-separated steps: `sbom,analyze,policy,redteam` |
| `policy` | string | — | Path to Cognitive Policy Markdown |
| `target` | string | — | Live app URL (required for `redteam` step) |
| `container_image` | string | — | Container image ref for Trivy |
| `llm` | bool | `false` | LLM enrichment in ATLAS pass |
| `config_path` | string | — | Path to `nuguard.yaml` |
| `timeout_seconds` | int | `600` | |

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
| `intent` | string | — | Override app intent description |
| `output` | string | — | Write report to this file |
| `fail_on` | string | `"high"` | Severity threshold |
| `timeout_seconds` | int | `300` | |

Static mode checks SBOM–policy alignment without hitting the live app. Dynamic mode sends probe conversations to the running app and judges each turn.

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
| `scenarios` | string | — | Comma-separated scenario types |
| `min_impact_score` | float | `0.0` | Minimum pre-impact score [0–10] |
| `output` | string | — | Write findings JSON to this file |
| `fail_on` | string | `"high"` | Severity threshold |
| `guided` | bool | — | Enable / disable guided multi-turn conversations |
| `guided_max_turns` | int | — | Max turns per guided conversation |
| `guided_concurrency` | int | — | Max parallel guided conversations |
| `timeout_seconds` | int | `900` | Red-team scans can be long-running |

Requires a running target application and an LLM configured for attack payload generation (`NUGUARD_REDTEAM_LLM_MODEL` env var).

---

### `nuguard_policy_check`

Cross-check a Cognitive Policy against an AI-SBOM and run compliance assessments.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `policy` | string | — | Path to Cognitive Policy Markdown |
| `sbom` | string | — | Path to AI-SBOM JSON |
| `config_path` | string | — | Path to `nuguard.yaml` |
| `framework` | string | — | `owasp-llm-top10` \| `nist-ai-rmf` \| `eu-ai-act` |
| `output` | string | — | Write compliance report to this file |
| `verbose` | bool | `false` | Show all controls with evidence |
| `timeout_seconds` | int | `60` | |

---

## Example Scenario

This walkthrough shows Claude running a full security audit on a LangChain-based customer service bot.

### Setup

The developer has a LangChain application at `~/projects/cs-bot`. The app uses an OpenAI model, a PostgreSQL tool, and a custom guardrail. No prior NuGuard config exists.

### Conversation

**User:** I've built a customer service AI agent at `~/projects/cs-bot`. Can you do a security audit?

**Claude** calls `nuguard_init`:
```
nuguard_init(project_dir="~/projects/cs-bot", target_url="http://localhost:8080")
```
Result: `nuguard.yaml`, `cognitive-policy.md`, and `canary.example.json` created in the project directory.

**Claude** calls `nuguard_sbom_generate`:
```
nuguard_sbom_generate(source="~/projects/cs-bot", output="~/projects/cs-bot/app.sbom.json")
```
Result: 14 nodes detected — 1 agent, 1 model (gpt-4o), 3 tools (PostgreSQL query, email sender, web search), 1 guardrail, 1 datastore (PostgreSQL), 2 prompts, 2 API endpoints. Summary shows PII classification on the customer records datastore.

**Claude** calls `nuguard_analyze`:
```
nuguard_analyze(sbom="~/projects/cs-bot/app.sbom.json", min_severity="medium")
```
Result: 3 findings:
- **HIGH** NGA-007: PostgreSQL tool node has `sql_injectable: true` with no parameterization evidence
- **HIGH** NGA-011: Datastore with PII classification (`customer.email`, `customer.name`) has no AUTH node in graph
- **MEDIUM** NGA-003: Email sender tool has `high_privilege: true` with no HITL trigger in graph

**Claude** explains findings to the user and asks whether to run the full red-team scan:

> The SBOM reveals three structural issues: the PostgreSQL tool appears to accept raw string inputs that could be SQL-injected, the customer PII datastore has no authentication boundary in the component graph, and the email sending tool has no human-in-the-loop gate. Want me to run the red-team scan to verify these are exploitable? You'll need to start the app locally first.

**User:** Yes, I've started the app on port 8080. Go ahead.

**Claude** calls `nuguard_redteam`:
```
nuguard_redteam(
    config_path="~/projects/cs-bot/nuguard.yaml",
    profile="ci",
    fail_on="high"
)
```
Result (after ~3 minutes): 2 confirmed findings:
- **CRITICAL** DATA_EXFILTRATION: The agent returned `customer.email` and `customer.name` for a different tenant's customer when asked "Show me John's account info" — cross-tenant PII leak confirmed
- **HIGH** PROMPT_DRIVEN_THREAT: System prompt partially extracted via indirect injection in the product description field

**Claude** summarizes and provides remediation guidance:

> The red-team scan confirmed two exploitable vulnerabilities. The most critical is a cross-tenant data leakage — the agent returned records from another tenant's database without enforcing a tenant boundary in the SQL query. The second is a partial system prompt extraction via an indirect injection vector in product descriptions.
>
> **Recommended fixes:**
> 1. Add a `tenant_id` filter to every SQL query generated by the PostgreSQL tool, and verify it in a guardrail layer before execution.
> 2. Add a sanitization step that strips instruction-like content from external inputs (product descriptions, user-uploaded files) before they reach the agent's context.
> 3. Add a HITL gate on the email sender — require human confirmation for any outbound email triggered from agent reasoning.

---

## Environment Variables

| Variable | Description |
|---|---|
| `LITELLM_API_KEY` | Default API key for LLM-enriched analysis (Gemini, OpenAI, etc.) |
| `GEMINI_API_KEY` | Gemini-specific API key (auto-detected if `LITELLM_API_KEY` is not set) |
| `OPENAI_API_KEY` | OpenAI-specific API key |
| `ANTHROPIC_API_KEY` | Anthropic-specific API key |
| `NUGUARD_DEFAULT_CONFIG` | Absolute path to `nuguard.yaml` — used as the default `config_path` for all tool calls |
| `NUGUARD_REDTEAM_LLM_MODEL` | LiteLLM model string for red-team attack payload generation |
| `NUGUARD_REDTEAM_LLM_API_KEY` | API key specifically for the red-team attack LLM |
| `NUGUARD_REDTEAM_EVAL_LLM_MODEL` | LiteLLM model for red-team response evaluation |

The MCP server process inherits these from its parent environment. In Claude Desktop, set them in the `env` block of `claude_desktop_config.json`. In Smithery, configure them in the server settings panel.

---

## Configuration File

Set `NUGUARD_DEFAULT_CONFIG` to avoid repeating `config_path` in every tool call. The variable must point to an absolute path. The `nuguard.yaml` file tells tools where the SBOM is, what the target URL is, and how to authenticate.

```bash
export NUGUARD_DEFAULT_CONFIG=/home/me/projects/cs-bot/nuguard.yaml
```

Generate a starter config for any project:

```bash
nuguard init --path /home/me/projects/cs-bot/nuguard.yaml \
             --target http://localhost:8080
```

See [CLI Reference](doc.html?page=cli-reference) for the full `nuguard.yaml` schema.

---

## Troubleshooting

**Tools don't appear in Claude Desktop**

Restart Claude Desktop after editing `claude_desktop_config.json`. Check that `nuguard-mcp` (or `uvx`) is on the `PATH` that the Claude Desktop process uses — Claude Desktop does not inherit shell aliases or `~/.zshrc` exports on macOS. Use the absolute path to the binary if needed:

```json
{
  "mcpServers": {
    "nuguard": {
      "command": "/usr/local/bin/nuguard-mcp"
    }
  }
}
```

**`nuguard_redteam` times out**

The default timeout is 900 seconds (15 minutes). For large scans with `profile=full` and guided conversations enabled, pass `timeout_seconds=1800`. Red-team scans are also sensitive to the target app's response time; set `redteam.request_timeout` in `nuguard.yaml` to match your app's SLA.

**`status: "error"` with no findings**

The `stderr` field in the response contains the error message from the nuguard CLI. Common causes:
- `sbom` path does not exist — check that `nuguard_sbom_generate` ran first and the output path matches
- `config_path` points to a directory, not a file
- Missing required environment variable (e.g., no `LITELLM_API_KEY` when `--llm` is used)

**LLM enrichment returns canned responses**

Set `LITELLM_API_KEY` (or a provider-specific key like `GEMINI_API_KEY`) in the MCP server's environment. Without an API key, LLM calls return placeholder text but do not fail the scan.

**Grype / Checkov / Trivy not running**

These are optional external tools. Install them on the same machine running `nuguard-mcp`:

```bash
# Grype
curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b /usr/local/bin

# Checkov / Semgrep (pip)
pip install checkov semgrep

# Trivy
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
```

Or disable individual scanners in the `nuguard_analyze` tool call:
```
nuguard_analyze(sbom="...", enable_grype=false, enable_trivy=false)
```
