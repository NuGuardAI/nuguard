"""Bundled AI-SBOM JSON Schema."""

from __future__ import annotations

from pathlib import Path


def get_schema_path() -> Path:
    """Return the path to the bundled ``aibom.schema.json``."""
    return Path(__file__).parent / "aibom.schema.json"
