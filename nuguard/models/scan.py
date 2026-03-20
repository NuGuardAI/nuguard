"""Scan data models.

TODO: Implement Scan, TestConfig, ScanStatus, and ScanResult Pydantic models.
"""

from enum import Enum

from pydantic import BaseModel


class ScanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Scan(BaseModel):
    """Represents a single nuguard scan run."""

    scan_id: str
    status: ScanStatus = ScanStatus.PENDING
    sbom_id: str | None = None
    policy_id: str | None = None
