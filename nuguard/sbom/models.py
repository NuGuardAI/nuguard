"""Xelo data models.

All public types are Pydantic ``BaseModel`` subclasses.  The JSON schema
exported by the CLI (``xelo schema``) is generated directly from these models
so schema and code can never drift.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .deps import PackageDep
from .types import ComponentType, RelationshipType


class SourceLocation(BaseModel):
    """File/line pointer for a piece of evidence."""

    path: str = Field(description="Relative path to the source file")
    line: int | None = Field(default=None, description="1-based line number, if known")


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


class AiSbomDocument(BaseModel):
    """AI Bill of Materials document produced by Xelo.

    This is the canonical output format.  Use ``AiSbomSerializer.to_json()``
    to serialise and ``AiSbomDocument.model_validate()`` to parse and validate.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://nuguard.ai/schemas/aibom/1.3.0/aibom.schema.json",
        }
    )

    schema_version: str = Field(
        default="1.3.0",
        description="AIBOM schema version (semver); bump when format changes",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0),
        description="ISO 8601 UTC timestamp when this document was generated",
    )
    generator: str = Field(
        default="xelo",
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
