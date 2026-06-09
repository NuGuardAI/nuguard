---
name: nuguard-sbom
description: Generate an AI Bill of Materials (AI-SBOM) from source code or a git repository
allowed-tools: ["Bash"]
---

Generate an AI-SBOM for the current project and summarise the detected components.

## Usage

```
/nuguard-sbom [--source PATH] [--repo URL] [--ref BRANCH] [--output FILE] [--llm]
```

## Steps

1. **Detect nuguard** — check `which uv 2>/dev/null`. If `uv` is present, use `uv run nuguard`. Otherwise check `which nuguard 2>/dev/null`; if on PATH use `nuguard`. If neither is available, run `pip install nuguard` first.

2. **Determine the source**:
   - `--source PATH` provided → pass `--source PATH`
   - `--repo URL` provided → pass `--from-repo URL` (plus `--ref`, default `main`)
   - Neither → default to `--source .`

3. **Run via Bash**:
   ```bash
   nuguard sbom generate --source . --output app.sbom.json
   # with repo:
   nuguard sbom generate --from-repo URL --ref BRANCH --output app.sbom.json
   # add --llm if user passed --llm
   ```

4. **Report the component inventory**:
   - Node count by type: agents, models, tools, datastores, guardrails, prompts, API endpoints, MCP servers
   - Edge count
   - Any nodes carrying risk metadata: `sql_injectable`, `ssrf_possible`, `high_privilege`, `pii_classification`, `no_auth`
   - Frameworks detected

5. **Flag zero-node results** — if no nodes found, explain why (wrong path, unsupported framework) and stop.

6. **Next step hint** — suggest `/nuguard-analyze` for static findings or `/nuguard-scan --full` for the unified policy/red-team validation path.

## Examples

```
/nuguard-sbom
/nuguard-sbom --source ./my-agent
/nuguard-sbom --repo https://github.com/org/repo --ref main
/nuguard-sbom --output sbom.json --llm
```
