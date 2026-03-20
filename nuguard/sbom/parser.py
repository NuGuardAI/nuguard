"""Parse an AI-SBOM JSON file into a structured model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import AiSbomDocument
from .serializer import AiSbomSerializer


def parse_sbom(data: dict[str, Any] | str | Path) -> AiSbomDocument:
    """Parse *data* (raw dict, JSON string, or file path) into an AiSbomDocument."""
    if isinstance(data, Path):
        raw = data.read_text(encoding="utf-8")
        return AiSbomSerializer.from_json(raw)
    if isinstance(data, str):
        return AiSbomSerializer.from_json(data)
    # dict
    return AiSbomSerializer.from_json(json.dumps(data))
