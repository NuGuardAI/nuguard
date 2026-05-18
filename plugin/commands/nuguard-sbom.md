---
name: nuguard-sbom
description: Generate an AI Bill of Materials (AI-SBOM) from source code or a git repository
---

Generate an AI-SBOM for the current project (or a specified source) and summarise the detected components.

## Usage

```
/nuguard-sbom [--source PATH] [--repo URL] [--ref BRANCH] [--output FILE] [--llm]
```

## Steps

1. **Determine the source** — use `--source PATH` if provided, otherwise default to `"."`.
   If `--repo URL` is provided, use `from_repo` instead of `source` (with `--ref` defaulting to `"main"`).

2. **Generate the SBOM** — call `nuguard_sbom_generate` with the resolved source/repo and
   `output` (default: `"app.sbom.json"`). Pass `llm=true` if `--llm` flag is set.

3. **Report the component inventory** — present a concise summary:
   - Total node count broken down by type: agents, models, tools, datastores, guardrails,
     prompts, API endpoints, MCP servers
   - Total edge count
   - Any nodes carrying risk metadata (`sql_injectable`, `ssrf_possible`, `high_privilege`,
     `pii_classification`, `no_auth`)
   - Framework(s) detected (LangChain, OpenAI Agents SDK, CrewAI, Google ADK, etc.)

4. **Flag zero-node results** — if `node_count == 0`, tell the user why the extractor found
   nothing (wrong path, unsupported framework, no Python/TypeScript source detected) and
   suggest fixes before stopping.

5. **Next step hint** — remind the user they can run `/nuguard-analyze` to scan the generated
   SBOM for security risks, or `/nuguard-scan` to run the full pipeline in one step.

## Examples

```
/nuguard-sbom
/nuguard-sbom --source ./my-agent
/nuguard-sbom --repo https://github.com/org/repo --ref main
/nuguard-sbom --output sbom.json --llm
```
