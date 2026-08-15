"""Best-effort SBOM component -> source-code evidence lookup.

Used to attach deeper evidence entries to compiled PolicyControls: in
addition to the policy-document file/line, point at the original source
file/line of an SBOM component (tool, model, datastore, agent, or system
prompt) whose vocabulary overlaps with the control's description.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nuguard.common.logging import get_logger
from nuguard.sbom.models import SourceLocation

_log = get_logger(__name__)

_RELEVANT_COMPONENT_TYPES = {"TOOL", "MODEL", "DATASTORE", "AGENT", "PROMPT"}
_MAX_CONTENT_CHARS = 1000


@dataclass(frozen=True)
class ComponentEvidenceCandidate:
    """An SBOM component that can serve as evidence for a policy control."""

    name: str
    location: SourceLocation
    match_text: str


def _enriched_sibling(sbom_path: Path) -> Path | None:
    """Return the ``<stem>.enriched.json`` sibling of *sbom_path*, if it exists.

    Enriched SBOMs carry LLM-populated ``metadata.description`` and
    ``metadata.extras.content`` (for PROMPT nodes) that plain regex/AST
    extraction usually doesn't populate — using it when available makes
    component-evidence matching meaningfully more accurate.
    """
    name = sbom_path.name
    if name.endswith(".enriched.json"):
        return None
    if not name.endswith(".json"):
        return None
    candidate = sbom_path.with_name(name[: -len(".json")] + ".enriched.json")
    return candidate if candidate.exists() else None


def _load_sbom_data(sbom_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(sbom_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.debug("build_component_evidence: could not read/parse %s: %s", sbom_path, exc)
        return None


def _build_match_text(name: str, metadata: dict[str, Any]) -> str:
    parts = [name]
    description = metadata.get("description")
    if description:
        parts.append(str(description))
    system_prompt_excerpt = metadata.get("system_prompt_excerpt")
    if system_prompt_excerpt:
        parts.append(str(system_prompt_excerpt))
    extras = metadata.get("extras") or {}
    extras_description = extras.get("description")
    if extras_description:
        parts.append(str(extras_description))
    content = extras.get("content")
    if content:
        parts.append(str(content)[:_MAX_CONTENT_CHARS])
    return " ".join(parts).lower()


def build_component_evidence(sbom_path: Path) -> list[ComponentEvidenceCandidate]:
    """Return SBOM components usable as evidence for compiled policy controls.

    Reads the raw SBOM JSON directly (no full model validation) so this stays
    cheap and tolerant of partial/older SBOM files. When a
    ``<stem>.enriched.json`` sibling exists alongside *sbom_path*, it is
    preferred as the richer source of node metadata (LLM-generated
    descriptions and prompt content).

    Returns an empty list on any error or when *sbom_path* doesn't exist.
    """
    if not sbom_path.exists():
        return []

    enriched = _enriched_sibling(sbom_path)
    data = _load_sbom_data(enriched) if enriched is not None else None
    if data is None:
        data = _load_sbom_data(sbom_path)
    if data is None:
        return []

    result: list[ComponentEvidenceCandidate] = []
    for node in data.get("nodes") or []:
        name = node.get("name")
        component_type = node.get("component_type")
        if not name or component_type not in _RELEVANT_COMPONENT_TYPES:
            continue
        evidence_list = node.get("evidence") or []
        if not evidence_list:
            continue
        location = evidence_list[0].get("location") or {}
        path = location.get("path")
        if not path or path.startswith("<"):
            # Skip synthetic/placeholder paths (e.g. "<runtime>" for
            # dynamically-discovered components) — not real source evidence.
            continue
        metadata = node.get("metadata") or {}
        result.append(
            ComponentEvidenceCandidate(
                name=name,
                location=SourceLocation(path=path, line=location.get("line")),
                match_text=_build_match_text(name, metadata),
            )
        )

    return result
