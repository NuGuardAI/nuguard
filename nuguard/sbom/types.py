from enum import Enum


class ComponentType(str, Enum):
    AGENT = "AGENT"
    GUARDRAIL = "GUARDRAIL"
    FRAMEWORK = "FRAMEWORK"
    MODEL = "MODEL"
    TOOL = "TOOL"
    DATASTORE = "DATASTORE"
    AUTH = "AUTH"
    PRIVILEGE = "PRIVILEGE"
    API_ENDPOINT = "API_ENDPOINT"
    DEPLOYMENT = "DEPLOYMENT"
    PROMPT = "PROMPT"
    CONTAINER_IMAGE = "CONTAINER_IMAGE"
    IAM = "IAM"
    # Supply-chain node types
    DEVELOPER_TOOL_CONFIG = "DEVELOPER_TOOL_CONFIG"  # .claude/settings.json, .mcp.json, AGENTS.md
    LIFECYCLE_SCRIPT = "LIFECYCLE_SCRIPT"            # npm postinstall, pyproject build hooks
    GITHUB_WORKFLOW = "GITHUB_WORKFLOW"              # .github/workflows/*.yml
    MCP_SERVER = "MCP_SERVER"                        # MCP server entries in .mcp.json


class RelationshipType(str, Enum):
    USES = "USES"
    CALLS = "CALLS"
    ACCESSES = "ACCESSES"
    PROTECTS = "PROTECTS"
    DEPLOYS = "DEPLOYS"
    DELEGATES_TO = "DELEGATES_TO"
    CONTAINS = "CONTAINS"  # config CONTAINS permission/script; workflow CONTAINS action ref


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
