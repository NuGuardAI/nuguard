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


class RelationshipType(str, Enum):
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
