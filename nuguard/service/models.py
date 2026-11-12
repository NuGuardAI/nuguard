"""Public request/response models of the remote scan service (``/v1``).

Every model is JSON-safe (``model_dump(mode="json")``) and registered in the
public API contract test. Structural validation lives here; checks that depend
on server configuration (allowed hosts, allowed tools, LLM availability) are
applied by the routes.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from nuguard.models.finding import Finding, FindingKind, FindingLocation, Severity
from nuguard.models.tool_run import ToolRun

ToolName = Literal[
    "nga-rules", "atlas", "semgrep", "osv", "grype", "checkov", "trivy", "supply-chain"
]

_REPO_PATH_RE = re.compile(
    r"^/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:/tree/(?P<rest>.+?))?/?$"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _check_relative_subpath(value: str) -> str:
    value = value.strip()
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or _CONTROL_RE.search(value)
        or ".." in path.parts
    ):
        raise ValueError("subpath must be a relative path without '..'")
    return str(path)


class ScanOptions(BaseModel):
    """What to run. The server may narrow these; it never widens them."""

    model_config = ConfigDict(extra="forbid")

    tools: list[ToolName] | None = Field(
        default=None, description="Subset of the server's allowed tools; null = all allowed"
    )
    min_severity: Severity = Severity.LOW
    include_sbom: bool = Field(default=False, description="Embed the full AI-SBOM in the result")
    include_posture: bool = Field(default=True, description="Include kind=posture findings")
    network: bool = Field(
        default=True, description="false: OSV/Grype/Trivy are reported skipped, never ok"
    )
    use_llm: bool = Field(default=False, description="Rejected unless the server enables LLM use")
    subpath: str | None = Field(default=None, description="Scan only this repo-relative directory")

    @field_validator("subpath")
    @classmethod
    def _subpath(cls, value: str | None) -> str | None:
        return None if value is None else _check_relative_subpath(value)

    @field_validator("tools")
    @classmethod
    def _dedupe_tools(cls, value: list[ToolName] | None) -> list[ToolName] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("tools must not be empty; omit it to run every allowed tool")
        return list(dict.fromkeys(value))


class ScanCreateRequest(BaseModel):
    """``POST /v1/scans`` body."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    repo_url: str = Field(
        max_length=2048,
        description="https://<host>/<owner>/<repo>[.git][/tree/<ref>/<subpath>]",
    )
    ref: str | None = Field(default=None, description="Branch, tag or full commit SHA")
    github_token: SecretStr | None = Field(
        default=None, description="Read-only token; held in memory only, never stored or returned"
    )
    options: ScanOptions = Field(default_factory=ScanOptions)
    client_reference: str | None = Field(default=None, max_length=200)
    callback_url: str | None = Field(default=None, max_length=2048)

    @field_validator("repo_url")
    @classmethod
    def _repo_url(cls, value: str) -> str:
        value = value.strip()
        parts = urlsplit(value)
        if parts.scheme != "https":
            raise ValueError("repo_url must use https://")
        if "@" in parts.netloc:
            raise ValueError("repo_url must not contain credentials")
        if parts.query or parts.fragment:
            raise ValueError("repo_url must not contain a query or fragment")
        if not parts.hostname or not _REPO_PATH_RE.match(parts.path):
            raise ValueError("repo_url path must be /<owner>/<repo>[/tree/<ref>/<subpath>]")
        return value

    @field_validator("ref")
    @classmethod
    def _ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from nuguard.sbom.extractor.git_safety import validate_ref  # noqa: PLC0415

        if len(value) > 255 or ".." in value or _CONTROL_RE.search(value):
            raise ValueError("invalid ref")
        try:
            validate_ref(value)
        except ValueError:
            raise ValueError("invalid ref") from None
        return value

    @field_validator("github_token")
    @classmethod
    def _token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value()
        if not 20 <= len(raw) <= 255 or not all(33 <= ord(c) <= 126 for c in raw):
            raise ValueError("github_token must be 20-255 printable ASCII characters")
        return value

    @field_validator("callback_url")
    @classmethod
    def _callback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
            raise ValueError("callback_url must be an https:// URL without credentials")
        return value


class ScanStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"  # finished, but a requested tool did not reach ok / not_applicable
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATUSES = frozenset(
    {ScanStatus.SUCCEEDED, ScanStatus.PARTIAL, ScanStatus.FAILED, ScanStatus.CANCELLED,
     ScanStatus.EXPIRED}
)


class ScanPhase(str, Enum):
    QUEUED = "queued"
    CLONING = "cloning"
    SBOM = "sbom"
    ANALYZING = "analyzing"
    FINALIZING = "finalizing"


class ProblemDetail(BaseModel):
    """RFC 9457 problem details; ``detail`` never carries stderr, paths or tokens."""

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None


class ScanJob(BaseModel):
    id: str = Field(description='"scn_" + 26-character ULID')
    status: ScanStatus
    phase: ScanPhase | None = None
    repo_url: str
    ref_requested: str | None = None
    commit_sha: str | None = None
    client_reference: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: ProblemDetail | None = None
    links: dict[str, str] = Field(default_factory=dict)


class ScanList(BaseModel):
    items: list[ScanJob]
    next_cursor: str | None = None


class ToolVersion(BaseModel):
    name: str
    version: str | None = None


class Provenance(BaseModel):
    nuguard_version: str
    service_version: str
    image_digest: str | None = None
    rulepack_sha256: str
    tools: list[ToolVersion] = Field(default_factory=list)
    options_effective: ScanOptions
    llm_used: bool = False


class Coverage(BaseModel):
    tool_runs: list[ToolRun] = Field(description="One per requested tool, always present")
    languages_detected: list[str] = Field(default_factory=list)
    files_scanned: int = 0
    files_skipped: int = 0
    gaps: list[str] = Field(default_factory=list, description='e.g. "osv: failed (timed out)"')


class ScanFinding(BaseModel):
    fingerprint: str = Field(description="nf1_… stable identity; see the fingerprint docs")
    rule_id: str
    source_tool: str
    kind: FindingKind
    severity: Severity
    title: str
    description: str
    locations: list[FindingLocation] = Field(default_factory=list)
    affected_component: str | None = None
    references: list[str] = Field(default_factory=list)
    owasp_llm_ref: str | None = None
    owasp_asi_ref: str | None = None
    mitre_atlas_technique: str | None = None
    remediation: str | None = None
    raw: Finding


class InventorySummary(BaseModel):
    node_counts: dict[str, int] = Field(default_factory=dict)
    edge_count: int = 0
    frameworks: list[str] = Field(default_factory=list)
    model_providers: list[str] = Field(default_factory=list)


class ScanResult(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    job: ScanJob
    provenance: Provenance
    coverage: Coverage
    summary: dict[Severity, int]
    findings: list[ScanFinding]
    inventory: InventorySummary
    sbom: dict | None = None
    result_sha256: str = Field(
        description=(
            "SHA-256 of the canonical JSON of this result with volatile fields removed "
            "(job id/timestamps/links/client_reference, tool durations, raw finding ids, sbom)"
        )
    )


class Capabilities(BaseModel):
    nuguard_version: str
    service_version: str
    tools: list[ToolVersion]
    tools_allowed: list[str]
    network_default: bool
    llm_available: bool
    allowed_git_hosts: list[str]
    limits: dict[str, float | int]


class HealthStatus(BaseModel):
    status: Literal["ok", "not_ready"]
    failing: list[str] = Field(default_factory=list)
