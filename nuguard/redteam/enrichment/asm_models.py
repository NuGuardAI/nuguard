"""Agentic Surface Model (ASM) — redteam-proposal.md W1.

The ASM is a *runtime* artifact (orchestrator run context + redteam report),
not persisted into the committed SBOM JSON — same treatment as
``chat_payload_extras``. Only a summarized subset
(:class:`nuguard.sbom.models.AsmSummary`) is promoted onto SBOM nodes so
static analysis and capability gating can reuse it generically.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

AsmEndpointSource = Literal["openapi", "heuristic_inventory", "sbom_ws", "cors_probe"]
AsmAuthClassification = Literal["open", "auth_enforced", "unknown"]


class AsmEndpoint(BaseModel):
    """One sibling endpoint discovered beyond the primary chat route."""

    url: str
    methods: list[str] = Field(default_factory=list)
    auth_classification: AsmAuthClassification = "unknown"
    source: AsmEndpointSource
    status_codes: dict[str, int] = Field(default_factory=dict)


class AsmObservationChannel(BaseModel):
    """A WS/SSE endpoint found alongside the primary chat transport."""

    url: str
    transport: Literal["ws", "sse"]
    connect_auth_required: bool
    node_id: str | None = None


class AsmCorsFinding(BaseModel):
    """Result of one OPTIONS + attacker-``Origin`` CORS reflection probe."""

    url: str
    origin_reflected: bool
    credentials_allowed: bool
    methods_allowed: list[str] = Field(default_factory=list)


class AgenticSurfaceModel(BaseModel):
    """Full live-probed surface for one scan — the W1 recon artifact.

    Never serialized into the committed SBOM; see module docstring.
    """

    target_base_urls: list[str] = Field(default_factory=list)
    endpoints: list[AsmEndpoint] = Field(default_factory=list)
    observation_channels: list[AsmObservationChannel] = Field(default_factory=list)
    cors_findings: list[AsmCorsFinding] = Field(default_factory=list)
    inventory_disclosures: list[dict] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    probe_budget_used: int = 0

    @property
    def has_sibling_surface(self) -> bool:
        return bool(self.endpoints)

    @property
    def has_unauthenticated_inventory_disclosure(self) -> bool:
        return bool(self.inventory_disclosures)

    @property
    def has_unauthenticated_observation_channel(self) -> bool:
        return any(not c.connect_auth_required for c in self.observation_channels)

    @property
    def has_cors_wildcard_with_credentials(self) -> bool:
        return any(
            f.origin_reflected and f.credentials_allowed for f in self.cors_findings
        )
