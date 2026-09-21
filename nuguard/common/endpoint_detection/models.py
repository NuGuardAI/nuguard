"""Shared result types for endpoint and payload-shape detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EndpointSource(StrEnum):
    """Origin of a resolved endpoint or payload field."""

    CONFIG = "config"
    SBOM = "sbom"
    PROBE = "probe"
    BROWSER = "browser"
    FALLBACK = "fallback"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FieldResolution:
    """Value plus provenance and explicitness for one resolved field."""

    value: Any = None
    source: EndpointSource = EndpointSource.UNKNOWN
    explicit: bool = False


@dataclass(frozen=True)
class PayloadShape:
    """Payload details required to send a message to a resolved endpoint."""

    key: str | None = None
    is_list: bool = False
    value_template: dict[str, Any] | None = None
    response_key: str | None = None
    source: EndpointSource = EndpointSource.UNKNOWN
    explicit_key: bool = False
    explicit_list: bool = False
    explicit_template: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedEndpoint:
    """Complete endpoint resolution result consumed by future callers."""

    path: str | None = None
    payload: PayloadShape = field(default_factory=PayloadShape)
    path_source: EndpointSource = EndpointSource.UNKNOWN
    path_explicit: bool = False
    notes: tuple[str, ...] = ()

    @property
    def payload_key(self) -> str | None:
        """Return the message field for compatibility with existing callers."""
        return self.payload.key

    @property
    def payload_list(self) -> bool:
        """Return whether the message value must be wrapped in a list."""
        return self.payload.is_list

    @property
    def response_key(self) -> str | None:
        """Return the configured or inferred response field."""
        return self.payload.response_key
