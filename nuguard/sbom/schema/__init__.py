"""Schema helpers for the bundled aibom.schema.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def get_schema_path() -> Path:
    """Return the path to the bundled aibom.schema.json."""
    # The actual file lives in nuguard/sbom/schemas/ (plural)
    return Path(__file__).parent.parent / "schemas" / "aibom.schema.json"


def get_schema() -> dict[str, Any]:
    """Load and return the bundled aibom JSON schema."""
    return json.loads(get_schema_path().read_text(encoding="utf-8"))
