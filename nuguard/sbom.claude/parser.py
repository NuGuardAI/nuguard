"""Parse a Xelo / NuGuard AI-SBOM into an ``AiSbomDocument``.

Usage::

    from nuguard.sbom.parser import parse_sbom

    doc = parse_sbom(raw_json_string)
    doc = parse_sbom(raw_bytes)
    doc = parse_sbom(already_parsed_dict)
"""

from __future__ import annotations

import json
from typing import Any

from nuguard.common.errors import SbomError
from nuguard.models.sbom import AiSbomDocument


def parse_sbom(data: str | bytes | dict[str, Any]) -> AiSbomDocument:
    """Parse *data* into an :class:`~nuguard.models.sbom.AiSbomDocument`.

    Args:
        data: Raw JSON string, UTF-8 bytes, or an already-parsed dict.

    Returns:
        Parsed and validated :class:`~nuguard.models.sbom.AiSbomDocument`.

    Raises:
        :class:`~nuguard.common.errors.SbomError`: When JSON decoding fails or
            the document does not conform to the expected structure.
    """
    if isinstance(data, (str, bytes)):
        try:
            parsed: dict[str, Any] = json.loads(data)
        except json.JSONDecodeError as exc:
            raise SbomError(f"Failed to decode SBOM JSON: {exc}") from exc
    elif isinstance(data, dict):
        parsed = data
    else:
        raise SbomError(
            f"parse_sbom expects str, bytes, or dict — got {type(data).__name__}"
        )

    try:
        return AiSbomDocument.model_validate(parsed)
    except Exception as exc:
        raise SbomError(f"Failed to parse SBOM document: {exc}") from exc
