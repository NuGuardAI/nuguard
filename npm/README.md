# @nuguardai/nuguard

[![npm version](https://img.shields.io/npm/v/@nuguardai/nuguard)](https://www.npmjs.com/package/@nuguardai/nuguard)
[![PyPI version](https://img.shields.io/pypi/v/nuguard)](https://pypi.org/project/nuguard/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/NuGuardAI/nuguard/blob/main/LICENSE)

NuGuard is an open-source AI application security CLI. This npm package provides the `nuguard-mcp` launcher, which wires NuGuard as a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server so AI-native tools (Claude Desktop, VS Code, Cursor, etc.) can invoke NuGuard capabilities directly.

The underlying CLI is a Python package. This launcher automatically locates or installs it so you don't have to manage the Python environment manually.

## Installation

```bash
npx @nuguardai/nuguard
```

Or install globally:

```bash
npm install -g @nuguardai/nuguard
nuguard-mcp
```

## What the launcher does

`nuguard-mcp` tries the following in order, using whichever succeeds first:

1. `nuguard-mcp` already on `PATH` (pip-installed globally or in an active venv)
2. `python3 -m nuguard.mcp` (nuguard installed but scripts not on `PATH`)
3. `uvx --from nuguard[mcp] nuguard-mcp` (uv available)
4. `pip install nuguard[mcp]` then `python -m nuguard.mcp` (one-time bootstrap)

## MCP configuration

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nuguard": {
      "command": "npx",
      "args": ["-y", "@nuguardai/nuguard"]
    }
  }
}
```

### VS Code (`.vscode/mcp.json`)

```json
{
  "servers": {
    "nuguard": {
      "command": "npx",
      "args": ["-y", "@nuguardai/nuguard"]
    }
  }
}
```

## Requirements

- Node.js 18+
- Python 3.12+ (or `uv` for zero-setup bootstrap)

## CLI capabilities

Once connected, the MCP server exposes NuGuard's full pipeline:

| Command | Description |
|---|---|
| `nuguard init` | Scaffold a `nuguard.yaml` config file |
| `nuguard sbom` | Generate an AI Bill of Materials from source code |
| `nuguard analyze` | Static analysis of an AI-SBOM for security risks |
| `nuguard policy` | Validate a cognitive policy document against a scan |
| `nuguard behavior` | Behavioral testing against a live AI application |
| `nuguard redteam` | Adversarial red-teaming with scenario-driven attacks |
| `nuguard scan` | Unified pipeline: SBOM → analyze, with optional policy/redteam steps |
| `nuguard target verify` | Check target connectivity and auth before a run |
| `nuguard seed` | Seed canary data into the target before red-teaming |
| `nuguard validate` | Validate AI application behavior |
| `nuguard report` | Generate reports for a completed red-team run |
| `nuguard findings` | List findings from a completed red-team run |
| `nuguard replay` | Deterministically replay a completed red-team run |

## Links

- [GitHub](https://github.com/NuGuardAI/nuguard)
- [Documentation](https://nuguardai.github.io/nuguard/)
- [PyPI](https://pypi.org/project/nuguard/)
- [Plugin guide](https://nuguardai.github.io/nuguard/plugin-guide.html)

## License

Apache-2.0
