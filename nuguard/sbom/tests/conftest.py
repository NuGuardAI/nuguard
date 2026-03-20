"""Shared helpers for all Xelo tests.

Import these directly in test modules::

    from conftest import APPS, FIXTURES, PY_ONLY, extract, nodes, names, adapters
"""

from __future__ import annotations

from pathlib import Path

from xelo.config import AiSbomConfig
from xelo.extractor import AiSbomExtractor
from xelo.models import AiSbomDocument
from xelo.types import ComponentType

# ---------------------------------------------------------------------------
# Canonical fixture roots
# ---------------------------------------------------------------------------

#: Rich scenario fixtures — each subdirectory is one AI application
APPS: Path = Path(__file__).parent / "fixtures" / "apps"

#: Flat fixture root — simpler integration fixtures live here directly
FIXTURES: Path = Path(__file__).parent / "fixtures"

#: Default config: Python-only, deterministic
PY_ONLY: AiSbomConfig = AiSbomConfig(
    include_extensions={".py"},
    enable_llm=False,
)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def extract(path: Path, config: AiSbomConfig | None = None) -> AiSbomDocument:
    """Run AiSbomExtractor on *path* using *config* (default: PY_ONLY)."""
    return AiSbomExtractor().extract_from_path(path, config or PY_ONLY)


def nodes(doc: AiSbomDocument, typ: ComponentType) -> list:
    """Return all nodes in *doc* with the given component type."""
    return [n for n in doc.nodes if n.component_type == typ]


def names(doc: AiSbomDocument, ctype: ComponentType) -> set[str]:
    """Return lowercase node names filtered by component type."""
    return {n.name.lower() for n in doc.nodes if n.component_type == ctype}


def adapters(doc: AiSbomDocument) -> set[str]:
    """Return the set of adapter names present anywhere in *doc*."""
    return {n.metadata.extras.get("adapter", "") for n in doc.nodes}
