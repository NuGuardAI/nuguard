"""Finding data models.

TODO: Implement Finding, Severity, and PolicyViolationType Pydantic models.
"""

from enum import Enum

from pydantic import BaseModel


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Finding(BaseModel):
    """A security finding produced by static analysis or red-team testing."""

    finding_id: str
    title: str
    severity: Severity
    description: str
    affected_component: str | None = None
    remediation: str | None = None
    references: list[str] = []
