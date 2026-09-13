

## How the graph is built without file content

The relationship graph is a **two-stage pipeline**, and only the *second* stage touches the LLM — and even then only with derived facts, never raw code:

**Stage 1 — deterministic node/edge construction** (happens earlier in extraction, well before `_llm_enrich`, in `AiSbomExtractor._resolve_edges`):
- Explicit edges come from `RelationshipHint`s produced by AST/regex adapters (e.g. an adapter that sees `agent.tools = [search_tool]` emits a `CALLS` hint) — these carry real evidence.
- Where hints are missing, **heuristic fallback edges** are synthesized purely from node type/metadata, e.g.:
  - `AGENT → TOOL` (CALLS) for the first 5 tools, alphabetically, if an agent has no tool edge
  - `AGENT → MODEL` (USES) for the top-3 highest-confidence models
  - `FRAMEWORK → AGENT/TOOL/MODEL` via matching `metadata.framework` string
  - `AGENT → DATASTORE` (ACCESSES) inferred transitively through `TOOL → DATASTORE` chains
  
  These fallbacks have **no code evidence** backing them — they're structural guesses to avoid a sparse/disconnected diagram.

**Stage 2 — LLM narrative** (`relationship_graph.py`):
- `build_mermaid_graph()` renders `doc.nodes`/`doc.edges` into a Mermaid diagram — no LLM, pure formatting.
- `_graph_context()` flattens the same edges into lines like `AgentX (AGENT) --[CALLS]--> ToolY (TOOL)` plus isolated nodes.
- One LLM call takes that text and writes a bullet-point narrative about architecture/risk.

So the LLM never sees which edges are evidence-backed vs. heuristic guesses, never sees `access_type` (read/write), confidence, or the underlying code — it only sees name/type/relationship-type triples.

## How it could be improved

1. **Surface edge provenance and confidence to the LLM.** `_graph_context()` currently discards `edge.access_type`, node `confidence`, and `evidence_kind`/`source_tier`. Including these (e.g. `ToolY --[ACCESSES:write, confidence=0.75, heuristic]--> DB`) would let the narrative hedge on guessed edges instead of stating them as fact.
2. **Mark heuristic fallback edges explicitly.** `_add_edge` in `_resolve_edges` doesn't tag *how* an edge was derived (hint vs. fallback heuristic). Adding a `source: "hint" | "fallback_heuristic"` attribute to `Edge` would let both the diagram (e.g. dashed line) and the LLM narrative distinguish confirmed data flow from inferred structure.
3. **Feed risk-relevant node metadata into the prompt.** Risk attribute tags (`SQL-injectable`, `no-auth-required`, etc.) and `access_type` already exist on nodes/edges elsewhere in the SBOM but aren't passed to `_graph_context()` — including them would let the narrative call out real risk patterns instead of generic architecture description.
4. **Ground low-confidence/fallback edges with actual code evidence.** For edges below a confidence threshold or tagged as heuristic, pull a short snippet from `Evidence.location` (already tracked per-node) and let the LLM verify/annotate rather than accepting the heuristic blindly — similar to the existing `verify_uncertain_nodes` pattern for nodes, but applied to edges.
5. **Chunk/cluster for large graphs.** The whole edge list is sent in a single call with no size cap — for large SBOMs this risks truncation or an unfocused narrative. Clustering by framework/subsystem (or capping to highest-risk/highest-centrality nodes) and summarizing hierarchically would scale better.
6. **A stronger model helps most at step 1's heuristics, not step 2's narrative.** Since the graph narrative already just describes derived structure, model capability mainly pays off if used to *replace* the naive fallback-edge heuristics (e.g. a targeted LLM call resolving ambiguous agent→tool/model bindings from actual code) rather than only prettifying the final description.