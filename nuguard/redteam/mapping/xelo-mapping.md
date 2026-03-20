# Xelo → NuGuard Attack Graph Mapping

**Xelo SBOM schema reference:** https://github.com/NuGuardAI/xelo/blob/main/docs/aibom-schema.md  
**Pinned version:** v1.3.0 (`schema_version: "1.3.0"`)

## ComponentType → NodeType

How Xelo `component_type` values (SCREAMING_SNAKE_CASE) map to NuGuard Attack Surface Graph `node_type` values (lowercase). Implemented in `src/graph/mapper.py`.

| Xelo `component_type` | NuGuard `node_type` | Mapping notes |
|---|---|---|
| `AGENT` | `agent` | LangGraph graph, CrewAI crew, AutoGen agent, OpenAI Agent SDK, etc. |
| `MODEL` | `model` | LLM or embedding model — e.g. `gpt-4o`, `claude-3-5-sonnet`, `text-embedding-3-small` |
| `TOOL` | `tool` | Function tool or MCP tool wired to an agent |
| `PROMPT` | `prompt` | System instruction or prompt template |
| `DATASTORE` | `vectorstore` | When `metadata.datastore_type` contains a vector store keyword: `chromavector`, `pinecone`, `weaviate`, `qdrant`, `milvus`, `faiss` |
| `DATASTORE` | `database` | All other datastore types — `postgres`, `mysql`, `sqlite`, `redis`, `mongodb`, `elasticsearch` |
| `GUARDRAIL` | `guardrail` | Content filter or safety validator (Guardrails AI, NeMo Guardrails, etc.) |
| `AUTH` | `identity` | Authentication node — OAuth2, Bearer, API key, JWT, MCP auth provider |
| `PRIVILEGE` | `privilege` | Capability grant — `db_write`, `filesystem_write`, `code_execution`, `network_out`, etc. |
| `IAM` | `iam` | IAM role, policy, service account, managed identity, K8s role binding |
| `API_ENDPOINT` | `api_endpoint` | Exposed API route or MCP endpoint |
| `FRAMEWORK` | `agent` | No dedicated agents found; framework absorbed into an agent-type node |
| `CONTAINER_IMAGE` | *(skipped in v1)* | Infrastructure artifact — not an attack surface node |
| `DEPLOYMENT` | *(skipped in v1)* | IaC deployment artifact — not an attack surface node |

## RelationshipType → EdgeType

How Xelo `relationship_type` values map to NuGuard Attack Surface Graph `edge_type` values. The `ACCESSES` mapping depends on write access context derived from the source node's metadata.

| Xelo `relationship_type` | NuGuard `edge_type` | Conditions |
|---|---|---|
| `USES` | `INVOKES` | Agent uses a model |
| `CALLS` | `INVOKES` | Agent calls a tool |
| `ACCESSES` | `READS` | Default — read access to a datastore |
| `ACCESSES` | `WRITES` | When source node metadata indicates write access (`privilege_scope` contains `_write`, or DATASTORE node `write_access=true`) |
| `PROTECTS` | `GUARDS` | Guardrail protects an agent or model |
| `DEPLOYS` | *(skipped in v1)* | Deployment infra — not mapped to attack graph |

## Risk Attribute Assignment

The enricher (`src/graph/enricher.py`) assigns `RiskAttribute` labels to nodes based on the following rules derived from Xelo metadata:

| Condition | Risk Attribute |
|---|---|
| `tool` or `api_endpoint` node with `metadata.auth_type == "none"` | `no-auth-required` |
| `database` or `vectorstore` with `metadata.data_classification` includes `PII` or `PHI` | `PII-stores` |
| `prompt` node with `metadata.extras.injection_risk_score > 0.5` | (scored by prompt-injection scenario generator) |
| `privilege` node with `metadata.privilege_scope` in `[db_write, filesystem_write, code_execution, admin]` | `high-privilege` |
| `api_endpoint` accepting `POST/PUT/PATCH/DELETE` without auth | `IDOR-surface` |
| `tool` or `api_endpoint` with no rate limiting | `no-rate-limit` |
| `agent` or `api_endpoint` accepting external network calls | `SSRF-possible` |
| `agent` or `tool` with `memory_access=true` or persistent memory store | `HITL-bypass-risk` |

