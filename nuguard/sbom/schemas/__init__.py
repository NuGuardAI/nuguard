"""
AIBOM Schemas Package

Exposes the committed AiSbomDocument JSON schema as a Python dict and Path.
The schema file is the canonical serialised form of ``AiSbomDocument.model_json_schema()``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_PATH: Path = Path(__file__).parent / "aibom.schema.json"

SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

__all__ = ["SCHEMA", "SCHEMA_PATH"]
