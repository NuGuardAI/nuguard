"""Best-effort SBOM component -> source-code evidence lookup.

Used to attach a second, deeper evidence entry to compiled PolicyControls:
in addition to the policy-document file/line, point at the original source
file/line of an SBOM component (tool, model, datastore) whose name is
mentioned in the control's description.
"""

from __future__ import annotations

import json
from pathlib import Path

from nuguard.common.logging import get_logger
from nuguard.sbom.models import SourceLocation

_log = get_logger(__name__)

_RELEVANT_COMPONENT_TYPES = {"TOOL", "MODEL", "DATASTORE", "AGENT"}


def build_component_evidence(sbom_path: Path) -> dict[str, SourceLocation]:
    """Return a map of SBOM component name -> SourceLocation from its first evidence entry.

    Reads the raw SBOM JSON directly (no full model validation) so this stays
    cheap and tolerant of partial/older SBOM files. Returns an empty dict on
    any error or when *sbom_path* doesn't exist.
    """
    if not sbom_path.exists():
        return {}

    try:
        data = json.loads(sbom_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.debug("build_component_evidence: could not read/parse %s: %s", sbom_path, exc)
        return {}

    result: dict[str, SourceLocation] = {}
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
        if not path:
            continue
        result[name] = SourceLocation(path=path, line=location.get("line"))

    return result
