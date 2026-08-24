# NuGuard SBOM Extractor — Remediation Plan (Phlox ground-truth benchmark)

Findings are from comparing `phlox.sbom.enriched.json` (nuguard-generated) against
`phlox.ground-truth.sbom.json` (hand-curated) for https://github.com/bloodworks-io/phlox.
Each issue below is traced to its root cause in the actual nuguard extractor code.

## 1. FastAPI router-prefix composition (bug — highest priority, cheapest fix)

**Root cause:** [nuguard/sbom/adapters/python/fastapi_adapter.py](../../../nuguard/sbom/adapters/python/fastapi_adapter.py)
(and the duplicate legacy copy at
[nuguard/sbom/extractor/framework_adapters/fastapi.py](../../../nuguard/sbom/extractor/framework_adapters/fastapi.py))
extracts each `@router.get/post(...)` path in isolation, per-file. It never tracks
`app.include_router(x_router, prefix="/api/x")` calls, so nested routers
(`config/__init__.py` → `server.py`) lose their prefix (`/mcp` instead of
`/api/config/mcp`).

**Plan:**
- Add a cross-file pass (similar to the existing `set_global_model_schemas`
  cross-file wiring) that scans all files for
  `app.include_router(router_var, prefix="...")` /
  `router.include_router(sub_router, prefix="...")` calls, builds a
  `router_var → prefix` map per file, and resolves it transitively when a
  router imported from another module is itself included with a prefix.
- Apply the composed prefix to `metadata["endpoint"]` before emitting the node.
- Add a regression test mirroring Phlox's `config/__init__.py` → `server.py`
  two-level nesting.

## 2. Empty-path route dedup collision (bug — data-corruption risk, high priority)

**Root cause:** `_parse_route_decorator` sets `path_str = ""` for
`@router.post("", ...)` (a very common FastAPI idiom for a router's "root"
endpoint — used by `/api/chat`, `/api/audit`, `/api/templates`,
`/api/letter/save`, etc.). Because `if path_str:` is falsy for `""`,
`metadata["endpoint"]` is never set. Worse, the node dedup key
`canon = f"endpoint:{method.upper()}:{path_str}"` collapses **every unrelated
empty-path route across the whole codebase** into one node — confirmed:
the "Chat" node merged `server/api/chat.py:147` with
`server/api/templates.py:192`, and the "List Audit" node merged
`server/api/audit.py:19` with `server/api/templates.py:180`.

**Plan:**
- Change the falsy check to `if path_str is not None:` so `""` is preserved as
  a real (empty) endpoint path.
- Include the file path (or the composed router prefix from #1) in the dedup
  canonical key — never dedup purely on `method:path` across files, since two
  different files can legitimately both define `POST ""`.
- Add a regression test with two files each declaring `@router.post("")`
  under different prefixes, asserting two distinct nodes.

## 3. No generic OpenAI-function-schema TOOL adapter (biggest ground-truth gap)

**Root cause:** every TOOL adapter is framework-specific (LangChain `@tool`,
CrewAI `Tool(...)`, MCP `@mcp.tool()`, etc. — see
[nuguard/sbom/adapters/registry.py](../../../nuguard/sbom/adapters/registry.py)).
Phlox instead builds raw OpenAI
`{"type": "function", "function": {"name": ..., "parameters": {...}}}` dicts by
hand and dispatches them via a custom executor — a common pattern for apps
that call `chat.completions.create(tools=[...])` directly without an agent
framework. Result: 9 of Phlox's 11 real tools were completely missed.

**Plan:**
- Add a new generic adapter, e.g.
  `nuguard/sbom/adapters/python/openai_function_schema.py`, that:
  - AST-matches dict/list literals shaped like OpenAI tool-call schemas: a
    dict with `"type": "function"` and a nested `"function"` dict containing
    `"name"` + `"parameters"`.
  - Emits one TOOL node per `"name"` found, using the `"description"` field as
    `metadata.description`.
  - Looks for a corresponding dispatcher (`if tool_name == "x": ...` /
    match/dict-based dispatch keyed by the same names) to add file/line
    evidence pointing at the actual implementation, not just the schema
    declaration.
- Add a `CALLS` edge from the nearest enclosing AGENT/chat-loop node (see #5)
  to each detected TOOL.
- This is the single highest-value fix — it directly recovers 9 of the 11
  missing tools.

## 4. No generic external-sanitization → GUARDRAIL heuristic

**Root cause:** GUARDRAIL only fires for `guardrails-ai` or OpenAI Agents SDK
guardrail decorators (see
[nuguard/sbom/adapters/python/guardrails_ai.py](../../../nuguard/sbom/adapters/python/guardrails_ai.py),
[openai_agents.py](../../../nuguard/sbom/adapters/python/openai_agents.py)).

**Plan:**
- Add a lightweight heuristic (regex/AST name-matching, similar to the
  existing `auth_generic` regex tier used for AUTH) that flags functions
  named like `sanitize_*`, `redact_*`, `scrub_*`, `filter_*_for_external`,
  etc. when they are called immediately before an outbound HTTP/tool call, as
  a low-confidence GUARDRAIL candidate.
- Add a `PROTECTS` edge from the guardrail function to whichever TOOL node
  (from #3) calls it, matched by same-file call-graph adjacency.
- Keep confidence lower (e.g. 0.5–0.6) than framework-native guardrail
  detections, and mark it in `metadata.extras.detected_by_tiers` as a
  heuristic rather than a framework-native detection, so it's clearly
  distinguishable in output.

## 5. No MCP-*client* detection (only MCP-*server* frameworks are covered)

**Root cause:**
[nuguard/sbom/adapters/python/mcp_server.py](../../../nuguard/sbom/adapters/python/mcp_server.py)
and
[claude_agent_sdk.py](../../../nuguard/sbom/adapters/python/claude_agent_sdk.py)
only detect apps that *host* an MCP server or wire `mcp_servers=` config into
Claude's SDK. Phlox's pattern — a config store of user-added, arbitrary MCP
server URLs (`server/database/config/mcp_manager.py`) plus a runtime tool
(`server/chat/tools/mcp_tool.py`) that connects to them — is a distinct and
arguably higher-risk shape (untrusted, user-supplied MCP servers vs. a fixed
hosted server list).

**Plan:**
- Add a new adapter (or extend `mcp_server.py` with a second detection mode)
  that recognizes: a data model / config table storing `{name, url, ...}` for
  MCP servers (fields like `url`+`enabled`/`allow_sensitive_data`), paired
  with an `mcp.ClientSession` / `stdio_client` / `sse_client` import from the
  `mcp` SDK.
- Emit an `MCP_SERVER` node representing "external, user-configured MCP
  servers" (not a DATASTORE for the config table itself) with
  `metadata.extras.trust_boundary = "user-configured/untrusted"`.
- Add a `CALLS` edge from the dispatching TOOL (e.g. `mcp_tool`) to this
  MCP_SERVER node, and flag it for the `MCP_TOXIC_FLOW` goal type used
  downstream in `nuguard/redteam`.
- Reclassify: currently `mcp_manager.py`'s sqlite3 usage is mislabeled as a
  generic `DATASTORE` — after this adapter exists, that file's config table
  should be treated as MCP_SERVER *configuration evidence*, not conflated
  with the two legitimate SQLite datastores.

## 6. Two competing FastAPI adapters (maintenance risk, do alongside #1/#2)

`nuguard/sbom/adapters/python/fastapi_adapter.py` and
`nuguard/sbom/extractor/framework_adapters/fastapi.py` appear to be
duplicate/legacy implementations of largely the same logic. Before fixing
#1/#2, confirm which one is actually wired into the active extractor pipeline
([nuguard/sbom/extractor/core.py](../../../nuguard/sbom/extractor/core.py)
references the `adapters/registry.py` path) and delete or deprecate the
unused copy so the fix isn't applied to dead code.

## Suggested order of work

1. **#6** (5 min sanity check — confirm live adapter path)
2. **#2** (empty-path dedup bug — small, isolated, prevents data corruption)
3. **#1** (router-prefix composition — moderate, needs cross-file pass)
4. **#3** (generic OpenAI-tool-schema adapter — highest value, moderate effort)
5. **#5** (MCP-client adapter — security-relevant, moderate effort)
6. **#4** (generic guardrail heuristic — lowest confidence/value, do last)

Each item should ship with its own unit test under `tests/sbom/` mirroring the
existing adapter test style (e.g. `test_fastapi_adapter.py`), plus one
end-to-end check that re-runs extraction against the Phlox fixture and
asserts the previously-missing ground-truth nodes are now found.
