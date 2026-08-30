"""NuGuard data models.

All public types are Pydantic ``BaseModel`` subclasses.  The JSON schema
exported by the CLI (``nuguard schema``) is generated directly from these models
so schema and code can never drift.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .deps import PackageDep
from .types import (
    AccessType,
    ComponentType,
    RelationshipType,
)
from .types import (
    DataClassification as _DataClassification,
)
from .types import (
    DatastoreType as _DatastoreType,
)
from .types import (
    PrivilegeScope as _PrivilegeScope,
)

# ---------------------------------------------------------------------------
# Backward-compat aliases (so callers can import from either location)
# ---------------------------------------------------------------------------

NodeType = ComponentType
EdgeRelationshipType = RelationshipType
DataClassification = _DataClassification
DatastoreType = _DatastoreType
PrivilegeScope = _PrivilegeScope


class EvidenceKind(str, Enum):
    """How a piece of evidence was collected."""

    AST = "ast"
    AST_INSTANTIATION = "ast_instantiation"
    AST_IMPORT = "ast_import"
    AST_CALL = "ast_call"
    AST_METHOD_CALL = "ast_method_call"
    AST_DECORATOR = "ast_decorator"
    AST_CONSTANT = "ast_constant"
    AST_STRING_LITERAL = "ast_string_literal"
    REGEX = "regex"
    CONFIG = "config"
    IAC = "iac"
    YAML = "yaml"
    DOCKERFILE = "dockerfile"
    NGINX = "nginx"
    PROMPT_FILE = "prompt_file"
    INFERRED = "inferred"
    LLM_DISCOVERY = "llm_discovery"


class SourceLocation(BaseModel):
    """File/line pointer for a piece of evidence."""

    path: str = Field(description="Relative path to the source file")
    line: int | None = Field(default=None, description="1-based line number, if known")


# Alias for backward compatibility
EvidenceLocation = SourceLocation


class Evidence(BaseModel):
    """A single piece of detection evidence supporting a Node."""

    kind: str = Field(description="Detection method: 'ast', 'regex', 'config', 'iac', 'inferred'")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Evidence-level confidence [0, 1]",
    )
    detail: str = Field(
        description=(
            "Detection description: '<adapter>: <evidence_kind>' for PROMPT nodes "
            "(full content is in metadata.extras.content); "
            "'<adapter>: <snippet>' (up to 500 chars) for all other node types."
        )
    )
    location: SourceLocation


class ToolParameter(BaseModel):
    """Schema for a single parameter accepted by a TOOL node.

    Captured from function signatures and docstrings by the SBOM extractor.
    Used by the redteam test generator to craft parameter-injection payloads
    for TOOL_ABUSE scenarios.
    """

    name: str = Field(description="Parameter name as it appears in the function signature")
    type: str | None = Field(
        default=None,
        description="Python/TypeScript type annotation string, e.g. 'str', 'int', 'dict'",
    )
    description: str | None = Field(
        default=None,
        description="Parameter description extracted from the function docstring",
    )
    required: bool = Field(
        default=True,
        description="True when the parameter has no default value",
    )


class RateLimitDetail(BaseModel):
    """Structured rate limit configuration extracted from code or IaC."""

    requests_per_minute: int | None = Field(default=None, description="Maximum requests allowed per minute")
    requests_per_hour: int | None = Field(default=None, description="Maximum requests allowed per hour")
    requests_per_day: int | None = Field(default=None, description="Maximum requests allowed per day")
    burst_size: int | None = Field(default=None, description="Burst/concurrency limit above the steady-state rate")
    window_seconds: int | None = Field(default=None, description="Duration of the rate limit window in seconds")
    enforcement_type: str | None = Field(
        default=None,
        description="How the rate limit is enforced, e.g. 'decorator', 'middleware', 'api_gateway'",
    )


class SecurityHeaderDetail(BaseModel):
    """HTTP security-header posture extracted from code or IaC."""

    csp: bool | None = Field(default=None, description="True when a Content-Security-Policy header is set")
    x_frame_options: bool | None = Field(default=None, description="True when an X-Frame-Options header is set")
    hsts: bool | None = Field(default=None, description="True when a Strict-Transport-Security header is set")
    missing: list[str] = Field(
        default_factory=list,
        description="Security headers confirmed absent, e.g. ['csp', 'x_frame_options', 'hsts']",
    )


class CorsPolicyDetail(BaseModel):
    """CORS configuration extracted from code or IaC."""

    origin: str | None = Field(
        default=None, description="Configured allowed origin(s), e.g. '*' or 'https://example.com'"
    )
    allow_credentials: bool | None = Field(
        default=None, description="True when the CORS policy allows credentialed cross-origin requests"
    )
    wildcard_with_credentials: bool = Field(
        default=False,
        description="True when origin is wildcarded AND credentials are allowed — the dangerous combination",
    )


class AuthDetail(BaseModel):
    """Detailed authentication and authorization posture for a component."""

    protocols: list[str] = Field(
        default_factory=list,
        description="Auth protocols in use, e.g. ['oauth2', 'bearer', 'api_key', 'basic']",
    )
    token_expiry_seconds: int | None = Field(default=None, description="Token TTL in seconds when detectable")
    mfa_required: bool | None = Field(default=None, description="True when MFA is required for access")
    credential_rotation_policy: str | None = Field(
        default=None, description="Rotation policy description, e.g. '90-day', 'on-demand'"
    )
    enforcement_strict: bool | None = Field(
        default=None, description="True when auth is enforced on every request with no opt-out"
    )
    auth_roles: list[str] = Field(
        default_factory=list,
        description="Roles or scopes required for access, e.g. ['admin', 'read:users']",
    )


class EncryptionDetail(BaseModel):
    """Encryption and redaction posture for a component."""

    in_transit: bool | None = Field(default=None, description="True when data is encrypted in transit (TLS/SSL)")
    at_rest: bool | None = Field(default=None, description="True when data is encrypted at rest")
    algorithm: str | None = Field(
        default=None, description="Encryption algorithm, e.g. 'AES256', 'aws:kms', 'RSA-4096'"
    )
    tls_min_version: str | None = Field(
        default=None, description="Minimum TLS version enforced, e.g. 'TLS1.2', 'TLS1.3'"
    )
    redacted_fields: list[str] = Field(
        default_factory=list,
        description="Field names whose values are redacted before logging or storage",
    )
    log_redaction_enabled: bool | None = Field(
        default=None, description="True when sensitive fields are masked/redacted in logs"
    )
    key_management: str | None = Field(
        default=None,
        description="Key management service, e.g. 'aws_kms', 'gcp_cmek', 'azure_key_vault', 'hashicorp_vault'",
    )


class DataHandlingDetail(BaseModel):
    """Data retention, backup, anonymization, and consent configuration."""

    retention_days: int | None = Field(default=None, description="Data retention period in days")
    purge_schedule: str | None = Field(
        default=None, description="Schedule or trigger for data purge, e.g. 'weekly', 'on-account-deletion'"
    )
    backup_frequency: str | None = Field(
        default=None, description="Backup frequency, e.g. 'daily', 'hourly', '7-day-retention'"
    )
    backup_encrypted: bool | None = Field(default=None, description="True when backups are encrypted")
    anonymization_method: str | None = Field(
        default=None, description="Anonymization or pseudonymization technique, e.g. 'tokenization', 'k-anonymity'"
    )
    consent_required: bool | None = Field(
        default=None, description="True when user consent is required before processing data"
    )
    cross_border_transfer: bool | None = Field(
        default=None, description="True when data is transferred across jurisdictional borders"
    )


class InstrumentationDetail(BaseModel):
    """Observability and monitoring tooling configured for a component or application."""

    tools: list[str] = Field(
        default_factory=list,
        description="Observability tools detected, e.g. ['opentelemetry', 'datadog', 'prometheus', 'sentry']",
    )
    log_level: str | None = Field(
        default=None, description="Configured log level, e.g. 'DEBUG', 'INFO', 'WARNING'"
    )
    tracing_enabled: bool | None = Field(default=None, description="True when distributed tracing is configured")
    metrics_enabled: bool | None = Field(default=None, description="True when metrics collection is configured")
    sampling_rate: float | None = Field(
        default=None, description="Trace or log sampling rate [0.0, 1.0] when detectable"
    )


class TestingDetail(BaseModel):
    """Testing, validation, and CI/CD posture for a component or application."""

    has_unit_tests: bool | None = Field(default=None, description="True when unit test files are detected")
    has_integration_tests: bool | None = Field(
        default=None, description="True when integration test files or suites are detected"
    )
    test_frameworks: list[str] = Field(
        default_factory=list,
        description="Test frameworks detected, e.g. ['pytest', 'jest', 'vitest', 'hypothesis']",
    )
    ci_cd_pipeline: str | None = Field(
        default=None, description="CI/CD platform detected, e.g. 'github_actions', 'gitlab_ci', 'jenkins'"
    )
    quality_gates: list[str] = Field(
        default_factory=list,
        description="Quality gate tools detected, e.g. ['codecov', 'sonarqube', 'snyk']",
    )
    test_coverage_tool: str | None = Field(
        default=None, description="Coverage measurement tool detected, e.g. 'coverage.py', 'istanbul'"
    )


class NodeMetadata(BaseModel):
    """Typed + open-ended metadata attached to a Node."""

    framework: str | None = Field(
        default=None, description="Agentic framework (e.g. 'langgraph', 'crewai', 'mcp-server')"
    )
    model_name: str | None = Field(
        default=None, description="LLM / embedding model name if applicable"
    )
    datastore_type: str | None = Field(
        default=None, description="Datastore technology, e.g. 'redis', 'postgres', 'pinecone'"
    )
    auth_type: str | None = Field(
        default=None,
        description="Authentication mechanism, e.g. 'oauth2', 'bearer', 'api_key', 'jwt'",
    )
    auth_class: str | None = Field(
        default=None,
        description="Auth provider class name, e.g. 'BearerAuthProvider', 'OAuth2ClientCredentialsProvider'",
    )
    privilege_scope: str | None = Field(
        default=None,
        description="Privilege or permission scope label, e.g. 'db_write', 'filesystem_read'",
    )
    endpoint: str | None = Field(
        default=None,
        description="API endpoint address, e.g. '0.0.0.0:8080 (sse)' for MCP or '/chat' for REST",
    )
    method: str | None = Field(default=None, description="HTTP method, e.g. 'GET', 'POST'")
    transport: str | None = Field(
        default=None,
        description="Transport protocol for API/MCP nodes, e.g. 'sse', 'streamable-http', 'stdio'",
    )
    server_name: str | None = Field(
        default=None,
        description="Server display name (MCP FastMCP name kwarg, or inferred service name)",
    )
    deployment_target: str | None = Field(
        default=None,
        description="Cloud or container deployment target, e.g. 'aws', 'gcp', 'kubernetes'",
    )
    # Data classification fields (populated on DATASTORE nodes by the classification adapters)
    data_classification: list[str] | None = Field(
        default=None,
        description=(
            "PII/PHI classification labels detected in schemas stored in this datastore, "
            "e.g. ['PHI', 'PII'].  Null when no classified fields were found."
        ),
    )
    classified_tables: list[str] | None = Field(
        default=None,
        description=(
            "SQL table or Python model names within this datastore that carry PII/PHI fields."
        ),
    )
    classified_fields: dict[str, list[str]] | None = Field(
        default=None,
        description=(
            "Per-table/-model mapping of sensitive field names to their classification labels, "
            "e.g. {'patients': ['name', 'dob'], 'users': ['email', 'password']}."
        ),
    )
    # Container image fields (populated by the Dockerfile adapter)
    image_name: str | None = Field(default=None, description="Container image name, e.g. 'python'")
    image_tag: str | None = Field(default=None, description="Image tag, e.g. '3.12-slim'")
    image_digest: str | None = Field(default=None, description="Image digest, e.g. 'sha256:abc…'")
    registry: str | None = Field(
        default=None, description="Registry host, e.g. 'docker.io', 'gcr.io'"
    )
    base_image: str | None = Field(
        default=None, description="Full base image reference, e.g. 'python:3.12-slim'"
    )
    # IaC security / resilience fields (populated by IaC adapters)
    cloud_region: str | None = Field(
        default=None,
        description="Cloud region, e.g. 'us-east-1', 'eastus', 'us-central1'",
    )
    availability_zones: list[str] | None = Field(
        default=None,
        description="Availability zones configured, e.g. ['us-east-1a', 'us-east-1b']",
    )
    secret_store: str | None = Field(
        default=None,
        description=(
            "Secret management service in use, e.g. 'aws_secrets_manager', "
            "'azure_key_vault', 'gcp_secret_manager', 'hashicorp_vault', 'k8s_secret'"
        ),
    )
    encryption_at_rest: bool | None = Field(
        default=None,
        description="True when encryption-at-rest is explicitly configured in IaC",
    )
    encryption_key_ref: str | None = Field(
        default=None,
        description="KMS key ARN, Key Vault URI, or CMEK resource reference",
    )
    runs_as_root: bool | None = Field(
        default=None,
        description="True when the container is configured to run as root (UID 0); False = non-root",
    )
    has_health_check: bool | None = Field(
        default=None,
        description="True when a HEALTHCHECK instruction (Dockerfile) or liveness/readiness probe (K8s) is present",
    )
    has_resource_limits: bool | None = Field(
        default=None,
        description="True when Kubernetes resource limits are defined for the workload container",
    )
    ha_mode: str | None = Field(
        default=None,
        description="High-availability topology: 'multi-az', 'replicated', or 'single'",
    )
    # IAM / identity fields (populated by IaC adapters for IAM nodes)
    iam_type: str | None = Field(
        default=None,
        description="IAM entity kind: 'role', 'policy', 'service_account', 'managed_identity', 'role_binding'",
    )
    principal: str | None = Field(
        default=None,
        description="ARN, email, or object ID of the IAM principal",
    )
    permissions: list[str] | None = Field(
        default=None,
        description="Actions or scopes granted by this IAM entity (up to 20 entries)",
    )
    iam_scope: str | None = Field(
        default=None,
        description="Scope of the IAM binding: 'project', 'subscription', 'cluster', 'namespace', 'resource'",
    )
    trust_principals: list[str] | None = Field(
        default=None,
        description="Principals trusted to assume this role (AWS trust policy subjects, K8s binding subjects)",
    )
    # Redteam test generation fields
    description: str | None = Field(
        default=None,
        description=(
            "Human-readable description of this component. "
            "For TOOL nodes: extracted from function docstring; used by the redteam "
            "test generator to craft context-authentic parameter injection payloads. "
            "For AGENT nodes: extracted from the agent's purpose/role description."
        ),
    )
    parameters: list[ToolParameter] | None = Field(
        default=None,
        description=(
            "For TOOL nodes: list of parameters accepted by this tool, extracted from "
            "the function signature and docstring. Used by TOOL_ABUSE test scenarios "
            "to craft parameter-injection payloads targeting each parameter."
        ),
    )
    injection_risk_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "For AGENT nodes: pre-computed injection risk score in [0, 1] set by the "
            "graph enricher. Derived from: number of tools with no_auth_required or "
            "high_privilege, presence of unguarded paths, reachable PII datastores, "
            "and unguarded HITL triggers. Used by ScenarioGenerator to sort generated "
            "test scenarios by expected impact."
        ),
    )
    # MCP tool identification (populated by MCP adapters)
    mcp_server_url: str | None = Field(
        default=None,
        description="URL of the MCP server this tool belongs to, e.g. 'http://mcp.example.com'",
    )
    trust_level: str | None = Field(
        default=None,
        description=(
            "MCP server trust classification set by the graph enricher: "
            "'trusted' | 'untrusted' | 'n/a'. "
            "All external MCP servers are 'untrusted' unless listed in "
            "redteam.mcp_trusted_servers in nuguard.yaml."
        ),
    )
    # Tool risk attributes (set by the graph enricher from SBOM metadata)
    no_auth_required: bool | None = Field(
        default=None,
        description="True when the tool or endpoint is invocable without authentication",
    )
    high_privilege: bool | None = Field(
        default=None,
        description="True when the tool has access to administrative or cross-tenant resources",
    )
    sql_injectable: bool | None = Field(
        default=None,
        description="True when the tool constructs SQL queries from agent-provided string parameters",
    )
    ssrf_possible: bool | None = Field(
        default=None,
        description="True when the tool accepts URL parameters that are fetched server-side (SSRF surface)",
    )
    accepts_external_url: bool | None = Field(
        default=None,
        description="True when the tool has a URL/endpoint parameter that is passed to an outbound request",
    )
    reads_external_content: bool | None = Field(
        default=None,
        description="True when the tool fetches or reads content from an external source (web, email, GitHub, etc.)",
    )
    # AGENT node attributes (populated by agent adapters)
    system_prompt_excerpt: str | None = Field(
        default=None,
        description=(
            "First 500 characters of the agent's system prompt / instructions / backstory. "
            "Used by the red-team scenario generator to craft context-authentic payloads."
        ),
    )
    # GUARDRAIL node attributes (populated by guardrail adapters / enricher)
    rules_excerpt: str | None = Field(
        default=None,
        description="Short description of the guardrail's rules, validator class, or blocking logic",
    )
    blocked_topics: list[str] | None = Field(
        default=None,
        description="Topics this guardrail is known to block (extracted by enricher or adapter)",
    )
    blocked_actions: list[str] | None = Field(
        default=None,
        description="Actions this guardrail is known to block (extracted by enricher or adapter)",
    )
    refusal_style: str | None = Field(
        default=None,
        description="How this guardrail normally refuses: 'hard_block', 'redirect', 'warn', etc.",
    )
    # API_ENDPOINT node attributes (populated by HTTP/API adapters)
    auth_required: bool | None = Field(
        default=None,
        description="True when this API endpoint requires authentication",
    )
    auth_scope: str | None = Field(
        default=None,
        description="OAuth2 scope or role required to call this endpoint: 'user' | 'admin' | 'none'",
    )
    accepts_user_input: bool | None = Field(
        default=None,
        description="True when this endpoint accepts user-controlled input in body or query params",
    )
    returns_sensitive_data: bool | None = Field(
        default=None,
        description="True when this endpoint returns PII, PHI, or other sensitive data",
    )
    rate_limited: bool | None = Field(
        default=None,
        description="True when rate limiting is configured for this endpoint",
    )
    idor_surface: bool | None = Field(
        default=None,
        description=(
            "True when this endpoint has user- or tenant-scoped path parameters "
            "such as {user_id} or {tenant_id}"
        ),
    )
    path_params: list[str] | None = Field(
        default=None,
        description=(
            "Path parameter names extracted from the endpoint URL template, "
            "e.g. ['user_id', 'tenant_id']"
        ),
    )
    path_param_sources: dict[str, str] | None = Field(
        default=None,
        description=(
            "Maps each entry in path_params to the API_ENDPOINT path that "
            "creates the identified resource, e.g. "
            "{'id': '/chat/conversations'} for a chat endpoint at "
            "'/chat/conversations/:id/messages'"
        ),
    )
    # Discovered request/response schema (populated by framework adapters)
    request_body_schema: dict[str, str] = Field(
        default_factory=dict,
        description="Pydantic/dataclass field map for the request body: {field_name: type_string}",
    )
    chat_payload_key: str | None = Field(
        default=None,
        description="Inferred primary prompt field in the request body (e.g. 'message', 'query')",
    )
    chat_payload_list: bool = Field(
        default=False,
        description="True when the chat payload key is typed as a list (e.g. list[str])",
    )
    response_text_key: str | None = Field(
        default=None,
        description="Inferred primary response text field in the response body",
    )
    context_payload_fields: dict[str, str] | None = Field(
        default=None,
        description=(
            "Non-chat context fields detected in the POST request body schema, "
            "mapping field_name → kind. "
            "kind='identity': static per user (user_id, tenant_id, account_id…) — "
            "auto-injected from login response or requires explicit config. "
            "kind='session': dynamic per conversation (session_id, thread_id…) — "
            "auto-generated as UUID for the first request per scenario."
        ),
    )
    # Datastore risk attributes (complement classified_fields for flat redteam lookups)
    pii_fields: list[str] | None = Field(
        default=None,
        description=(
            "Flat list of general PII field names in this datastore, "
            "e.g. ['name', 'email', 'phone', 'address', 'date_of_birth']. "
            "Derived from classified_fields by the graph enricher. "
            "Financial identifiers (card numbers, bank accounts) belong in pfi_fields."
        ),
    )
    phi_fields: list[str] | None = Field(
        default=None,
        description=(
            "Flat list of PHI field names in this datastore (HIPAA-regulated), "
            "e.g. ['diagnosis', 'medication', 'lab_result', 'patient_id']. "
            "Derived from classified_fields by the graph enricher."
        ),
    )
    pfi_fields: list[str] | None = Field(
        default=None,
        description=(
            "Flat list of Personal Financial Information field names (PCI-DSS / GLBA), "
            "e.g. ['card_number', 'bank_account', 'routing_number', 'cvv', 'ssn', "
            "'tax_id', 'account_balance', 'iban', 'swift_code']. "
            "Derived from classified_fields by the graph enricher."
        ),
    )
    access_type: str | None = Field(
        default=None,
        description="Datastore access mode inferred from ACCESSES edge: 'read' | 'write' | 'readwrite'",
    )
    # ── SBOM 1.5.0 enrichment fields ─────────────────────────────────────────
    descriptive_name: str | None = Field(
        default=None,
        description="LLM-generated human-readable label, e.g. 'User Authentication API'",
    )
    security_headers_detail: SecurityHeaderDetail | None = Field(
        default=None,
        description="HTTP security-header posture extracted from code or IaC",
    )
    cors_policy: CorsPolicyDetail | None = Field(
        default=None,
        description="CORS configuration extracted from code or IaC",
    )
    debug_error_leak: bool | None = Field(
        default=None,
        description="True when the app runs in a debug/verbose-error mode that leaks stack traces",
    )
    rate_limit_detail: RateLimitDetail | None = Field(
        default=None,
        description="Structured rate limit configuration extracted from code or IaC",
    )
    auth_detail: AuthDetail | None = Field(
        default=None,
        description="Detailed authentication and authorization posture",
    )
    encryption_detail: EncryptionDetail | None = Field(
        default=None,
        description="Encryption and redaction posture; supersedes encryption_at_rest for new consumers",
    )
    data_handling: DataHandlingDetail | None = Field(
        default=None,
        description="Data retention, backup, anonymization, and consent configuration",
    )
    dependency_names: list[str] | None = Field(
        default=None,
        description="Package names from the manifest that appear in this component's source files",
    )
    instrumentation: InstrumentationDetail | None = Field(
        default=None,
        description="Observability and monitoring tooling detected for this component",
    )
    testing: TestingDetail | None = Field(
        default=None,
        description="Test coverage and CI/CD posture for this component",
    )
    loc: int | None = Field(
        default=None,
        description="Total lines of source code across the files where this component is defined",
    )
    request_schema: dict[str, Any] | None = Field(
        default=None,
        description="Request body JSON schema for API endpoints, keyed by Pydantic model class name",
    )
    response_schema: dict[str, Any] | None = Field(
        default=None,
        description="Response body JSON schema for API endpoints, keyed by Pydantic model class name",
    )
    has_network_policy: bool | None = Field(
        default=None,
        description="K8s workload: True when covered by a NetworkPolicy resource in the same namespace",
    )
    source_url: str | None = Field(
        default=None,
        description="Model artifact origin URL (HuggingFace repo, local path, or API base endpoint)",
    )
    integrity_hash: str | None = Field(
        default=None,
        description="Model artifact SHA-256 or git revision hash for supply-chain verification",
    )
    checksum: str | None = Field(
        default=None,
        description="Model artifact checksum (md5, sha1, sha256) as a hex string",
    )
    # ── DEVELOPER_TOOL_CONFIG node fields ────────────────────────────────────
    tool_config_type: str | None = Field(
        default=None,
        description=(
            "AI-agent or editor config sub-type, e.g. 'claude-settings', 'mcp-config', "
            "'cursor-config', 'vscode-config', 'gemini-config', 'smithery-config'"
        ),
    )
    permissions_granted: list[str] | None = Field(
        default=None,
        description="Permission patterns granted in this config, e.g. ['Bash(*:*)', 'Read', 'Write']",
    )
    permissions_denied: list[str] | None = Field(
        default=None,
        description="Permission patterns denied in this config, e.g. ['Bash(rm:*)']",
    )
    auto_execute: bool | None = Field(
        default=None,
        description="True when auto-run or auto-approve mode is enabled in this config",
    )
    permission_scope: str | None = Field(
        default=None,
        description="Scope at which this config grants permissions: 'repo' | 'user' | 'global'",
    )
    file_size_bytes: int | None = Field(
        default=None,
        description="Raw file size in bytes of this config file (for SC-017: large file detection)",
    )
    content_entropy: float | None = Field(
        default=None,
        description=(
            "Shannon entropy (bits/byte) of the file content computed at extraction time "
            "(for SC-018: high-entropy blob detection). Typical text is ~4–5; encrypted/base64 is >6.5."
        ),
    )
    # ── LIFECYCLE_SCRIPT node fields ──────────────────────────────────────────
    script_phase: str | None = Field(
        default=None,
        description=(
            "Package lifecycle phase this script runs at, e.g. 'postinstall', 'preinstall', "
            "'prepare', 'build-backend', 'publish-hook'"
        ),
    )
    script_body: str | None = Field(
        default=None,
        description="Lifecycle script body, truncated to 2000 chars",
    )
    invokes_network: bool | None = Field(
        default=None,
        description="True when the script contains network download commands (curl, wget, fetch, etc.)",
    )
    invokes_shell: bool | None = Field(
        default=None,
        description="True when the script pipes into a shell (| bash, | sh, | node, etc.)",
    )
    downloads_binary: bool | None = Field(
        default=None,
        description="True when the script downloads and executes a binary (Bun, node, etc.)",
    )
    references_credentials: bool | None = Field(
        default=None,
        description=(
            "True when the script references credential paths or env vars: "
            "/proc/*/environ, .npmrc, AWS_SECRET_ACCESS_KEY, GITHUB_TOKEN, etc."
        ),
    )
    # ── GITHUB_WORKFLOW additional boolean flags (computed from step bodies) ──
    workflow_has_unpinned_global_install: bool | None = Field(
        default=None,
        description="True when any step in this workflow runs an unpinned global npm install or npx",
    )
    workflow_has_cred_access: bool | None = Field(
        default=None,
        description="True when any step in this workflow accesses credential paths or env vars",
    )
    # ── GITHUB_WORKFLOW node fields ───────────────────────────────────────────
    workflow_triggers: list[str] | None = Field(
        default=None,
        description="GitHub Actions trigger event names parsed from the 'on:' block",
    )
    workflow_permissions: dict[str, str] | None = Field(
        default=None,
        description="Top-level permissions block, e.g. {'id-token': 'write', 'contents': 'read'}",
    )
    publishes_to: list[str] | None = Field(
        default=None,
        description="Publish targets detected in workflow steps, e.g. ['pypi', 'npm', 'smithery']",
    )
    uses_oidc: bool | None = Field(
        default=None,
        description="True when any job has permissions.id-token: write (OIDC publishing)",
    )
    action_refs: list[str] | None = Field(
        default=None,
        description=(
            "All 'uses: owner/action@ref' strings found in this workflow. "
            "Unpinned refs (branch or tag, not full SHA) indicate a supply-chain risk."
        ),
    )
    # ── MCP_SERVER node fields ────────────────────────────────────────────────
    mcp_server_trusted: bool | None = Field(
        default=None,
        description="True when this MCP server is explicitly listed as trusted in nuguard.yaml",
    )
    mcp_transport: str | None = Field(
        default=None,
        description="MCP transport protocol declared in .mcp.json: 'stdio' | 'http' | 'sse'",
    )
    extras: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-specific key/value pairs (provider, model_family, version, …)",
    )


class Node(BaseModel):
    """A detected AI component (agent, model, tool, prompt, datastore, etc.)."""

    id: UUID = Field(default_factory=uuid4, description="Stable UUID for edge references")
    name: str = Field(description="Display name of the component")
    component_type: ComponentType
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Extraction confidence [0, 1]",
    )
    metadata: NodeMetadata = Field(default_factory=NodeMetadata)
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="Detection evidence supporting this node",
    )


class Edge(BaseModel):
    """A directed relationship between two Nodes."""

    source: UUID = Field(description="ID of the source Node")
    target: UUID = Field(description="ID of the target Node")
    relationship_type: RelationshipType
    access_type: AccessType | None = Field(
        default=None, description="Access direction on ACCESSES edges: read | write | readwrite"
    )
    derivation: Literal["hint", "fallback_heuristic"] = Field(
        default="hint",
        description=(
            "'hint': backed by an adapter-emitted RelationshipHint (explicit "
            "code evidence). 'fallback_heuristic': synthesized by structural "
            "fallback rules with no direct evidence."
        ),
    )
    confidence: float | None = Field(
        default=None,
        description=(
            "Set only for fallback_heuristic edges — a rough strength score "
            "for the guess. None for hint edges (evidence-backed, no "
            "separate score needed)."
        ),
    )


class ScanSummary(BaseModel):
    """Deterministic scan-level summary populated on every extraction."""

    use_case: str = Field(
        default="",
        description="Human-readable description of the application's AI use cases",
    )
    frameworks: list[str] = Field(
        default_factory=list,
        description="Agentic framework names detected (e.g. ['langgraph', 'crewai'])",
    )
    modalities: list[str] = Field(
        default_factory=list,
        description="Supported I/O modalities in upper-case (e.g. ['TEXT', 'VOICE'])",
    )
    modality_support: dict[str, bool] = Field(
        default_factory=dict,
        description="Detailed modality flags, e.g. {'text': true, 'voice': false}",
    )
    api_endpoints: list[str] = Field(
        default_factory=list,
        description="API route paths extracted from source (e.g. ['/chat', '/health'])",
    )
    deployment_platforms: list[str] = Field(
        default_factory=list,
        description="Cloud/CI platforms inferred from IaC files (e.g. ['AWS', 'GCP'])",
    )
    regions: list[str] = Field(
        default_factory=list,
        description="Cloud regions referenced in IaC/config (e.g. ['us-east-1'])",
    )
    environments: list[str] = Field(
        default_factory=list,
        description="Deployment environments inferred from config (e.g. ['prod', 'staging'])",
    )
    deployment_urls: list[str] = Field(
        default_factory=list,
        description="Canonical deployment URLs found in IaC/workflow files",
    )
    iac_accounts: list[str] = Field(
        default_factory=list,
        description="Cloud account IDs / subscription IDs / project IDs found in IaC",
    )
    node_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Count of nodes per ComponentType, e.g. {'AGENT': 3, 'MODEL': 2}",
    )
    # Security & resilience aggregate fields (populated from IaC/Dockerfile adapter output)
    secret_stores: list[str] = Field(
        default_factory=list,
        description="Deduped secret management services referenced across all IaC files",
    )
    availability_zones: list[str] = Field(
        default_factory=list,
        description="All cloud availability zones referenced in IaC files",
    )
    encryption_at_rest_coverage: bool = Field(
        default=False,
        description="True when at least one IaC resource has encryption-at-rest configured",
    )
    security_findings: list[str] = Field(
        default_factory=list,
        description=(
            "Notable security / resilience findings across IaC and container config, e.g. "
            "['container_runs_as_root', 'missing_health_check', 'no_resource_limits', "
            "'secrets_in_env_vars', 'overly_permissive_iam']"
        ),
    )
    iam_principals: list[str] = Field(
        default_factory=list,
        description="IAM role ARNs, GCP service account emails, and Azure managed identity names",
    )
    service_accounts: list[str] = Field(
        default_factory=list,
        description="K8s ServiceAccount names and GCP/Azure service account identifiers",
    )
    iac_security_summary: str | None = Field(
        default=None,
        description=(
            "LLM-generated security briefing for practitioners covering deployment posture, "
            "IAM configuration, secret management, encryption, HA, and CI/CD risks across "
            "all detected IaC and GitHub Actions workflows."
        ),
    )
    data_classification: list[str] = Field(
        default_factory=list,
        description=(
            "Union of all data classification labels detected across the repository, "
            "e.g. ['PHI', 'PII'].  Empty list when no classified fields are found."
        ),
    )
    classified_tables: list[str] = Field(
        default_factory=list,
        description=(
            "Names of SQL tables or Python models that contain classified data fields "
            "(PII or PHI).  Sorted alphabetically."
        ),
    )
    # App-launch discovery fields (populated by app_env_detector)
    startup_commands: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Startup commands discovered from package.json, Makefile, Procfile, "
            "pyproject.toml, or inferred entry points.  Each entry is "
            "{command, source, label} where label is 'dev' or 'start'."
        ),
    )
    env_files: list[str] = Field(
        default_factory=list,
        description="Relative paths to .env / dotenv files found in the repository.",
    )
    env_var_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Sorted list of environment variable *keys* found across all dotenv files. "
            "Values are intentionally omitted from the SBOM."
        ),
    )
    local_url: str | None = Field(
        default=None,
        description=(
            "Inferred local dev URL (e.g. 'http://localhost:8000') derived from "
            "PORT env var or startup command hints.  None when not determinable."
        ),
    )
    staging_urls: list[str] = Field(
        default_factory=list,
        description="Staging / QA deployment URLs discovered from env files or config.",
    )
    production_urls: list[str] = Field(
        default_factory=list,
        description="Production deployment URLs discovered from env files or config.",
    )
    log_paths: list[str] = Field(
        default_factory=list,
        description="Log file paths discovered during scanning (relative to app root).",
    )
    # Streaming output detection (populated by SBOM extractor)
    uses_streaming: bool = Field(
        default=False,
        description=(
            "True when the application exposes one or more streaming output endpoints "
            "(SSE, StreamingResponse, or framework-native streaming such as Google ADK "
            "/run_sse).  The behavior step reads this to enable streaming-aware turn "
            "execution instead of buffered HTTP requests."
        ),
    )
    streaming_endpoints: list[str] = Field(
        default_factory=list,
        description=(
            "API endpoint paths confirmed to serve streaming output "
            "(e.g. ['/run_sse', '/chat/stream']).  Populated from FastAPI "
            "StreamingResponse return types, SSE route patterns, ADK /run_sse, "
            "and similar source-code evidence."
        ),
    )
    # ── SBOM 1.5.0 summary fields ─────────────────────────────────────────────
    total_loc: int | None = Field(
        default=None,
        description="Total lines of source code scanned across all files in the repository",
    )
    tokens_used_for_enrichment: int | None = Field(
        default=None,
        description="Total LLM tokens consumed (prompt + completion) during SBOM enrichment",
    )
    input_tokens_used: int | None = Field(
        default=None,
        description="LLM prompt tokens consumed during SBOM enrichment",
    )
    output_tokens_used: int | None = Field(
        default=None,
        description="LLM completion tokens consumed during SBOM enrichment",
    )
    llm_model_used: str | None = Field(
        default=None,
        description="LLM model string used for SBOM enrichment, e.g. 'gemini/gemini-2.0-flash'",
    )
    instrumentation: InstrumentationDetail | None = Field(
        default=None,
        description="App-wide observability and monitoring tooling summary",
    )
    testing: TestingDetail | None = Field(
        default=None,
        description="App-wide testing and CI/CD posture summary",
    )
    github_actions_content: str = Field(
        default="",
        description=(
            "Raw concatenation of all detected GitHub Actions workflow YAML files, "
            "separated by '\n---\n'. Used by NGA-010/011/014 regex rules as a fallback "
            "when structured workflow_security_findings are not available."
        ),
    )
    workflow_security_findings: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Structured security findings emitted by GitHubActionsAdapter during SBOM extraction. "
            "Each dict has keys: {rule_signal, path, line, snippet, confidence}."
        ),
    )
    k8s_network_policy_namespaces: list[str] = Field(
        default_factory=list,
        description=(
            "Kubernetes namespaces that have at least one NetworkPolicy resource detected. "
            "Populated by K8sAdapter. Used by NGA-013 to assert cross-file coverage."
        ),
    )
    # ── Supply-chain summary fields ───────────────────────────────────────────
    has_package_json: bool | None = Field(
        default=None,
        description=(
            "True when a package.json file was found at the repo root during SBOM extraction. "
            "Used by supply-chain scanner to check lockfile coverage (SC-024) without "
            "requiring a live filesystem."
        ),
    )
    has_lockfile: bool | None = Field(
        default=None,
        description=(
            "True when at least one npm lockfile (package-lock.json, pnpm-lock.yaml, or "
            "yarn.lock) was found at the repo root during SBOM extraction. "
            "Used by supply-chain scanner (SC-024) without requiring a live filesystem."
        ),
    )
    minified_js_files: list[str] = Field(
        default_factory=list,
        description=(
            "Relative paths to JavaScript files that contain a single line exceeding 5000 "
            "characters — a reliable indicator of minified/bundled code. "
            "Populated by the extractor during the main JS scanning pass. "
            "Used by supply-chain scanner (SC-019) without requiring a live filesystem."
        ),
    )


class AiSbomDocument(BaseModel):
    """AI Bill of Materials document produced by NuGuard.

    This is the canonical output format.  Use ``AiSbomSerializer.to_json()``
    to serialise and ``AiSbomDocument.model_validate()`` to parse and validate.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://nuguard.ai/schemas/aibom/1.5.0/aibom.schema.json",
        }
    )

    schema_version: str = Field(
        default="1.5.0",
        description="AIBOM schema version (semver); bump when format changes",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0),
        description="ISO 8601 UTC timestamp when this document was generated",
    )
    generator: str = Field(
        default="nuguard",
        description="Tool that produced this document",
    )
    target: str = Field(description="Repository URL or local path that was scanned")
    nodes: list[Node] = Field(
        default_factory=list,
        description="Detected AI components",
    )
    edges: list[Edge] = Field(
        default_factory=list,
        description="Directed relationships between components",
    )
    deps: list[PackageDep] = Field(
        default_factory=list,
        description=(
            "Package dependencies from manifests "
            "(pyproject.toml, requirements*.txt, package.json, …)"
        ),
    )
    summary: ScanSummary | None = Field(
        default=None,
        description=(
            "Scan-level metadata: use-case summary, frameworks, modalities, "
            "API endpoints, and IaC/deployment context"
        ),
    )
    relationship_graph_md: str | None = Field(
        default=None,
        description=(
            "Markdown section containing a Mermaid flowchart of key component "
            "relationships (AGENT → TOOL → DATASTORE, guardrail coverage, etc.) "
            "plus an LLM-written plain-English narrative. "
            "Only populated when enable_llm=True during SBOM generation."
        ),
    )
