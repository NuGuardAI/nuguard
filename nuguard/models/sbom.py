"""Pydantic models for the Xelo AI-SBOM v1.3.0 document format.

These models are the canonical in-memory representation used by all nuguard
capabilities.  They are intentionally kept close to the JSON wire format so
round-tripping is lossless.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    """All component types recognised by the Xelo AI-SBOM v1.3.0 schema."""

    AGENT = "AGENT"
    MODEL = "MODEL"
    TOOL = "TOOL"
    PROMPT = "PROMPT"
    DATASTORE = "DATASTORE"
    GUARDRAIL = "GUARDRAIL"
    AUTH = "AUTH"
    PRIVILEGE = "PRIVILEGE"
    CONTAINER_IMAGE = "CONTAINER_IMAGE"
    DEPLOYMENT = "DEPLOYMENT"
    IAM = "IAM"
    FRAMEWORK = "FRAMEWORK"
    API_ENDPOINT = "API_ENDPOINT"


class EdgeRelationshipType(str, Enum):
    """Relationship types for edges in the AI-SBOM graph."""

    USES = "USES"
    CALLS = "CALLS"
    ACCESSES = "ACCESSES"
    PROTECTS = "PROTECTS"
    DEPLOYS = "DEPLOYS"


class AccessType(str, Enum):
    """Access direction on ``ACCESSES`` edges."""

    READ = "read"
    WRITE = "write"
    READWRITE = "readwrite"


class DatastoreType(str, Enum):
    """Sub-type of a DATASTORE node."""

    VECTOR = "vector"
    RELATIONAL = "relational"
    KV = "kv"
    KNOWLEDGE_BASE = "knowledge_base"


class PrivilegeScope(str, Enum):
    """Capability grant represented by a PRIVILEGE node."""

    DB_WRITE = "db_write"
    FILESYSTEM_WRITE = "filesystem_write"
    CODE_EXECUTION = "code_execution"
    NETWORK_OUT = "network_out"
    EMAIL_OUT = "email_out"
    SOCIAL_MEDIA_OUT = "social_media_out"
    ADMIN = "admin"
    RBAC = "rbac"


class DataClassification(str, Enum):
    """Sensitivity classification for data stored in a DATASTORE."""

    PII = "PII"
    PHI = "PHI"
    INTERNAL = "INTERNAL"
    PUBLIC = "PUBLIC"


class EvidenceKind(str, Enum):
    """How a piece of evidence was collected."""

    AST = "ast"
    AST_INSTANTIATION = "ast_instantiation"
    REGEX = "regex"
    CONFIG = "config"
    IAC = "iac"
    INFERRED = "inferred"


class EvidenceLocation(BaseModel):
    """File and optional line number for a piece of evidence."""

    path: str
    line: int | None = None


class Evidence(BaseModel):
    """A single piece of evidence supporting a node or edge claim."""

    kind: EvidenceKind
    confidence: float
    detail: str
    location: EvidenceLocation | None = None


class NodeMetadata(BaseModel):
    """Flexible metadata bag shared by all node types.

    Fields are optional at the model level; which ones are populated depends on
    the ``NodeType`` they accompany.
    """

    # DATASTORE
    datastore_type: DatastoreType | None = None
    data_classification: list[DataClassification] = []
    classified_tables: list[str] = []
    classified_fields: list[str] = []

    # AUTH / API_ENDPOINT
    auth_type: str | None = None
    auth_class: str | None = None
    transport: str | None = None
    endpoint: str | None = None
    method: str | None = None

    # PRIVILEGE
    privilege_scope: PrivilegeScope | None = None

    # MODEL
    model_name: str | None = None
    framework: str | None = None

    # CONTAINER_IMAGE
    image_name: str | None = None
    image_tag: str | None = None
    runs_as_root: bool | None = None
    has_health_check: bool | None = None
    has_resource_limits: bool | None = None

    # IAM
    iam_type: str | None = None
    principal: str | None = None
    permissions: list[str] = []

    # All nodes
    deployment_target: str | None = None
    extras: dict[str, Any] = {}


class Node(BaseModel):
    """A single node in the AI-SBOM graph."""

    id: str
    name: str
    component_type: NodeType
    confidence: float = 1.0
    metadata: NodeMetadata = Field(default_factory=NodeMetadata)
    evidence: list[Evidence] = []


class Edge(BaseModel):
    """A directed edge between two nodes in the AI-SBOM graph."""

    source: str
    target: str
    relationship_type: EdgeRelationshipType
    access_type: AccessType | None = None  # populated on ACCESSES edges only


class PackageDep(BaseModel):
    """A Python/JS/etc package dependency discovered during SBOM generation."""

    name: str
    version_spec: str | None = None
    purl: str | None = None
    source_file: str | None = None
    ecosystem: str | None = None


class ScanSummary(BaseModel):
    """High-level summary computed by the extractor pipeline."""

    use_case: str | None = None
    frameworks: list[str] = []
    modalities: list[str] = []
    node_counts: dict[str, int] = {}
    security_findings: list[str] = []
    data_classification: list[str] = []
    classified_tables: list[str] = []
    api_endpoints: list[str] = []
    deployment_platforms: list[str] = []
    iac_accounts: list[str] = []
    iac_security_summary: str | None = None


class AiSbomDocument(BaseModel):
    """Root document conforming to the Xelo AI-SBOM v1.3.0 schema."""

    schema_version: str = "1.3.0"
    generated_at: datetime
    generator: str = "nuguard"
    target: str
    nodes: list[Node] = []
    edges: list[Edge] = []
    deps: list[PackageDep] = []
    summary: ScanSummary = Field(default_factory=ScanSummary)
