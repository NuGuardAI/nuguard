# NuGuard MCP Server

NuGuard's MCP server exposes AI security tools — SBOM generation, static analysis, behavioral testing, and adversarial red-teaming — directly to Claude. Ask Claude to audit your AI application and it will orchestrate the full pipeline, interpret findings, and recommend fixes without you leaving the chat.

---

## Installation

Choose the method that matches your environment. All methods expose the same seven tools.

---

### Claude Code plugin (recommended)

The plugin installs the MCP server and wires slash commands, agents, and skills in one step — no manual config editing required.

**From the Claude Code UI:** open the plugin marketplace, search for **NuGuard**, and click **Install**.

**From the terminal:**

```bash
claude plugin add NuGuardAI/nuguard
```

The bundled `.mcp.json` starts the server via `npx -y nuguard`, which auto-detects the best available Python runtime (see [How the launcher picks a runtime](#how-the-launcher-picks-a-runtime) below). No separate tool installation is required if you have Node.js or Python.

After installation, run the configuration wizard once per project:

```
/nuguard-config
```

The wizard collects your LLM API key, model, target URL, and auth details, then writes them to `.claude/nuguard.local.md` (project-local, gitignored). Every NuGuard command and the AI security-review skill read this file automatically — you never need to repeat credentials.

---

### pip (fastest if you already have Python)

Install the package once, then the MCP server is available everywhere with no extra tooling:

```bash
pip install "nuguard[mcp]"
```

For Claude Desktop, add to your config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "nuguard": {
      "command": "nuguard-mcp",
      "env": {
        "LITELLM_API_KEY": "your-api-key-here",
        "NUGUARD_DEFAULT_CONFIG": "/absolute/path/to/nuguard.yaml"
      }
    }
  }
}
```

For Claude Code (project-local), add a `.mcp.json` at your project root:

```json
{
  "mcpServers": {
    "nuguard": {
      "type": "stdio",
      "command": "nuguard-mcp"
    }
  }
}
```

Restart Claude Desktop or reload the Claude Code window after saving. The NuGuard tools appear in the tools panel (hammer icon).

> **Tip:** Using a virtual environment? Activate it first, or point directly to the venv's binary:
> ```json
> { "command": "/path/to/.venv/bin/nuguard-mcp" }
> ```

---

### npx (no Python required)

If you have Node.js (which includes `npx`) but not Python, `npx` downloads and runs the wrapper on first use with no interactive prompts:

For Claude Desktop:

```json
{
  "mcpServers": {
    "nuguard": {
      "command": "npx",
      "args": ["-y", "nuguard"],
      "env": {
        "LITELLM_API_KEY": "your-api-key-here",
        "NUGUARD_DEFAULT_CONFIG": "/absolute/path/to/nuguard.yaml"
      }
    }
  }
}
```

For Claude Code:

```json
{
  "mcpServers": {
    "nuguard": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "nuguard"]
    }
  }
}
```

The npm wrapper uses the same runtime detection waterfall as the plugin (see below), so it will use pip-installed nuguard if present, or install it automatically.

---

### Smithery

If you use Smithery to manage your MCP servers:

```bash
smithery mcp add NuGuardAI/nuguard
```

Or open [smithery.ai/servers/NuGuardAI/nuguard](https://smithery.ai/servers/NuGuardAI/nuguard) and click **Connect**.

During install, Smithery prompts for three optional settings:

| Setting | Description |
|---|---|
| `litellm_api_key` | API key for LLM-enriched analysis (Gemini, OpenAI, Anthropic, etc.) |
| `nuguard_config_path` | Absolute path to a `nuguard.yaml` config — sets the default for all tool calls |
| `redteam_llm_model` | LiteLLM model for red-team payload generation (e.g. `openai/gpt-4o`) |

Secrets are passed as environment variables to the `nuguard-mcp` process and never travel through Claude's context.

---

### uvx (uv users)

If you use [uv](https://docs.astral.sh/uv/), you can run the MCP server directly without a permanent install:

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

---

### How the launcher picks a runtime

When started via `npx -y nuguard` (the plugin default), the launcher checks in order:

1. `nuguard-mcp` already on `PATH` — uses it directly (zero setup, fastest)
2. `python3` / `python` with `nuguard` importable — runs `python -m nuguard.mcp`
3. `uvx` on `PATH` — runs `uvx --from nuguard[mcp] nuguard-mcp`
4. Any Python available — runs `pip install nuguard[mcp]` silently, then starts the server

Steps 1 and 2 require nothing extra if nuguard is already pip-installed. Step 3 and 4 handle fresh environments automatically.

---

## Configuration

### Quick setup with the Claude Code plugin

If you installed via the Claude Code Plugin, run the interactive wizard once per project:

```
/nuguard-config
```

It collects:

| Setting | Required | Description |
|---|---|---|
| LLM API key | Yes | Used for SBOM enrichment, analysis, and red-team payload generation |
| Model name | No | Default: `gemini/gemini-2.0-flash`. Any LiteLLM model string |
| API base URL | No | Required for Azure OpenAI or self-hosted models |
| API version | No | Required for Azure OpenAI (e.g. `2024-05-01-preview`) |
| Target URL | Yes | Base URL of the running AI application |
| Chat endpoint | No | Default: `/chat` |
| Auth type | No | `none` \| `bearer_token` \| `api_key` \| `basic` |
| Auth credentials | Conditional | Token, API key header/value, or username + password |

Settings are saved to `.claude/nuguard.local.md` — a project-local file that is never committed (already in `.gitignore`). Re-run `/nuguard-config` any time to update values.

### Using a nuguard.yaml (all install methods)

For Smithery, manual Claude Desktop, or manual Claude Code installs, create `nuguard.yaml` in your project directory. Run the wizard or use the template from `nuguard.yaml.example`. The NUGUARD_DEFAULT_CONFIG environment variable tells the MCP server where to find it:

```bash
export NUGUARD_DEFAULT_CONFIG=/absolute/path/to/nuguard.yaml
```

To generate `nuguard.yaml` from your Claude Code plugin config:

```
/nuguard-init
```

This reads `.claude/nuguard.local.md` and creates `nuguard.yaml`, `cognitive-policy.md`, and `canary.example.json` in the current directory.

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

## Example: Azure OpenAI-backed agent

This walkthrough uses NuGuard to audit an AI application that calls Azure OpenAI. LiteLLM is used for NuGuard's own analysis; the target app uses its own Azure credentials independently.

### Environment setup

```bash
# API key for NuGuard's LLM enrichment and red-team payload generation
# LiteLLM format for Azure: azure/<deployment-name>
export LITELLM_API_KEY="your-azure-openai-key"
export NUGUARD_REDTEAM_LLM_MODEL="azure/gpt-4o"

# Azure-specific vars consumed by LiteLLM
export AZURE_API_BASE="https://your-instance.openai.azure.com"
export AZURE_API_VERSION="2024-05-01-preview"
```

### nuguard.yaml

```yaml
llm:
  model: azure/gpt-4o
  api_base: "${AZURE_API_BASE}"
  api_version: "${AZURE_API_VERSION}"

redteam:
  target: http://localhost:8080
  endpoint: /chat
  llm_model: azure/gpt-4o

behavior:
  target: http://localhost:8080
  endpoint: /chat
```

### Conversation

**User:** My AI agent at `~/projects/cs-bot` uses Azure OpenAI. Audit it with NuGuard.

**Claude** calls `nuguard_init`:
```
nuguard_init(project_dir="~/projects/cs-bot", target_url="http://localhost:8080")
```
Creates `nuguard.yaml`, `cognitive-policy.md`, `canary.example.json`.

**Claude** calls `nuguard_sbom_generate`:
```
nuguard_sbom_generate(
  source="~/projects/cs-bot",
  output="~/projects/cs-bot/app.sbom.json",
  llm=true
)
```
Result: 11 nodes — 1 agent, `azure/gpt-4o-deployment` model, PostgreSQL tool, Azure Blob Storage datastore, 2 prompts, REST API endpoint. LLM enrichment annotates the Blob datastore as PII-adjacent.

**Claude** calls `nuguard_analyze`:
```
nuguard_analyze(
  sbom="~/projects/cs-bot/app.sbom.json",
  min_severity="medium",
  llm=true
)
```
Result:
- **HIGH** NGA-011: Azure Blob datastore with PII signals has no AUTH node in component graph
- **HIGH** NGA-007: SQL tool node lacks parameterization evidence — potential injection path
- **MEDIUM** NGA-003: No HITL gate before blob write operations

**Claude** asks whether to run red-team with the app running:

> Static analysis found three structural issues. The PII datastore has no authentication boundary in the component graph, the SQL tool may accept unparameterized input, and blob writes have no human-in-the-loop gate. Start the app on port 8080 and I can run red-team to verify exploitability.

**User:** App is running. Go ahead.

**Claude** calls `nuguard_redteam`:
```
nuguard_redteam(
  config_path="~/projects/cs-bot/nuguard.yaml",
  profile="ci",
  fail_on="high"
)
```
Result (~4 min):
- **CRITICAL** DATA_EXFILTRATION: Agent returned blob file listing for a different tenant when asked "what files do you have access to?"
- **HIGH** PROMPT_DRIVEN_THREAT: System prompt partially extracted via indirect injection in uploaded filename field

**Claude** responds:

> The red-team scan confirmed two exploitable vulnerabilities. The most critical is a cross-tenant storage leak — the agent returns Azure Blob container contents without enforcing a tenant boundary. The second is partial system prompt extraction via a filename injection vector.
>
> **Recommended fixes:**
> 1. Scope all Azure Blob queries to the authenticated tenant's container prefix and enforce this in the tool layer, not just the prompt.
> 2. Sanitize uploaded filenames and other external string inputs before they reach the agent's context window.
> 3. Add a HITL confirmation step before any blob write or delete operation.

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
Restart Claude Desktop after editing `claude_desktop_config.json`. On macOS and Windows, Claude Desktop does not inherit your shell PATH, so relative command names may not resolve. Use the absolute path to whichever binary you chose:

```json
{ "command": "/usr/local/bin/nuguard-mcp" }
```
```json
{ "command": "/usr/local/bin/npx", "args": ["-y", "nuguard"] }
```

Find the absolute path with `which nuguard-mcp` (macOS/Linux) or `where nuguard-mcp` (Windows).

**`nuguard-mcp: no suitable Python runtime found`**  
The `npx` launcher could not find Python or uvx. Install one of:
```bash
pip install "nuguard[mcp]"      # recommended — also installs the nuguard-mcp binary
```
or install [uv](https://docs.astral.sh/uv/) and the launcher will use `uvx` automatically.

**`nuguard-mcp` not found after `pip install nuguard[mcp]`**  
pip installed into a virtual environment whose `bin/` directory is not on your PATH. Either activate the venv before starting Claude Desktop, or use the absolute path:
```json
{ "command": "/path/to/.venv/bin/nuguard-mcp" }
```

**`nuguard_redteam` times out**  
The default timeout is 900 s. For large `profile=full` scans pass `timeout_seconds=1800`. Also check `redteam.request_timeout` in `nuguard.yaml` — it should match your app's response SLA.

**`status: "error"` with no findings**  
Check the `stderr` field in the response. Common causes: `sbom` path doesn't exist, `config_path` points to a directory, or `LITELLM_API_KEY` is not set when `llm=true`.

**Grype / Checkov / Trivy not running**  
These are optional external tools. Install them or disable them per-call:
```
nuguard_analyze(sbom="...", enable_grype=false, enable_trivy=false)
```
