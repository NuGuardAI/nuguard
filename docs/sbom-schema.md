# NuGuard AI SBOM Schema

This document describes the canonical AI-SBOM document shape used by NuGuard. The schema is defined by `AiSbomDocument` in the Pydantic models and enforced by the bundled JSON Schema at `nuguard/sbom/schemas/aibom.schema.json`.

Current schema version: **1.5.0**

Schema URI: `https://nuguard.ai/schemas/aibom/1.5.0/aibom.schema.json`

---

## Top-Level Object

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | string | - | AIBOM schema version (semver). Default: `"1.5.0"` |
| `generated_at` | string (ISO 8601) | yes | UTC timestamp of generation |
| `generator` | string | - | Tool that produced the document. Default: `"nuguard"` |
| `target` | string | **yes** | Repository URL or local path that was scanned |
| `nodes` | Node[] | - | Detected AI components |
| `edges` | Edge[] | - | Directed relationships between components |
| `deps` | PackageDep[] | - | Package dependencies from manifests |
| `summary` | ScanSummary \| null | - | Scan-level metadata: use-case summary, frameworks, modalities, API endpoints, deployment context, security posture, and SBOM 1.5.0 enrichment metrics |
| `relationship_graph_md` | string \| null | - | Mermaid flowchart plus LLM-written narrative of key component relationships. Only populated when LLM enrichment is enabled during SBOM generation. |

```json
{
  "schema_version": "1.5.0",
  "generated_at": "2026-06-08T00:00:00Z",
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

A Node represents a detected AI component, service boundary, infrastructure resource, policy surface, or artifact used by the application.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | UUID string | - | Stable UUID for edge references. Generated when omitted. |
| `name` | string | **yes** | Display name of the component |
| `component_type` | ComponentType | **yes** | Component category |
| `confidence` | float [0, 1] | **yes** | Extraction confidence |
| `metadata` | NodeMetadata | - | Typed and adapter-specific metadata. Empty object by default. |
| `evidence` | Evidence[] | - | Detection evidence supporting this node |

### ComponentType values

| Value | What it represents |
|---|---|
| `AGENT` | An LLM-backed agent, orchestrator, sub-agent, crew member, or assistant |
| `GUARDRAIL` | A safety check, topic filter, output validator, or content policy |
| `FRAMEWORK` | The agentic framework in use, such as LangGraph, CrewAI, OpenAI Agents, ADK, or MCP |
| `MODEL` | An LLM, embedding model, or model artifact reference |
| `TOOL` | A tool/function exposed to an agent |
| `DATASTORE` | A database, vector store, cache, or file store accessed by the app |
| `AUTH` | An authentication provider, middleware, or auth service |
| `PRIVILEGE` | A permission scope or privilege boundary |
| `API_ENDPOINT` | An HTTP or MCP endpoint exposed by the application |
| `DEPLOYMENT` | A cloud, container, Kubernetes, CI/CD, or runtime deployment resource |
| `PROMPT` | A system prompt, instruction template, backstory, or prompt file |
| `CONTAINER_IMAGE` | A Docker or OCI container image |
| `IAM` | An IAM role, policy, service account, managed identity, or role binding |

---

## NodeMetadata

All fields are optional unless marked otherwise. Fields are populated by whichever adapter or enricher detected the node. Unused nullable fields are omitted from generated JSON.

### Common and enrichment fields

| Field | Type | Description |
|---|---|---|
| `description` | string | Human-readable component description. For TOOL nodes, usually extracted from function docstrings; for AGENT nodes, usually extracted from purpose/role text. |
| `descriptive_name` | string | SBOM 1.5.0 LLM-generated human-readable label, e.g. `"User Authentication API"` |
| `loc` | integer | SBOM 1.5.0 total lines of source code across the files where this component is defined |
| `dependency_names` | string[] | SBOM 1.5.0 package names from manifests that appear in this component's source files |
| `instrumentation` | InstrumentationDetail | SBOM 1.5.0 observability and monitoring tooling detected for this component |
| `testing` | TestingDetail | SBOM 1.5.0 test coverage and CI/CD posture for this component |
| `extras` | object | Adapter-specific key/value pairs, such as provider, model family, version, prompt content, or confidence breakdown |

### AGENT and FRAMEWORK fields

| Field | Type | Description |
|---|---|---|
| `framework` | string | Agentic framework, e.g. `"langgraph"`, `"crewai"`, `"mcp-server"` |
| `system_prompt_excerpt` | string | First 500 chars of the agent's system prompt, instructions, or backstory. Used by red-team scenario generation. |
| `injection_risk_score` | float [0, 1] | Pre-computed injection risk score set by the graph enricher. Derived from privileged tools, unauthenticated paths, reachable sensitive datastores, and unguarded HITL triggers. |
| `model_name` | string | LLM or embedding model name, e.g. `"gpt-4o"`, `"gemini-2.0-flash"` |

### MODEL fields

| Field | Type | Description |
|---|---|---|
| `model_name` | string | LLM, embedding model, or artifact name |
| `source_url` | string | SBOM 1.5.0 model artifact origin URL, such as a Hugging Face repo, local path, or API base endpoint |
| `integrity_hash` | string | SBOM 1.5.0 model artifact SHA-256 or git revision hash for supply-chain verification |
| `checksum` | string | SBOM 1.5.0 model artifact checksum, such as md5, sha1, or sha256 |

### TOOL fields

| Field | Type | Description |
|---|---|---|
| `parameters` | ToolParameter[] | Parameters accepted by this tool, from signatures and docstrings. Used by TOOL_ABUSE red-team scenarios. |
| `no_auth_required` | boolean | True when the tool or endpoint is invocable without authentication |
| `high_privilege` | boolean | True when the tool has access to administrative or cross-tenant resources |
| `sql_injectable` | boolean | True when the tool constructs SQL from agent-provided string parameters |
| `ssrf_possible` | boolean | True when the tool accepts URL parameters fetched server-side |
| `accepts_external_url` | boolean | True when the tool has a URL or endpoint parameter passed to an outbound request |
| `reads_external_content` | boolean | True when the tool fetches content from an external source, such as web, email, or GitHub |
| `mcp_server_url` | string | URL of the MCP server this tool belongs to |
| `trust_level` | string | MCP server trust: `"trusted"`, `"untrusted"`, or `"n/a"`. External MCP servers are untrusted unless listed in `redteam.mcp_trusted_servers` in `nuguard.yaml`. |

### GUARDRAIL fields

| Field | Type | Description |
|---|---|---|
| `rules_excerpt` | string | Short description of the guardrail's rules, validator class, or blocking logic |
| `blocked_topics` | string[] | Topics this guardrail is known to block |
| `blocked_actions` | string[] | Actions this guardrail is known to block |
| `refusal_style` | string | How this guardrail normally refuses, e.g. `"hard_block"`, `"redirect"`, `"warn"` |

### API_ENDPOINT fields

| Field | Type | Description |
|---|---|---|
| `endpoint` | string | Endpoint address, e.g. `"/chat"` or `"0.0.0.0:8080 (sse)"` |
| `method` | string | HTTP method: `"GET"`, `"POST"`, etc. |
| `transport` | string | Transport protocol: `"sse"`, `"streamable-http"`, `"stdio"` |
| `server_name` | string | Server display name, such as FastMCP `name` kwarg or inferred service name |
| `auth_required` | boolean | True when this endpoint requires authentication |
| `auth_scope` | string | OAuth2 scope or role required, e.g. `"user"`, `"admin"`, `"none"` |
| `accepts_user_input` | boolean | True when user-controlled input is accepted in body or query params |
| `returns_sensitive_data` | boolean | True when this endpoint returns PII, PHI, PFI, or other sensitive data |
| `rate_limited` | boolean | True when rate limiting is configured |
| `rate_limit_detail` | RateLimitDetail | SBOM 1.5.0 structured rate-limit configuration extracted from code or IaC |
| `idor_surface` | boolean | True when the endpoint has user- or tenant-scoped path params, e.g. `{user_id}` |
| `path_params` | string[] | Path parameter names extracted from the URL template |
| `request_body_schema` | object | Pydantic/dataclass field map: `{field_name: type_string}` |
| `request_schema` | object | SBOM 1.5.0 request body JSON schema for API endpoints, keyed by Pydantic model class name |
| `response_schema` | object | SBOM 1.5.0 response body JSON schema for API endpoints, keyed by Pydantic model class name |
| `chat_payload_key` | string | Inferred primary prompt field in the request body, e.g. `"message"` or `"query"` |
| `chat_payload_list` | boolean | True when the chat payload key is typed as a list |
| `response_text_key` | string | Inferred primary response text field in the response body |
| `context_payload_fields` | object | Non-chat context fields detected in POST body schemas. Values are `"identity"` for static user/tenant/account identifiers or `"session"` for per-conversation identifiers. |
| `no_auth_required` | boolean | True when the endpoint is invocable without authentication |

### DATASTORE fields

| Field | Type | Description |
|---|---|---|
| `datastore_type` | string | Technology, e.g. `"redis"`, `"postgres"`, `"pinecone"` |
| `data_classification` | string[] | PII/PHI/PFI classification labels detected in schemas |
| `classified_tables` | string[] | SQL table or Python model names that carry sensitive fields |
| `classified_fields` | object | Per-table mapping of sensitive field names to classification labels, e.g. `{"patients": ["name", "dob"]}` |
| `pii_fields` | string[] | Flat list of general PII field names, e.g. `["name", "email", "phone"]` |
| `phi_fields` | string[] | Flat list of HIPAA-regulated PHI field names, e.g. `["diagnosis", "medication"]` |
| `pfi_fields` | string[] | Flat list of Personal Financial Information field names, e.g. `["card_number", "ssn", "account_balance"]` |
| `access_type` | string | Datastore access mode from the `ACCESSES` edge: `"read"`, `"write"`, or `"readwrite"` |
| `encryption_detail` | EncryptionDetail | SBOM 1.5.0 encryption and redaction posture. New consumers should prefer this over the legacy `encryption_at_rest` boolean. |
| `data_handling` | DataHandlingDetail | SBOM 1.5.0 retention, backup, anonymization, and consent posture |

### AUTH fields

| Field | Type | Description |
|---|---|---|
| `auth_type` | string | Authentication mechanism, e.g. `"oauth2"`, `"bearer"`, `"api_key"`, `"jwt"` |
| `auth_class` | string | Auth provider class name, e.g. `"BearerAuthProvider"` |
| `auth_detail` | AuthDetail | SBOM 1.5.0 detailed authentication and authorization posture |
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
| `secret_store` | string | Secret management service, e.g. `"aws_secrets_manager"`, `"azure_key_vault"`, `"gcp_secret_manager"`, `"hashicorp_vault"`, `"k8s_secret"` |
| `encryption_at_rest` | boolean | Legacy boolean: true when encryption-at-rest is explicitly configured in IaC |
| `encryption_key_ref` | string | KMS key ARN, Key Vault URI, or CMEK resource reference |
| `encryption_detail` | EncryptionDetail | SBOM 1.5.0 structured encryption and redaction posture |
| `ha_mode` | string | High-availability topology: `"multi-az"`, `"replicated"`, `"single"` |
| `has_network_policy` | boolean | SBOM 1.5.0 Kubernetes workload flag. True when covered by a NetworkPolicy resource in the same namespace. |

### CONTAINER_IMAGE fields

| Field | Type | Description |
|---|---|---|
| `image_name` | string | Container image name, e.g. `"python"` |
| `image_tag` | string | Image tag, e.g. `"3.12-slim"` |
| `image_digest` | string | Image digest, e.g. `"sha256:abc..."` |
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
| `permissions` | string[] | Actions or scopes granted by this IAM entity, up to 20 entries |
| `iam_scope` | string | Scope of the IAM binding: `"project"`, `"subscription"`, `"cluster"`, `"namespace"`, `"resource"` |
| `trust_principals` | string[] | Principals trusted to assume this role |

---

## SBOM 1.5.0 Detail Objects

### RateLimitDetail

| Field | Type | Description |
|---|---|---|
| `requests_per_minute` | integer | Maximum requests allowed per minute |
| `requests_per_hour` | integer | Maximum requests allowed per hour |
| `requests_per_day` | integer | Maximum requests allowed per day |
| `burst_size` | integer | Burst/concurrency limit above the steady-state rate |
| `window_seconds` | integer | Duration of the rate-limit window in seconds |
| `enforcement_type` | string | How the rate limit is enforced, e.g. `"decorator"`, `"middleware"`, `"api_gateway"` |

### AuthDetail

| Field | Type | Description |
|---|---|---|
| `protocols` | string[] | Auth protocols in use, e.g. `["oauth2", "bearer", "api_key"]` |
| `token_expiry_seconds` | integer | Token TTL in seconds when detectable |
| `mfa_required` | boolean | True when MFA is required for access |
| `credential_rotation_policy` | string | Rotation policy description, e.g. `"90-day"` or `"on-demand"` |
| `enforcement_strict` | boolean | True when auth is enforced on every request with no opt-out |
| `auth_roles` | string[] | Roles or scopes required for access, e.g. `["admin", "read:users"]` |

### EncryptionDetail

| Field | Type | Description |
|---|---|---|
| `in_transit` | boolean | True when data is encrypted in transit |
| `at_rest` | boolean | True when data is encrypted at rest |
| `algorithm` | string | Encryption algorithm, e.g. `"AES256"`, `"aws:kms"`, `"RSA-4096"` |
| `tls_min_version` | string | Minimum TLS version enforced, e.g. `"TLS1.2"`, `"TLS1.3"` |
| `redacted_fields` | string[] | Field names whose values are redacted before logging or storage |
| `log_redaction_enabled` | boolean | True when sensitive fields are masked/redacted in logs |
| `key_management` | string | Key management service, e.g. `"aws_kms"`, `"gcp_cmek"`, `"azure_key_vault"` |

### DataHandlingDetail

| Field | Type | Description |
|---|---|---|
| `retention_days` | integer | Data retention period in days |
| `purge_schedule` | string | Schedule or trigger for data purge, e.g. `"weekly"` or `"on-account-deletion"` |
| `backup_frequency` | string | Backup frequency, e.g. `"daily"`, `"hourly"`, `"7-day-retention"` |
| `backup_encrypted` | boolean | True when backups are encrypted |
| `anonymization_method` | string | Anonymization or pseudonymization technique |
| `consent_required` | boolean | True when user consent is required before processing data |
| `cross_border_transfer` | boolean | True when data is transferred across jurisdictional borders |

### InstrumentationDetail

| Field | Type | Description |
|---|---|---|
| `tools` | string[] | Observability tools detected, e.g. `["opentelemetry", "datadog", "prometheus", "sentry"]` |
| `log_level` | string | Configured log level, e.g. `"DEBUG"`, `"INFO"` |
| `tracing_enabled` | boolean | True when distributed tracing is configured |
| `metrics_enabled` | boolean | True when metrics collection is configured |
| `sampling_rate` | float | Trace or log sampling rate in `[0.0, 1.0]` when detectable |

### TestingDetail

| Field | Type | Description |
|---|---|---|
| `has_unit_tests` | boolean | True when unit test files are detected |
| `has_integration_tests` | boolean | True when integration test files or suites are detected |
| `test_frameworks` | string[] | Test frameworks detected, e.g. `["pytest", "jest", "vitest"]` |
| `ci_cd_pipeline` | string | CI/CD platform detected, e.g. `"github_actions"`, `"gitlab_ci"`, `"jenkins"` |
| `quality_gates` | string[] | Quality gate tools detected, e.g. `["codecov", "sonarqube", "snyk"]` |
| `test_coverage_tool` | string | Coverage measurement tool detected, e.g. `"coverage.py"`, `"istanbul"` |

---

## ToolParameter

Schema for a single parameter accepted by a TOOL node. Captured from function signatures and docstrings; used by the red-team test generator for TOOL_ABUSE scenarios.

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | **yes** | Parameter name as it appears in the function signature |
| `type` | string \| null | - | Python/TypeScript type annotation, e.g. `"str"`, `"int"`, `"dict"` |
| `description` | string \| null | - | Parameter description from the function docstring |
| `required` | boolean | - | True when the parameter has no default value. Default: `true` |

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
| `kind` | string | **yes** | Detection method. See EvidenceKind values below. |
| `confidence` | float [0, 1] | **yes** | Evidence-level confidence |
| `detail` | string | **yes** | For PROMPT nodes: `"<adapter>: <evidence_kind>"` with full content in `metadata.extras.content`. For all other nodes: `"<adapter>: <snippet>"` up to 500 chars. |
| `location` | SourceLocation | **yes** | File and line pointer |

### EvidenceKind values

| Value | Meaning |
|---|---|
| `ast` | Generic AST evidence |
| `ast_instantiation` | Class or object instantiation evidence |
| `ast_import` | Import statement evidence |
| `ast_call` | Function call evidence |
| `ast_method_call` | Method call evidence |
| `ast_decorator` | Decorator evidence |
| `ast_constant` | Constant or literal assignment evidence |
| `ast_string_literal` | String literal evidence |
| `regex` | Regex match evidence |
| `config` | Configuration file evidence |
| `iac` | Infrastructure-as-code evidence |
| `yaml` | YAML evidence |
| `dockerfile` | Dockerfile evidence |
| `nginx` | NGINX configuration evidence |
| `prompt_file` | Prompt file evidence |
| `inferred` | Inferred evidence derived from other detections |
| `llm_discovery` | LLM-assisted discovery evidence |

### SourceLocation

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | **yes** | Relative path to the source file |
| `line` | integer \| null | - | 1-based line number, if known |

---

## Edge

A directed relationship between two nodes.

| Field | Type | Required | Description |
|---|---|---|---|
| `source` | UUID string | **yes** | ID of the source Node |
| `target` | UUID string | **yes** | ID of the target Node |
| `relationship_type` | RelationshipType | **yes** | One of the values listed below |
| `access_type` | AccessType \| null | - | Access direction for `ACCESSES` edges |

### RelationshipType values

| Value | Meaning |
|---|---|
| `CALLS` | Agent calls a tool or sub-agent |
| `ACCESSES` | Agent or tool reads from or writes to a datastore |
| `USES` | Component depends on a framework, model, auth provider, or other component |
| `PROTECTS` | A guardrail protects an agent or endpoint |
| `DEPLOYS` | A deployment resource hosts a container or service |

### AccessType values

| Value | Meaning |
|---|---|
| `read` | Read-only access |
| `write` | Write-only access |
| `readwrite` | Both read and write access |

---

## PackageDep

A single declared package dependency from a manifest file.

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | **yes** | Package name. Python names are PEP 503-normalized; JavaScript names retain their package spelling. |
| `version_spec` | string | **yes** | Version specifier as declared, e.g. `">=1.70,<2.0"`, `"^18.0.0"`, or `""` |
| `purl` | string | **yes** | Package URL, e.g. `"pkg:pypi/openai@1.70.0"` or `"pkg:npm/%40langchain/core@0.3.0"` |
| `group` | string | **yes** | Dependency group: `"runtime"`, `"dev"`, `"optional:<name>"`, or `"optional:peer"` |
| `source_file` | string | **yes** | Manifest file the dependency was extracted from, e.g. `"pyproject.toml"` or `"package.json"` |

---

## ScanSummary

Scan-level metadata derived during extraction. Populated when `nuguard sbom generate` runs.

### Application metadata

| Field | Type | Description |
|---|---|---|
| `use_case` | string | Human-readable description of the application's AI use cases |
| `frameworks` | string[] | Agentic framework names detected, e.g. `["langgraph", "crewai"]` |
| `modalities` | string[] | Supported I/O modalities in upper-case, e.g. `["TEXT", "VOICE"]` |
| `modality_support` | object | Detailed modality flags: `{"text": true, "voice": false}` |
| `node_counts` | object | Count of nodes per ComponentType, e.g. `{"AGENT": 3, "MODEL": 2}` |
| `total_loc` | integer | SBOM 1.5.0 total lines of source code scanned across all files in the repository |
| `uses_streaming` | boolean | True when the app exposes streaming output endpoints. The behavior engine reads this to use streaming-aware turn execution. |
| `streaming_endpoints` | string[] | Endpoint paths confirmed to serve streaming output, e.g. `["/run_sse", "/chat/stream"]` |

### API and network

| Field | Type | Description |
|---|---|---|
| `api_endpoints` | string[] | API route paths extracted from source, e.g. `["/chat", "/health"]` |
| `local_url` | string \| null | Inferred local dev URL, e.g. `"http://localhost:8000"` |
| `staging_urls` | string[] | Staging/QA deployment URLs |
| `production_urls` | string[] | Production deployment URLs |
| `deployment_urls` | string[] | Canonical deployment URLs found in IaC/workflow files |

### Deployment and infrastructure

| Field | Type | Description |
|---|---|---|
| `deployment_platforms` | string[] | Cloud/CI platforms inferred from IaC files, e.g. `["AWS", "GCP"]` |
| `regions` | string[] | Cloud regions referenced in IaC/config, e.g. `["us-east-1"]` |
| `environments` | string[] | Deployment environments inferred from config, e.g. `["prod", "staging"]` |
| `iac_accounts` | string[] | Cloud account IDs, subscription IDs, or project IDs found in IaC |
| `availability_zones` | string[] | All cloud availability zones referenced in IaC files |
| `secret_stores` | string[] | Deduped secret management services referenced across IaC files |
| `encryption_at_rest_coverage` | boolean | True when at least one IaC resource has encryption-at-rest configured |
| `k8s_network_policy_namespaces` | string[] | SBOM 1.5.0 Kubernetes namespaces with at least one NetworkPolicy resource detected. Used by NGA-013 to assert cross-file coverage. |

### Security posture and static analysis evidence

| Field | Type | Description |
|---|---|---|
| `security_findings` | string[] | Notable security/resilience findings across IaC and container config, e.g. `["container_runs_as_root", "missing_health_check", "secrets_in_env_vars"]` |
| `iam_principals` | string[] | IAM role ARNs, GCP service account emails, and Azure managed identity names |
| `service_accounts` | string[] | K8s ServiceAccount names and GCP/Azure service account identifiers |
| `iac_security_summary` | string \| null | LLM-generated security briefing covering deployment posture, IAM configuration, secret management, encryption, HA, and CI/CD risks. Only populated when LLM enrichment is enabled. |
| `github_actions_content` | string | SBOM 1.5.0 raw concatenation of detected GitHub Actions workflow YAML files. Used by NGA-010, NGA-011, and NGA-014 regex rules as a fallback when structured workflow findings are not available. |
| `workflow_security_findings` | object[] | SBOM 1.5.0 structured findings emitted by the GitHub Actions adapter. Each object uses `{rule_signal, path, line, snippet, confidence}`. |

### Data classification

| Field | Type | Description |
|---|---|---|
| `data_classification` | string[] | Union of all classification labels detected across the repo, e.g. `["PHI", "PII", "PFI"]` |
| `classified_tables` | string[] | Names of SQL tables or Python models that contain classified data fields |

### Runtime environment

| Field | Type | Description |
|---|---|---|
| `startup_commands` | object[] | Startup commands from `package.json`, Makefile, Procfile, `pyproject.toml`, or inferred entry points. Each entry is `{command, source, label}` where `label` is `"dev"` or `"start"`. |
| `env_files` | string[] | Relative paths to `.env`/dotenv files found in the repo |
| `env_var_keys` | string[] | Sorted list of environment variable keys across dotenv files. Values are intentionally omitted from the SBOM. |
| `log_paths` | string[] | Log file paths discovered during scanning, relative to app root |

### LLM enrichment usage

| Field | Type | Description |
|---|---|---|
| `tokens_used_for_enrichment` | integer | SBOM 1.5.0 total LLM tokens consumed during SBOM enrichment |
| `input_tokens_used` | integer | SBOM 1.5.0 prompt/input tokens consumed during SBOM enrichment |
| `output_tokens_used` | integer | SBOM 1.5.0 completion/output tokens consumed during SBOM enrichment |
| `llm_model_used` | string | SBOM 1.5.0 LLM model string used for SBOM enrichment, e.g. `"gemini/gemini-2.0-flash"` |

### App-wide posture summaries

| Field | Type | Description |
|---|---|---|
| `instrumentation` | InstrumentationDetail | SBOM 1.5.0 app-wide observability and monitoring tooling summary |
| `testing` | TestingDetail | SBOM 1.5.0 app-wide testing and CI/CD posture summary |

---

## NGA and MITRE ATLAS Static Analysis Inputs

The SBOM does not store `nuguard analyze` findings directly. Instead, `nuguard analyze` consumes the SBOM and emits NGA findings, optionally enriched with MITRE ATLAS annotations. SBOM 1.5.0 adds several evidence fields so the 18 NGA rules can reason over generated SBOMs without re-scanning source where possible.

| Rule area | SBOM evidence fields used |
|---|---|
| Sensitive data to external LLMs | `MODEL` nodes (`model_name`, `source_url`, `extras`), `DATASTORE` nodes (`data_classification`, `pii_fields`, `phi_fields`, `pfi_fields`), and `USES`/`ACCESSES` edges |
| Insufficient guardrails and missing HITL | `GUARDRAIL` nodes, `PROTECTS` edges, `AGENT` nodes, `TOOL` risk fields, and `injection_risk_score` |
| Secrets and workflow injection | `summary.env_var_keys`, `summary.security_findings`, `summary.github_actions_content`, and `summary.workflow_security_findings` |
| Container hardening | `CONTAINER_IMAGE` fields: `runs_as_root`, `has_health_check`, `has_resource_limits`, `image_tag`, `base_image`, `registry` |
| Datastore encryption and isolation | `DATASTORE` fields, `encryption_detail`, `encryption_at_rest`, `data_handling`, `IAM` nodes, and `ACCESSES` edges |
| Missing endpoint auth and IDOR | `API_ENDPOINT` fields: `auth_required`, `no_auth_required`, `auth_scope`, `idor_surface`, `path_params`, `context_payload_fields`, `request_schema`, and `response_schema` |
| Overly permissive IAM | `IAM` fields: `iam_type`, `principal`, `permissions`, `iam_scope`, `trust_principals`, plus `summary.iam_principals` and `summary.service_accounts` |
| Untrusted model registry and model integrity | `MODEL` fields: `source_url`, `integrity_hash`, `checksum`, `dependency_names`, and `extras` |
| Audit logging and observability | Node and summary `instrumentation`, `summary.log_paths`, and `summary.security_findings` |
| Kubernetes network policy | `DEPLOYMENT.has_network_policy` and `summary.k8s_network_policy_namespaces` |

MITRE ATLAS annotations are produced by the analysis layer. They appear on analysis findings under an `atlas` block, not on the SBOM document itself. A typical analysis finding includes:

```json
{
  "source": "nga-rules",
  "nga_rule": "NGA-006",
  "mitre_atlas_technique": "AML.T0051",
  "atlas": {
    "atlas_version": "v2",
    "techniques": [
      {
        "technique_id": "AML.T0051",
        "technique_name": "LLM Prompt Injection",
        "atlas_url": "https://atlas.mitre.org/techniques/AML.T0051"
      }
    ]
  }
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

---

## Example

Generated SBOM examples are available under `tests/apps/*/*.sbom.json`. The committed `docs/sample-sbom.json` may lag behind the current schema until regenerated, so prefer `nuguard sbom schema` or `nuguard/sbom/schemas/aibom.schema.json` as the source of truth for validation.
