# Marketplace Module

This module owns marketplace and agent-host distribution rules. Runtime manifests stay at platform-required paths; do not move `.claude-plugin/plugin.json`, root `marketplace.json`, or extension manifests here unless upstream supports that path.

| Area | Rule |
|------|------|
| Install targets | Claude Code, ClawHub.ai/OpenClaw, Gemini CLI, and generic MCP-compatible agents; MCP server wired via `.mcp.json`. |
| Manifest files | `.claude-plugin/plugin.json`, `marketplace.json`, `.claude-plugin/marketplace.json`, `gemini-extension.json`, `openclaw.plugin.json`. |
| MCP server | Canonical entry point: `.mcp.json` → `uvx --from nuguard[mcp] nuguard-mcp`; Smithery listing: `https://smithery.ai/servers/NuGuardAI/nuguard`. |
| Sync | Canonical version: `.claude-plugin/plugin.json`; version field must match `pyproject.toml` and `smithery.yaml`. |

## Install

```bash
# Claude Code / Smithery
smithery mcp add NuGuardAI/nuguard

# PyPI CLI
pip install nuguard
```
