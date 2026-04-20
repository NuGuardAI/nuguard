# NuGuard AI SBOM Schema

This document describes the canonical AI-SBOM document shape used by NuGuard. The schema is defined by `AiSbomDocument` in the Pydantic models and enforced by the bundled JSON Schema at `nuguard/sbom/schemas/aibom.schema.json`.

Current schema version: **1.4.0**

Schema URI: `https://nuguard.ai/schemas/aibom/1.4.0/aibom.schema.json`

---

## Top-Level Object

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | string | — | AIBOM schema version (semver). Default: `"1.4.0"` |
| `generated_at` | string (ISO 8601) | yes | UTC timestamp of generation |
| `generator` | string | — | Tool that produced the document. Default: `"nuguard"` |
| `target` | string | **yes** | Repository URL or local path that was scanned |
| `nodes` | Node[] | — | Detected AI components |
| `edges` | Edge[] | — | Directed relationships between components |
| `deps` | PackageDep[] | — | Package dependencies from manifests |
| `summary` | ScanSummary \| null | — | Scan-level metadata (frameworks, modalities, deployment info, etc.) |
| `relationship_graph_md` | string \| null | — | Mermaid flowchart + LLM narrative of key component relationships. Only populated when `--llm` is enabled during SBOM generation. |

```json
{
  "schema_version": "1.4.0",
  "generated_at": "2026-04-18T00:00:00Z",
  "generator": "nuguard",
  "target": "https://github.com/org/repo",
  "nodes": [],
  "edges": [],
  "deps": [],
  "summary": null,
  "relationship_graph_md": null
}
```

---

## Node

A detected AI-related component.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | UUID string | — | Stable UUID for edge references (auto-generated) |
| `name` | string | **yes** | Display name of the component |
| `component_type` | ComponentType | **yes** | One of the values listed below |
| `confidence` | float [0, 1] | **yes** | Extraction confidence |
| `metadata` | NodeMetadata | — | Typed + open-ended metadata (see below) |
| `evidence` | Evidence[] | — | Detection evidence entries |

### ComponentType values

| Value | What it represents |
|---|---|
| `AGENT` | An LLM-backed agent (orchestrator, sub-agent, crew member, etc.) |
| `GUARDRAIL` | A safety check, topic filter, output validator, or content policy |
| `FRAMEWORK` | The agentic framework in use (LangGraph, CrewAI, OpenAI Agents, ADK, etc.) |
| `MODEL` | An LLM or embedding model reference |
| `TOOL` | A tool/function exposed to an agent |
| `DATASTORE` | A database, vector store, or file store accessed by the app |
| `AUTH` | An authentication provider or middleware |
| `PRIVILEGE` | A permission scope or privilege boundary |
| `API_ENDPOINT` | An HTTP endpoint exposed by the application |
| `DEPLOYMENT` | A cloud or container deployment resource |
| `PROMPT` | A system prompt, instruction template, or backstory |
| `CONTAINER_IMAGE` | A Docker or OCI container image |
| `IAM` | An IAM role, policy, service account, or managed identity |

### Example node

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "name": "support-agent",
  "component_type": "AGENT",
  "confidence": 0.98,
  "metadata": {
    "framework": "openai-agents",
    "system_prompt_excerpt": "You are a customer support assistant.",
    "injection_risk_score": 0.72,
    "extras": { "adapter": "openai_agents" }
  },
  "evidence": [
    {
      "kind": "ast",
      "confidence": 0.98,
      "detail": "openai_agents: agent instantiation",
      "location": { "path": "app/agents.py", "line": 12 }
    }
  ]
}
```

---

## NodeMetadata

All fields are optional (null by default). Fields are populated by whichever adapter detected the node; unused fields are omitted from the JSON output.

### General fields (any node type)

| Field | Type | Description |
|---|---|---|
| `framework` | string | Agentic framework, e.g. `"langgraph"`, `"crewai"`, `"mcp-server"` |
| `description` | string | Human-readable description. For TOOL nodes: from docstring. For AGENT nodes: role/purpose description. Used by the redteam test generator to craft payloads. |
| `extras` | object | Adapter-specific key/value pairs (provider, model_family, version, …) |

### AGENT fields

| Field | Type | Description |
|---|---|---|
| `system_prompt_excerpt` | string | First 500 chars of the agent's system prompt / instructions. Used by the redteam generator to craft context-authentic payloads. |
| `injection_risk_score` | float [0, 1] | Pre-computed injection risk score set by the graph enricher. Derived from tool privilege, reachable PII datastores, unguarded HITL triggers, etc. |
| `model_name` | string | LLM or embedding model name, e.g. `"gpt-4o"`, `"gemini-2.0-flash"` |

### TOOL fields

| Field | Type | Description |
|---|---|---|
| `parameters` | ToolParameter[] | Parameters accepted by this tool (from signature + docstring). Used by TOOL_ABUSE scenarios to craft parameter-injection payloads. |
| `no_auth_required` | boolean | True when the tool is invocable without authentication |
| `high_privilege` | boolean | True when the tool has access to administrative or cross-tenant resources |
| `sql_injectable` | boolean | True when the tool constructs SQL from agent-provided string parameters |
| `ssrf_possible` | boolean | True when the tool accepts URL parameters fetched server-side |
| `accepts_external_url` | boolean | True when the tool has a URL parameter passed to an outbound request |
| `reads_external_content` | boolean | True when the tool fetches content from an external source (web, email, GitHub, etc.) |
| `mcp_server_url` | string | URL of the MCP server this tool belongs to |
| `trust_level` | string | MCP server trust: `"trusted"` \| `"untrusted"` \| `"n/a"`. All external MCP servers are `"untrusted"` unless listed in `redteam.mcp_trusted_servers` in `nuguard.yaml`. |

### GUARDRAIL fields

| Field | Type | Description |
|---|---|---|
| `rules_excerpt` | string | Short description of the guardrail's rules, validator class, or blocking logic |
| `blocked_topics` | string[] | Topics this guardrail is known to block |
| `blocked_actions` | string[] | Actions this guardrail is known to block |
| `refusal_style` | string | How this guardrail normally refuses: `"hard_block"`, `"redirect"`, `"warn"`, etc. |

### API_ENDPOINT fields

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | Endpoint address, e.g. `"/chat"`, `"0.0.0.0:8080 (sse)"` |
| `method` | string | HTTP method: `"GET"`, `"POST"`, etc. |
| `transport` | string | Transport protocol: `"sse"`, `"streamable-http"`, `"stdio"` |
| `server_name` | string | Server display name (e.g. FastMCP `name` kwarg) |
| `auth_required` | boolean | True when this endpoint requires authentication |
| `auth_scope` | string | OAuth2 scope or role required: `"user"` \| `"admin"` \| `"none"` |
| `accepts_user_input` | boolean | True when user-controlled input is accepted in body or query params |
| `returns_sensitive_data` | boolean | True when this endpoint returns PII, PHI, or other sensitive data |
| `rate_limited` | boolean | True when rate limiting is configured |
| `idor_surface` | boolean | True when the endpoint has user/tenant-scoped path params (e.g. `{user_id}`) |
| `path_params` | string[] | Path parameter names extracted from the URL template |
| `request_body_schema` | object | Pydantic/dataclass field map: `{field_name: type_string}` |
| `chat_payload_key` | string | Inferred primary prompt field in the request body (e.g. `"message"`, `"query"`) |
| `chat_payload_list` | boolean | True when the chat payload key is typed as a list |
| `response_text_key` | string | Inferred primary response text field in the response body |
| `no_auth_required` | boolean | True when the endpoint is invocable without authentication |

### DATASTORE fields

| Field | Type | Description |
|---|---|---|
| `datastore_type` | string | Technology, e.g. `"redis"`, `"postgres"`, `"pinecone"` |
| `data_classification` | string[] | PII/PHI classification labels detected in schemas, e.g. `["PHI", "PII"]` |
| `classified_tables` | string[] | SQL table or Python model names that carry PII/PHI fields |
| `classified_fields` | object | Per-table mapping of sensitive field names to classification labels: `{"patients": ["name", "dob"]}` |
| `pii_fields` | string[] | Flat list of general PII field names, e.g. `["name", "email", "phone"]` |
| `phi_fields` | string[] | Flat list of HIPAA-regulated PHI field names, e.g. `["diagnosis", "medication"]` |
| `pfi_fields` | string[] | Flat list of Personal Financial Information field names (PCI-DSS/GLBA), e.g. `["card_number", "ssn"]` |
| `access_type` | string | Datastore access mode from the ACCESSES edge: `"read"` \| `"write"` \| `"readwrite"` |

### AUTH fields

| Field | Type | Description |
|---|---|---|
| `auth_type` | string | Authentication mechanism: `"oauth2"`, `"bearer"`, `"api_key"`, `"jwt"` |
| `auth_class` | string | Auth provider class name, e.g. `"BearerAuthProvider"` |
| `no_auth_required` | boolean | True when this component allows unauthenticated access |

### PRIVILEGE fields

| Field | Type | Description |
|---|---|---|
| `privilege_scope` | string | Permission scope label, e.g. `"db_write"`, `"filesystem_read"` |

### DEPLOYMENT fields

| Field | Type | Description |
|---|---|---|
| `deployment_target` | string | Cloud or container deployment target, e.g. `"aws"`, `"gcp"`, `"kubernetes"` |
| `cloud_region` | string | Cloud region, e.g. `"us-east-1"`, `"eastus"`, `"us-central1"` |
| `availability_zones` | string[] | Availability zones configured, e.g. `["us-east-1a", "us-east-1b"]` |
| `secret_store` | string | Secret management service: `"aws_secrets_manager"`, `"azure_key_vault"`, `"hashicorp_vault"`, `"k8s_secret"`, etc. |
| `encryption_at_rest` | boolean | True when encryption-at-rest is explicitly configured in IaC |
| `encryption_key_ref` | string | KMS key ARN, Key Vault URI, or CMEK resource reference |
| `ha_mode` | string | High-availability topology: `"multi-az"`, `"replicated"`, `"single"` |

### CONTAINER_IMAGE fields

| Field | Type | Description |
|---|---|---|
| `image_name` | string | Container image name, e.g. `"python"` |
| `image_tag` | string | Image tag, e.g. `"3.12-slim"` |
| `image_digest` | string | Image digest, e.g. `"sha256:abc…"` |
| `registry` | string | Registry host, e.g. `"docker.io"`, `"gcr.io"` |
| `base_image` | string | Full base image reference, e.g. `"python:3.12-slim"` |
| `runs_as_root` | boolean | True when the container is configured to run as root (UID 0) |
| `has_health_check` | boolean | True when a HEALTHCHECK instruction or liveness/readiness probe is present |
| `has_resource_limits` | boolean | True when Kubernetes resource limits are defined for the container |

### IAM fields

| Field | Type | Description |
|---|---|---|
| `iam_type` | string | IAM entity kind: `"role"`, `"policy"`, `"service_account"`, `"managed_identity"`, `"role_binding"` |
| `principal` | string | ARN, email, or object ID of the IAM principal |
| `permissions` | string[] | Actions or scopes granted by this IAM entity (up to 20 entries) |
| `iam_scope` | string | Scope of the IAM binding: `"project"`, `"subscription"`, `"cluster"`, `"namespace"`, `"resource"` |
| `trust_principals` | string[] | Principals trusted to assume this role (AWS trust policy subjects, K8s binding subjects) |

---

## ToolParameter

Schema for a single parameter accepted by a TOOL node. Captured from function signatures and docstrings; used by the redteam test generator for TOOL_ABUSE scenarios.

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | **yes** | Parameter name as it appears in the function signature |
| `type` | string \| null | — | Python/TypeScript type annotation, e.g. `"str"`, `"int"`, `"dict"` |
| `description` | string \| null | — | Parameter description from the function docstring |
| `required` | boolean | — | True when the parameter has no default value. Default: `true` |

```json
{
  "name": "query",
  "type": "str",
  "description": "The search query to send to the web search API",
  "required": true
}
```

---

## Evidence

A single piece of detection evidence supporting a Node.

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | string | **yes** | Detection method: `"ast"`, `"regex"`, `"config"`, `"iac"`, `"inferred"` |
| `confidence` | float [0, 1] | **yes** | Evidence-level confidence |
| `detail` | string | **yes** | For PROMPT nodes: `"<adapter>: <evidence_kind>"` (full content is in `metadata.extras.content`). For all other nodes: `"<adapter>: <snippet>"` (up to 500 chars). |
| `location` | SourceLocation | **yes** | File and line pointer |

### SourceLocation

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | **yes** | Relative path to the source file |
| `line` | integer \| null | — | 1-based line number, if known |

---

## Edge

A directed relationship between two nodes.

| Field | Type | Required | Description |
|---|---|---|---|
| `source` | UUID string | **yes** | ID of the source Node |
| `target` | UUID string | **yes** | ID of the target Node |
| `relationship_type` | RelationshipType | **yes** | One of the values listed below |
| `access_type` | AccessType \| null | — | Access direction for `ACCESSES` edges |

### RelationshipType values

| Value | Meaning |
|---|---|
| `CALLS` | Agent calls a tool or sub-agent |
| `ACCESSES` | Agent or tool reads from / writes to a datastore |
| `USES` | Component depends on a framework, model, or auth provider |
| `PROTECTS` | A guardrail protects an agent or endpoint |
| `DEPLOYS` | A deployment resource hosts a container or service |

### AccessType values (for `ACCESSES` edges)

| Value | Meaning |
|---|---|
| `read` | Read-only access |
| `write` | Write-only access |
| `readwrite` | Both read and write access |

```json
{
  "source": "11111111-1111-1111-1111-111111111111",
  "target": "22222222-2222-2222-2222-222222222222",
  "relationship_type": "ACCESSES",
  "access_type": "readwrite"
}
```

---

## PackageDep

A single declared package dependency from a manifest file.

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | **yes** | Package name |
| `version_spec` | string | **yes** | Version specifier as declared, e.g. `">=1.70,<2.0"` |
| `purl` | string | **yes** | Package URL, e.g. `"pkg:pypi/openai@1.70.0"` |
| `group` | string | **yes** | Dependency group: `"main"`, `"dev"`, `"optional"` |
| `source_file` | string | **yes** | Manifest file the dep was extracted from, e.g. `"pyproject.toml"` |

```json
{
  "name": "openai",
  "version_spec": ">=1.70,<2.0",
  "purl": "pkg:pypi/openai@1.70.0",
  "group": "main",
  "source_file": "pyproject.toml"
}
```

---

## ScanSummary

Scan-level metadata derived during extraction. Always populated when `nuguard sbom generate` runs.

### Application metadata

| Field | Type | Description |
|---|---|---|
| `use_case` | string | Human-readable description of the application's AI use cases |
| `frameworks` | string[] | Agentic framework names detected, e.g. `["langgraph", "crewai"]` |
| `modalities` | string[] | Supported I/O modalities in upper-case, e.g. `["TEXT", "VOICE"]` |
| `modality_support` | object | Detailed modality flags: `{"text": true, "voice": false}` |
| `node_counts` | object | Count of nodes per ComponentType, e.g. `{"AGENT": 3, "MODEL": 2}` |
| `uses_streaming` | boolean | True when the app exposes streaming output endpoints (SSE, StreamingResponse, ADK `/run_sse`, etc.). The behavior engine reads this to use streaming-aware turn execution. |
| `streaming_endpoints` | string[] | Endpoint paths confirmed to serve streaming output, e.g. `["/run_sse", "/chat/stream"]` |

### API and network

| Field | Type | Description |
|---|---|---|
| `api_endpoints` | string[] | API route paths extracted from source, e.g. `["/chat", "/health"]` |
| `local_url` | string \| null | Inferred local dev URL (e.g. `"http://localhost:8000"`), derived from `PORT` env var or startup command hints |
| `staging_urls` | string[] | Staging/QA deployment URLs |
| `production_urls` | string[] | Production deployment URLs |
| `deployment_urls` | string[] | Canonical deployment URLs found in IaC/workflow files |

### Deployment and infrastructure

| Field | Type | Description |
|---|---|---|
| `deployment_platforms` | string[] | Cloud/CI platforms inferred from IaC files, e.g. `["AWS", "GCP"]` |
| `regions` | string[] | Cloud regions referenced in IaC/config, e.g. `["us-east-1"]` |
| `environments` | string[] | Deployment environments inferred from config, e.g. `["prod", "staging"]` |
| `iac_accounts` | string[] | Cloud account IDs / subscription IDs / project IDs found in IaC |
| `availability_zones` | string[] | All cloud availability zones referenced in IaC files |
| `secret_stores` | string[] | Deduped secret management services referenced across IaC files |
| `encryption_at_rest_coverage` | boolean | True when at least one IaC resource has encryption-at-rest configured |

### Security posture

| Field | Type | Description |
|---|---|---|
| `security_findings` | string[] | Notable security/resilience findings across IaC and container config, e.g. `["container_runs_as_root", "missing_health_check", "secrets_in_env_vars"]` |
| `iam_principals` | string[] | IAM role ARNs, GCP service account emails, and Azure managed identity names |
| `service_accounts` | string[] | K8s ServiceAccount names and GCP/Azure service account identifiers |
| `iac_security_summary` | string \| null | LLM-generated security briefing covering deployment posture, IAM configuration, secret management, encryption, HA, and CI/CD risks. Only populated when `--llm` is enabled. |

### Data classification

| Field | Type | Description |
|---|---|---|
| `data_classification` | string[] | Union of all classification labels detected across the repo, e.g. `["PHI", "PII"]` |
| `classified_tables` | string[] | Names of SQL tables or Python models that contain classified data fields |

### Runtime environment

| Field | Type | Description |
|---|---|---|
| `startup_commands` | object[] | Startup commands from `package.json`, Makefile, Procfile, `pyproject.toml`, or inferred entry points. Each entry: `{"command": "...", "source": "...", "label": "dev"|"start"}` |
| `env_files` | string[] | Relative paths to `.env`/dotenv files found in the repo |
| `env_var_keys` | string[] | Sorted list of environment variable *keys* across all dotenv files (values are intentionally omitted from the SBOM) |
| `log_paths` | string[] | Log file paths discovered during scanning (relative to app root) |

### Example summary

```json
{
  "use_case": "Customer support assistant with tool access",
  "frameworks": ["openai-agents"],
  "modalities": ["TEXT"],
  "modality_support": {"text": true, "voice": false},
  "api_endpoints": ["/chat", "/health"],
  "node_counts": {"AGENT": 1, "TOOL": 3, "API_ENDPOINT": 2},
  "uses_streaming": false,
  "streaming_endpoints": [],
  "local_url": "http://localhost:8000",
  "staging_urls": [],
  "production_urls": ["https://api.example.com"],
  "deployment_platforms": ["AWS"],
  "regions": ["us-east-1"],
  "data_classification": ["PII"],
  "classified_tables": ["users"],
  "startup_commands": [
    {"command": "uv run python -m app.main", "source": "pyproject.toml", "label": "dev"}
  ],
  "env_files": [".env"],
  "env_var_keys": ["OPENAI_API_KEY", "DATABASE_URL"],
  "security_findings": [],
  "iac_accounts": [],
  "secret_stores": ["aws_secrets_manager"],
  "encryption_at_rest_coverage": true,
  "availability_zones": ["us-east-1a", "us-east-1b"],
  "iam_principals": ["arn:aws:iam::123456789012:role/AppRole"],
  "service_accounts": [],
  "iac_security_summary": null
}
```

---

## Validation

```bash
# Validate an SBOM file against the bundled schema
nuguard sbom validate --file ./app.sbom.json

# Print the bundled JSON Schema to stdout
nuguard sbom schema
```

The `test_committed_schema_matches_models` test enforces that the committed `aibom.schema.json` always matches `AiSbomDocument.model_json_schema()`.
