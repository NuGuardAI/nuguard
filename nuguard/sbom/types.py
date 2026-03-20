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
