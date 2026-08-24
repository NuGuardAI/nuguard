"""Data-exposure validation for direct-HTTP API_ATTACK responses.

An HTTP 2xx status code alone does not prove an endpoint leaked data — an
empty body, a generic placeholder, or a 200-wrapped error all satisfy that
check just as well as a response carrying another user's PII. This module
inspects the actual response body for evidence that real, structured,
sensitive data came back: PII-shaped values, a bulk record list, or fields
matching the endpoint's SBOM-declared sensitive-data schema.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from nuguard.common.pii_patterns import find_pii_values


@dataclass
class DataExposureResult:
    """Result of scanning one API response body for evidence of data exposure."""

    exposed: bool
    record_count: int
    pii_values: list[str] = field(default_factory=list)
    matched_sensitive_fields: list[str] = field(default_factory=list)
    evidence: str = ""


def _count_records(data: object) -> int:
    """Best-effort count of "records" in a parsed JSON body.

    A bare list is treated as *the* record set. A dict is scanned one level
    deep for a list-of-objects value (the common ``{"items": [...]}`` /
    ``{"results": [...]}`` envelope shape); anything else counts as a single
    record.
    """
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return len(value)
        return 1
    return 0


def check_response_for_data_exposure(
    response_text: str,
    sensitive_fields: list[str] | None = None,
) -> DataExposureResult:
    """Inspect a direct-HTTP response body for evidence of actual data exposure.

    *sensitive_fields* is the flat list of field names known — from SBOM
    ``pii_fields``/``phi_fields``/``pfi_fields`` metadata on the endpoint's
    backing datastore — to carry sensitive data, used to check whether those
    exact field names appear in the response body.

    A record count on its own (a single-object response) is not treated as
    exposure — that's indistinguishable from a normal single-resource GET.
    Exposure requires either recognizable PII-shaped values, a match against
    known sensitive field names, or a bulk (>1) record list, which is itself
    an anomaly for endpoints not designed to return collections.
    """
    pii_values = find_pii_values(response_text)

    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        data = None

    record_count = _count_records(data) if data is not None else 0
    matched_fields: list[str] = []
    if data is not None and sensitive_fields:
        body_lower = response_text.lower()
        matched_fields = [f for f in sensitive_fields if f.lower() in body_lower]

    exposed = bool(pii_values) or bool(matched_fields) or record_count > 1

    evidence_parts: list[str] = []
    if pii_values:
        evidence_parts.append(f"PII-shaped values: {pii_values[:3]}")
    if matched_fields:
        evidence_parts.append(f"sensitive fields present: {matched_fields[:5]}")
    if record_count > 1:
        evidence_parts.append(f"bulk record list (count={record_count})")

    return DataExposureResult(
        exposed=exposed,
        record_count=record_count,
        pii_values=pii_values,
        matched_sensitive_fields=matched_fields,
        evidence="; ".join(evidence_parts),
    )
