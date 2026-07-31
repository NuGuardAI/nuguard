# NuGuard Plugin Guide

**Talk to your coding agent, get an AI security audit.** NuGuard exposes SBOM generation, static analysis, behavioral testing, and adversarial red-teaming as tools your agent can call directly — no separate terminal, no context-switching. Ask it to audit your AI application and it orchestrates the full pipeline, interprets the findings, and recommends fixes right there in the chat.

**How it works, in short:**

1. **Install** — pick your coding agent below and connect NuGuard to it.
2. **Configure** — point it at your app (one wizard command, or a `nuguard.yaml` file).
3. **Ask** — "audit this app with NuGuard" and it takes it from there.

---

## 📦 Installation

Each panel below is self-contained — expand the one that matches your agent and follow it top to bottom.

<details open>
<summary id="claude-code"><strong>💻 Claude Code</strong> — recommended, one-command install</summary>

<br>

The plugin wires up slash commands, agents, and skills in one step — no manual config editing required.

**From the Claude Code UI:** open the plugin marketplace, search for **NuGuard**, and click **Install**.

**From the terminal:**

```bash
claude plugin marketplace add NuGuardAI/nuguard
claude plugin install nuguard
```

**From inside a Claude Code chat:**

```
/plugin marketplace add NuGuardAI/nuguard
/plugin install nuguard
```

After installation, run the configuration wizard once per project:

```
/nuguard-config
```

The wizard collects your LLM API key, model, target URL, and auth details, then writes them to `.claude/nuguard.local.md` (project-local, gitignored). Every NuGuard command and the AI security-review skill read this file automatically — you never need to repeat credentials.

**What's included:**
- `/nuguard-scan`, `/nuguard-redteam`, `/nuguard-config`, `/nuguard-init` slash commands
- `ai-security-review` and `sbom-analysis` skills that Claude invokes automatically
- `security-auditor` agent for autonomous end-to-end audits
- Credentials stored per-project in `.claude/nuguard.local.md`, never globally

**Tip:** If you have multiple projects, run `/nuguard-config` in each one to set project-specific targets and credentials. The slash commands will always use the config from the current project context.

**Try it:**
```
use nuguard to generate an SBOM for this project and analyze it for security risks
```

[⬆ Back to agent picker](#choose-your-coding-agent)

</details>

<details>
<summary id="claude-desktop"><strong>🖥️ Claude Desktop</strong> — connects as an MCP server</summary>

<br>

Claude Desktop doesn't support the plugin marketplace, so it connects to NuGuard as an MCP server instead. Pick whichever option matches what's already on your machine — you only need one.

**Option A — you have Python installed (recommended):**

Install the package once with [`uv`](https://docs.astral.sh/uv/) (fast, recommended) or plain `pip`:

```bash
uv pip install "nuguard[mcp]"
```

```bash
pip install "nuguard[mcp]"
```

**Option B — you don't have Python, but you do have Node.js:**

`npx` (bundled with Node.js) downloads and runs NuGuard automatically the first time it's needed — nothing to install ahead of time.

Either way, add this to your Claude Desktop config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

If you installed via Option A (`uv`/`pip`):

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

If you're using Option B (`npx`):

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

Restart Claude Desktop after saving. The NuGuard tools appear in the tools panel (hammer icon).

> **Using a virtual environment?** Activate it first, or point directly to the venv's binary:
> ```json
> { "command": "/path/to/.venv/bin/nuguard-mcp" }
> ```

> **Have `uv` and want to skip a permanent install entirely?** Use `uvx` instead, which runs NuGuard on demand:
> ```json
> {
>   "mcpServers": {
>     "nuguard": {
>       "command": "uvx",
>       "args": ["--from", "nuguard[mcp]", "nuguard-mcp"],
>       "env": { "LITELLM_API_KEY": "your-api-key-here" }
>     }
>   }
> }
> ```

[⬆ Back to agent picker](#choose-your-coding-agent)

</details>

<details>
<summary id="cursor-windsurf-cline-and-other-editors"><strong>🧩 Cursor, Windsurf, Cline, and Other Editors</strong> — any MCP-compatible editor</summary>

<br>

NuGuard runs as a standard [MCP](https://modelcontextprotocol.io) server, so any editor or agent that supports MCP — Cursor, Windsurf, Cline, and others — can connect to it using the same server definition, even though each editor stores that config in a different place. Check your editor's MCP / tool settings for the exact file location, then add:

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

`npx` (bundled with Node.js) is the lowest-friction option since it needs nothing pre-installed. If you have Python and `uv` installed instead, swap the `command` and `args` for:

```json
"command": "nuguard-mcp"
```

(after running `uv pip install "nuguard[mcp]"` — see [Claude Desktop](#claude-desktop) above for the full install step) or:

```json
"command": "uvx",
"args": ["--from", "nuguard[mcp]", "nuguard-mcp"]
```

Reload your editor's window after saving the config. The NuGuard tools should appear wherever your editor lists connected MCP tools.

[⬆ Back to agent picker](#choose-your-coding-agent)

</details>

<details>
<summary id="smithery"><strong>🛒 Smithery</strong> — one-click install via the MCP registry</summary>

<br>

[Smithery](https://smithery.ai) is a registry that many coding agents (Cursor, Windsurf, Claude Desktop, Claude Code, and others) can install MCP servers from directly, without you hand-writing any JSON config.

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

Secrets are passed as environment variables to the `nuguard-mcp` process and never travel through the agent's context.

> **Note:** Smithery provisions only the seven NuGuard tools. Slash commands, skills, and the security-auditor agent require the [Claude Code plugin](#claude-code).

[⬆ Back to agent picker](#choose-your-coding-agent)

</details>

<details>
<summary id="ci--scripting-no-chat-ui"><strong>⚙️ CI / Scripting</strong> — no chat UI, just the CLI</summary>

<br>

No chat interface involved — you're calling NuGuard from a pipeline or script. Install the package directly and use the `nuguard` CLI (see the [Quick Start guide](quick-start.md)) instead of the MCP server:

```bash
uv pip install nuguard   # recommended if you have uv
pip install nuguard      # otherwise
```

[⬆ Back to agent picker](#choose-your-coding-agent)

</details>

---

## ⚙️ Configuration

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

### Using a nuguard.yaml (all other install methods)

For Smithery, Claude Desktop, or any other MCP-based install, create `nuguard.yaml` in your project directory. Use the template from `nuguard.yaml.example`. The `NUGUARD_DEFAULT_CONFIG` environment variable tells NuGuard where to find it:

```bash
export NUGUARD_DEFAULT_CONFIG=/absolute/path/to/nuguard.yaml
```

To generate `nuguard.yaml` from your Claude Code plugin config:

```
/nuguard-init
```

This reads `.claude/nuguard.local.md` and creates `nuguard.yaml`, `cognitive-policy.md`, and `canary.example.json` in the current directory.

---

## 🧰 Available Tools

Seven tools cover the full pipeline. Expand any one for its parameters.

<details>
<summary><code>nuguard_init</code> — scaffold a <code>nuguard.yaml</code> for a project</summary>

<br>

Initialize a `nuguard.yaml` config file in a project directory. Also creates `canary.example.json` and `cognitive-policy.md`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_dir` | string | required | Directory to initialize |
| `target_url` | string | — | URL of the running AI application |
| `source_dir` | string | — | Source code directory for SBOM generation |
| `force` | bool | `false` | Overwrite existing files |

</details>

<details>
<summary><code>nuguard_sbom_generate</code> — build an AI Bill of Materials</summary>

<br>

Generate an AI Bill of Materials from source code or a git repository. Either `source` or `from_repo` must be provided.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `source` | string | — | Local source directory |
| `from_repo` | string | — | Git repository URL |
| `ref` | string | `"main"` | Branch / tag / commit (with `from_repo`) |
| `output` | string | `"app.sbom.json"` | Output file path |
| `llm` | bool | `false` | Enable LLM enrichment |
| `config_path` | string | — | Path to `nuguard.yaml` |

</details>

<details>
<summary><code>nuguard_analyze</code> — static risk analysis, no live app needed</summary>

<br>

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

</details>

<details>
<summary><code>nuguard_scan</code> — the unified pipeline, everything in one call</summary>

<br>

Runs the unified security scan pipeline. Defaults to SBOM generation plus static analysis; include `policy,redteam` in `steps` for policy and red-team validation. Returns artifact paths (`sbom.json`, `findings.json`, `findings.sarif`, `report.md`) and a severity summary.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `source` | string | `"."` | AI application source directory |
| `output_dir` | string | `"nuguard-reports"` | Directory for output files |
| `fail_on` | string | `"high"` | Severity threshold for non-zero exit |
| `steps` | string | `"sbom,analyze"` | Comma-separated steps: `sbom,analyze,policy,redteam` |
| `policy` | string | — | Path to Cognitive Policy Markdown; required for the `policy` step and used by `redteam` when supplied |
| `target` | string | — | Live app URL for the `redteam` step; the SBOM deployment URL is used as a fallback when available |
| `llm` | bool | `false` | LLM enrichment |
| `config_path` | string | — | Path to `nuguard.yaml` |

</details>

<details>
<summary><code>nuguard_behavior</code> — verify the app behaves as intended</summary>

<br>

Run intent-aware behavioral testing against a live AI application. Static mode checks SBOM–policy alignment without hitting the live app. Dynamic mode sends probe conversations and judges each turn for intent drift, policy violations, and data leakage.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `config_path` | string | required | Path to `nuguard.yaml` |
| `mode` | string | `"static+dynamic"` | `static` \| `dynamic` \| `static+dynamic` |
| `target` | string | — | Override `behavior.target` URL |
| `policy` | string | — | Path to Cognitive Policy Markdown |
| `output` | string | — | Write report to this file |
| `fail_on` | string | `"high"` | Severity threshold |

</details>

<details>
<summary><code>nuguard_redteam</code> — adversarial attacks against a live app</summary>

<br>

Run adversarial red-team testing against a live AI application. Requires a running target app and an LLM configured for attack payload generation (`NUGUARD_REDTEAM_LLM_MODEL`).

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

</details>

<details>
<summary><code>nuguard_policy_check</code> — compliance mapping against your Cognitive Policy</summary>

<br>

Cross-check a Cognitive Policy against an AI-SBOM and run compliance assessments (OWASP LLM Top 10, NIST AI RMF, EU AI Act).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `policy` | string | — | Path to Cognitive Policy Markdown |
| `sbom` | string | — | Path to AI-SBOM JSON |
| `config_path` | string | — | Path to `nuguard.yaml` |
| `framework` | string | — | `owasp-llm-top10` \| `nist-ai-rmf` \| `eu-ai-act` |
| `output` | string | — | Write compliance report to this file |
| `verbose` | bool | `false` | Show all controls with evidence |

</details>

---

## 🧪 See It In Action

<details>
<summary>Full walkthrough — auditing an Azure OpenAI-backed agent, from first prompt to fix</summary>

<br>

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

</details>

---

## 🔑 Environment Variables

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

## 🛠️ Troubleshooting

<details>
<summary>Tools don't appear in Claude Desktop</summary>

<br>

Restart Claude Desktop after editing `claude_desktop_config.json`. On macOS and Windows, Claude Desktop does not inherit your shell PATH, so relative command names may not resolve. Use the absolute path to whichever binary you chose:

```json
{ "command": "/usr/local/bin/nuguard-mcp" }
```
```json
{ "command": "/usr/local/bin/npx", "args": ["-y", "nuguard"] }
```

Find the absolute path with `which nuguard-mcp` (macOS/Linux) or `where nuguard-mcp` (Windows).

</details>

<details>
<summary><code>nuguard-mcp: no suitable Python runtime found</code></summary>

<br>

The `npx` launcher could not find Python or uvx. Install one of:
```bash
uv pip install "nuguard[mcp]"   # recommended if you have uv
pip install "nuguard[mcp]"      # alternative — also installs the nuguard-mcp binary
```
or install [uv](https://docs.astral.sh/uv/) and the launcher will use `uvx` automatically.

</details>

<details>
<summary><code>nuguard-mcp</code> not found after <code>pip install nuguard[mcp]</code></summary>

<br>

pip installed into a virtual environment whose `bin/` directory is not on your PATH. Either activate the venv before starting Claude Desktop, or use the absolute path:
```json
{ "command": "/path/to/.venv/bin/nuguard-mcp" }
```

</details>

<details>
<summary><code>nuguard_redteam</code> times out</summary>

<br>

The default timeout is 900 s. For large `profile=full` scans pass `timeout_seconds=1800`. Also check `redteam.request_timeout` in `nuguard.yaml` — it should match your app's response SLA.

</details>

<details>
<summary><code>status: "error"</code> with no findings</summary>

<br>

Check the `stderr` field in the response. Common causes: `sbom` path doesn't exist, `config_path` points to a directory, or `LITELLM_API_KEY` is not set when `llm=true`.

</details>

<details>
<summary>Grype / Checkov / Trivy not running</summary>

<br>

These are optional external tools. Install them or disable them per-call:
```
nuguard_analyze(sbom="...", enable_grype=false, enable_trivy=false)
```

</details>
