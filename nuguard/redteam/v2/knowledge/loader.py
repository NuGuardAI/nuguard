"""Loader for the bundled technique knowledge base.

Technique records ship as YAML under ``knowledge/data/techniques/`` and are
loaded via :mod:`importlib.resources` so they resolve correctly whether NuGuard
runs from source or an installed wheel.

The knowledge base is versioned independently from the runner via
:data:`KNOWLEDGE_BASE_VERSION`.  :func:`load_techniques` is cached, so callers
can call it freely; use :func:`load_techniques.cache_clear` in tests that mutate
bundled files.

YAML file shape (one or more per file)::

    techniques:
      - id: AIT-INDIRECT-INJECTION-001
        name: ...
        ...
"""
from __future__ import annotations

import importlib.resources as resources
from functools import lru_cache

import yaml

from nuguard.common.logging import get_logger
from nuguard.redteam.v2.knowledge.schema import TechniqueRecord

_log = get_logger(__name__)

#: Pinned knowledge-base version.  Bump on any record add/change/remove.
KNOWLEDGE_BASE_VERSION = "0.1.0"

_TECHNIQUES_ANCHOR = "nuguard.redteam.v2.knowledge"
_TECHNIQUES_SUBDIR = "data/techniques"

__all__ = [
    "KNOWLEDGE_BASE_VERSION",
    "load_techniques",
    "load_technique_index",
    "verify_builder_keys",
    "verify_scenario_ids",
]


@lru_cache(maxsize=1)
def load_techniques() -> tuple[TechniqueRecord, ...]:
    """Load and validate all bundled technique records, sorted by ``id``.

    Raises:
        ValueError: if a record fails validation or two records share an ``id``.
    """
    techniques_dir = resources.files(_TECHNIQUES_ANCHOR).joinpath(_TECHNIQUES_SUBDIR)
    records: list[TechniqueRecord] = []
    seen: dict[str, str] = {}

    for entry in sorted(techniques_dir.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith((".yaml", ".yml")):
            continue
        raw = yaml.safe_load(entry.read_text(encoding="utf-8")) or {}
        items = raw.get("techniques", []) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise ValueError(f"{entry.name}: expected a 'techniques' list")
        for item in items:
            record = TechniqueRecord.model_validate(item)
            if record.id in seen:
                raise ValueError(
                    f"duplicate technique id {record.id!r} in {entry.name} "
                    f"(already defined in {seen[record.id]})"
                )
            seen[record.id] = entry.name
            records.append(record)

    records.sort(key=lambda r: r.id)
    _log.debug("loaded %d technique records (KB v%s)", len(records), KNOWLEDGE_BASE_VERSION)
    return tuple(records)


@lru_cache(maxsize=1)
def load_technique_index() -> dict[str, TechniqueRecord]:
    """Return technique records keyed by ``id``."""
    return {t.id: t for t in load_techniques()}


def verify_builder_keys(records: tuple[TechniqueRecord, ...] | None = None) -> list[str]:
    """Return technique IDs whose ``builder_key`` is not a registered factory.

    Records with ``builder_key=None`` (strategy-only techniques) are skipped.
    """
    from nuguard.redteam.catalog.builders import BUILDER_FACTORIES

    records = records or load_techniques()
    return [
        r.id
        for r in records
        if r.builder_key and r.builder_key not in BUILDER_FACTORIES
    ]


def verify_scenario_ids(records: tuple[TechniqueRecord, ...] | None = None) -> list[str]:
    """Return ``(technique_id, scenario_id)`` strings for unknown scenario refs."""
    from nuguard.redteam.catalog.registry import CATALOG_BY_ID

    records = records or load_techniques()
    bad: list[str] = []
    for r in records:
        for sid in r.mapped_scenario_ids:
            if sid not in CATALOG_BY_ID:
                bad.append(f"{r.id}->{sid}")
    return bad
