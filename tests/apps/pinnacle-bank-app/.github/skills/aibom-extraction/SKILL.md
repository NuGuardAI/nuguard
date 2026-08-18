---
name: aibom-extraction
description: "Domain knowledge for NuGuard's AI Bill of Materials (AIBOM) extraction system. Covers the NuGuard SBOM primary schema, NuGuardEnricher pipeline, canonical_id derivation rules, adapter architecture (Python/TypeScript framework adapters), JSONB storage layout, and assessment_service integration. USE FOR: writing or modifying adapters, enricher logic, AIBOM API routes, migration scripts, or assessment integration. DO NOT USE FOR: general FastAPI questions or non-AIBOM backend work."
---

# NuGuard AIBOM Extraction — Domain Knowledge

## Two Extraction Paths

```
GitHub URL
   │
   └─► ai_asset_service/core/extraction_service.py
           │  
           ├─── PRIMARY: NuGuard SBOM path (default)
           │      nuguard.sbom.extract(repo) → AiSbomDocument
           │             │
           │             └─► NuGuardEnricher.process_document()
           │                      │
           │                      └─► DB: AIBOMNode / AIBOMEdge (nuguard_node JSONB)
           │
           └─── FALLBACK: Legacy adapter path
                  AdapterRegistry → per-framework BaseAdapter.extract()
                  → AIBOMNode / AIBOMEdge dataclasses (pipeline_types.py)
                  → DB write (no nuguard_node JSONB)
```

**Never mix the two paths.** The NuGuard SBOM path stores `nuguard_node` / `nuguard_edge` JSONB; the legacy path leaves those columns NULL.

---

## NuGuard SBOM Primary Schema

`nuguard` (version 0.4.0) is NuGuard's AI SBOM library, installed as `nuguard==0.4.0` and imported via `nuguard.sbom`. Its core type is `AiSbomDocument`, which contains a list of `Node` objects and `Edge` objects.

### Key NuGuard SBOM Types

```python
AiSbomDocument:
  nodes: List[Node]
  edges: List[Edge]
  metadata: DocumentMetadata

Node:
  id: UUID                  # nuguard-internal UUID — NOT the NuGuard DB primary key
  component_type: ComponentType  # e.g. "AGENT", "MODEL", "IAM"
  name: str
  metadata: NodeMetadata
    framework: Optional[str]     # e.g. "langchain", "openai-agents"
    extras: Dict[str, Any]       # provider lives HERE, not top-level
      provider: Optional[str]    # e.g. "openai", "google"
    locations: List[Location]
      file_path: str
      line_start: int

Edge:
  source_id: UUID  # refers to Node.id
  target_id: UUID  # refers to Node.id
  relationship_type: str  # e.g. "agent_uses_model"
```

### CRITICAL: Where `provider` Lives

```python
# WRONG — metadata.provider does NOT exist in nuguard 0.4.0
node.metadata.provider

# CORRECT — provider is in extras
node.metadata.extras.get("provider")
```

Failing to use `extras.get("provider")` silently breaks `canonical_id` derivation for MODEL nodes, causing duplicate entries across scans.

---

## canonical_id Format

```
{node_type}:{namespace}:{name}
```

Examples:
- `agent:langchain:CustomerSupportAgent`
- `model:openai:gpt-4-turbo`
- `tool:local:search_database`
- `prompt:openai-agents:triage_instructions`
- `iam:aws:LambdaExecutionRole`

### Namespace Derivation (`_derive_namespace` in `enricher.py`)

Precedence order:
1. `node.metadata.framework` (if set and not a known-bad value)
2. `node.metadata.extras.get("provider")` — **this is where provider lives**
3. `"nuguard"` fallback

### NodeType → canonical_id prefix

```python
# From ids.py NodeType enum
AGENT       → "agent"
MODEL       → "model"
TOOL        → "tool"
PROMPT      → "prompt"
DATASTORE   → "datastore"
GUARDRAIL   → "guardrail"
AUTH        → "auth"
PRIVILEGE   → "privilege"
FRAMEWORK   → "framework"
IAM         → "iam"          # Added nuguard 0.4.0
CONFIG      → "config"
ENDPOINT    → "endpoint"
MCP_SERVER  → "mcp_server"
```

### ComponentType → NodeType Mapping

```python
# From NuGuardEnricher._COMPONENT_TO_NODE_TYPE
"AGENT"           → NodeType.AGENT
"MODEL"           → NodeType.MODEL
"TOOL"            → NodeType.TOOL
"PROMPT"          → NodeType.PROMPT
"DATASTORE"       → NodeType.DATASTORE
"GUARDRAIL"       → NodeType.GUARDRAIL
"AUTH"            → NodeType.AUTH
"PRIVILEGE"       → NodeType.PRIVILEGE
"FRAMEWORK"       → NodeType.FRAMEWORK   # but see LLM provider reclassification below
"API_ENDPOINT"    → NodeType.ENDPOINT
"DEPLOYMENT"      → NodeType.CONFIG
"CONTAINER_IMAGE" → NodeType.CONFIG
"IAM"             → NodeType.IAM
```

### Special Reclassification Rules

1. **FRAMEWORK → MODEL**: If `name.lower()` matches `_KNOWN_LLM_PROVIDER_NAMES` (openai, google, anthropic, mistral, etc.), the node is reclassified to `NodeType.MODEL`. This prevents bare LLM client imports from appearing as FRAMEWORK nodes.

2. **CONTAINER_IMAGE / DEPLOYMENT skipped if infra**: Node names matching `_CONTAINER_INFRA_SKIP_NAMES` (docker, docker-compose, containerd, podman) are dropped entirely.

3. **Datastore name normalization**: `sqlite3` → `sqlite` to prevent duplicates.

---

## DB Storage Layout

### `aibom_nodes` table (SQLAlchemy: `AIBOMNode`)

```sql
id UUID PRIMARY KEY
canonical_id TEXT UNIQUE        -- "type:namespace:name"
scan_id UUID → aibom_scans.id
type TEXT                       -- NodeType value
name TEXT
namespace TEXT
file_path TEXT
line_start INT
properties JSONB                -- flattened fields for assessment_service
nuguard_node JSONB              -- full AiSbomDocument Node (NuGuard SBOM path only)
```

**`properties` JSONB** — flattened for backward compatibility, assessment_service reads from here in Phases 1–2. Key fields:
- `file_path`, `line_start`, `namespace`
- `model_name`, `model_family`, `provider`
- `framework`, `agent_type`, `role`
- `nuguard_node_id` — the original NuGuard SBOM UUID (for cross-reference)

**assessment_service Phase 3+** can read directly from `nuguard_node`.

### `aibom_edges` table

```sql
id UUID PRIMARY KEY
scan_id UUID
source_node_id UUID → aibom_nodes.id
target_node_id UUID → aibom_nodes.id
edge_type TEXT             -- e.g. "agent_uses_model"
relationship_type TEXT     -- human-readable label
properties JSONB
nuguard_edge JSONB         -- full NuGuard SBOM Edge (NuGuard SBOM path only)
```

---

## NuGuardEnricher API

```python
from ai_asset_service.core.enricher import NuGuardEnricher

enricher = NuGuardEnricher()
node_rows, edge_rows = enricher.process_document(document)  # document: AiSbomDocument
```

**`node_rows`** — list of dicts ready for DB insert:
```python
{
  "canonical_id": str,
  "type": str,           # NodeType.value
  "name": str,
  "namespace": str,
  "file_path": str,
  "line_start": int,
  "properties": dict,
  "nuguard_node": dict,  # full model_dump(mode="json")
  "_nuguard_id": UUID,   # internal — used by caller to resolve edges
}
```

**`edge_rows`** — list of dicts:
```python
{
  "source_nuguard_id": UUID,  # resolve to DB node UUID via lookup
  "target_nuguard_id": UUID,
  "edge_type": str,
  "relationship_type": str,
  "confidence": float,
  "properties": dict,
  "nuguard_edge": dict,
}
```

Callers are responsible for resolving `source_nuguard_id`/`target_nuguard_id` to DB UUIDs after the node insert batch.

---

## Legacy Adapter Architecture

Used only as fallback when NuGuard SBOM extraction fails.

### BaseAdapter

```python
class MyAdapter(BaseAdapter):
    FRAMEWORK_NAME = "myframework"
    FRAMEWORK_ALIASES = ["my_framework"]

    def can_process(self, file_path: str, parse_result: Any) -> bool:
        """Return True if this file/parse_result contains relevant imports."""
        ...

    def extract(self, files: List[Tuple[str, str]]) -> AdapterResult:
        """
        Returns AdapterResult(nodes=List[Node], edges=List[Edge], evidence=List[Evidence])
        """
        ...
```

### Node / Edge dataclasses (for adapters)

```python
Node(
    canonical_id="agent:langchain:MyAgent",
    node_type=NodeType.AGENT,
    name="MyAgent",
    namespace="langchain",
    file_path="src/agent.py",
    line_start=42,
    metadata={"agent_type": "conversable", "framework": "langchain"},
    confidence=0.95,
)

Edge(
    source_id="agent:langchain:MyAgent",  # canonical_id
    target_id="model:openai:gpt-4",
    edge_type=EdgeType.AGENT_USES_MODEL,
)
```

### Supported Adapters (Python)
- `autogen` — AutoGen v0.2 + v0.4 agents, GroupChat
- `bedrock_agents` — AWS Bedrock invoke_agent / InvokeInlineAgentCommand
- `crewai` — CrewAI Crew, Agent, Task
- `google_adk` — Google Agent Development Kit
- `langgraph` — LangGraph StateGraph, agents
- `llamaindex` — LlamaIndex VectorStoreIndex, query engines
- `openai_agents` — OpenAI Agents SDK (Agent, Runner, handoffs)
- `prompts` — Generic PromptTemplate extraction
- `semantic_kernel` — Semantic Kernel Kernel, plugins
- `llm_clients` — Bare LLM API calls (openai, anthropic, google)
- `datastores` — Vector store and database connections

TypeScript adapters mirror the Python set under `adapters/typescript/`.

---

## assessment_service Integration

When assessment_service evaluates an AIBOM scan, it reads node `properties` JSONB (and `nuguard_node` for Phase 3+) to generate evidence for each control.

Key property fields consumed by assessment:
```python
properties = {
    "framework":    "langchain",   # used for FRAMEWORK controls
    "model_name":   "gpt-4",       # used for MODEL controls
    "provider":     "openai",      # used for vendor controls
    "file_path":    "src/agent.py",
    "line_start":   42,
    "role":         "system",      # for PROMPT nodes
    "prompt_type":  "instructions",
    "is_template":  True,
    "injection_risk_score": 0.7,   # computed for PROMPT nodes
}
```

The `ai_sbom_assessable` flag on `control_library` rows (migration 013) marks which controls should use the AIBOM evidence path vs. requiring manual attestation.

---

## Querying AIBOM from Frontend

```typescript
// All under CONFIG.ASSETS_SERVICE_URL = '/api/assets'
GET /api/assets/scans/{scanId}/nodes?page=1&page_size=50&type=AGENT
GET /api/assets/scans/{scanId}/nodes/types           // → [{ type, count }]
GET /api/assets/scans/{scanId}/edges
GET /api/assets/scans/{scanId}/evidence
GET /api/assets/scans/{scanId}/graph                 // full graph
GET /api/assets/scans/{scanId}/export/nuguard        // raw AiSbomDocument
```

---

## Common Pitfalls

| Pitfall | Correct approach |
|---------|-----------------|
| Reading `node.metadata.provider` | Read `node.metadata.extras.get("provider")` |
| Creating a new adapter without updating `registry.py` | Always register via `AdapterRegistry.register(MyAdapter)` |
| Using `node.id` as the DB primary key | NuGuard SBOM `node.id` is an internal UUID; store in `properties.nuguard_node_id`; DB gets its own UUID |
| Assuming `canonical_id` is unique per scan | It's unique per tenant across scans — deduplication intentional |
| Adding a new `ComponentType` without updating `_COMPONENT_TO_NODE_TYPE` | Add mapping in `enricher.py`; also add to `NodeType` enum in `ids.py` if it's a new type |
| Storing sensitive data in `properties` JSONB | Properties are returned in API responses; never store secrets, tokens, or PII there |
