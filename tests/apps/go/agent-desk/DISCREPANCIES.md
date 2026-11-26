# First deterministic scan — 2026-11-26

NuGuard `develop` commit `361ed4bc`, LLM disabled:

```sh
nuguard sbom generate --source tests/apps/go/agent-desk --no-llm --output tmp/agent-desk-747/agent-desk.sbom.json
nuguard analyze --sbom tmp/agent-desk-747/agent-desk.sbom.json --nga --no-llm --format json --output tmp/agent-desk-747/analysis.json
```

The SBOM contains 164 nodes and 176 edges: 138 API endpoints, five datastores,
five privileges, four deployments, four models, three auth nodes, three frameworks,
one prompt and one agent. The agent and Eino/Gin frameworks are recognized.

**Tool coverage gap:** zero TOOL nodes and zero MCP_SERVER nodes are emitted. Source
contains `create_ticket_with_confirmation` and `handoff_to_human` in
`internal/pkg/toolx/builtin_tools.go`, with implementation in runtime/tools and
runtime/graphs. The graph/tool registry pattern needs adapter coverage before the
SBOM can support meaningful tool-path campaigns. MCP is a client integration;
absence of an MCP_SERVER node alone is not an error.

Names such as Supabase and the four model names are configuration/source detections,
not proof of a running deployment or model execution. Supabase appears in commented
configuration examples.

The NGA static pass produced 137 candidate findings. These are scanner hypotheses,
not 137 confirmed vulnerabilities. No live server or model was involved.
The committed SBOM is the first-run snapshot, not an expected-node-count test.
