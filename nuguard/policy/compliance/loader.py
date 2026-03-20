"""Compliance control loader.

Loads ComplianceControl definitions from bundled framework JSON files or
from a user-supplied custom JSON file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nuguard.common.logging import get_logger
from nuguard.models.policy import ComplianceControl, FrameworkRef

_log = get_logger(__name__)

# Directory containing bundled framework JSON files (alongside this module).
_DATA_DIR = Path(__file__).parent / "data"

# Bundled framework name → filename mapping
_BUNDLED_FRAMEWORKS: dict[str, str] = {
    "owasp-llm-top10": "owasp_llm_top10.json",
    "nist-ai-rmf": "nist_ai_rmf.json",
    "eu-ai-act": "eu_ai_act.json",
}


def _control_from_dict(data: dict[str, Any], framework_name: str) -> ComplianceControl:
    """Parse a single control dict into a ComplianceControl.

    Raises:
        ValueError: When required fields are missing.
    """
    for required in ("id", "name", "description"):
        if required not in data:
            raise ValueError(
                f"Control is missing required field {required!r}: {data}"
            )

    framework_refs: list[FrameworkRef] = []
    for ref_dict in data.get("framework_refs", []):
        framework_refs.append(
            FrameworkRef(
                framework=ref_dict.get("framework", framework_name),
                control_id=ref_dict.get("control_id", data["id"]),
                url=ref_dict.get("url"),
            )
        )

    return ComplianceControl(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        framework=data.get("framework", framework_name),
        framework_refs=framework_refs,
        control_type=data.get("control_type", "automated"),  # type: ignore[arg-type]
        ai_sbom_assessable=data.get("ai_sbom_assessable", True),
        manual_attestation_required=data.get("manual_attestation_required", False),
        ai_sbom_basis=data.get("ai_sbom_basis", []),
        severity=data.get("severity", "medium"),
        gap_diagnosis=data.get("gap_diagnosis", ""),
        fix_guidance=data.get("fix_guidance", ""),
        tags=data.get("tags", []),
        ccd=data.get("ccd"),
    )


def load_controls(
    framework: str | None = None,
    path: Path | None = None,
) -> list[ComplianceControl]:
    """Load ComplianceControls from a bundled framework name or a custom JSON file.

    Args:
        framework: Bundled framework name, e.g. ``"owasp-llm-top10"``.
        path: Path to a custom JSON file following the same schema.

    Returns:
        List of ComplianceControl instances.

    Raises:
        ValueError: When neither *framework* nor *path* is provided, or when
                    the framework name is not recognised.
        FileNotFoundError: When the specified *path* does not exist.
    """
    import json

    if path is not None:
        if not path.exists():
            raise FileNotFoundError(f"Controls file not found: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
    elif framework is not None:
        filename = _BUNDLED_FRAMEWORKS.get(framework.lower())
        if filename is None:
            raise ValueError(
                f"Unknown framework {framework!r}. "
                f"Available: {list(_BUNDLED_FRAMEWORKS)}"
            )
        data_path = _DATA_DIR / filename
        if not data_path.exists():
            _log.warning("Bundled framework file not found: %s", data_path)
            return []
        raw = json.loads(data_path.read_text(encoding="utf-8"))
    else:
        raise ValueError("Provide either 'framework' or 'path'.")

    framework_name = raw.get("framework", framework or "unknown")
    controls: list[ComplianceControl] = []
    for ctrl_dict in raw.get("controls", []):
        try:
            ctrl_dict.setdefault("framework", framework_name)
            controls.append(_control_from_dict(ctrl_dict, framework_name))
        except (ValueError, KeyError) as exc:
            _log.warning("Skipping invalid control: %s", exc)

    _log.debug("Loaded %d controls for framework %r", len(controls), framework_name)
    return controls


def map_finding_to_refs(
    finding_control_id: str,
    controls: list[ComplianceControl],
) -> list[FrameworkRef]:
    """Return framework_refs from the control whose id matches *finding_control_id*.

    Args:
        finding_control_id: Control ID to look up.
        controls: List of loaded ComplianceControls.

    Returns:
        framework_refs list, or an empty list when no match is found.
    """
    for control in controls:
        if control.id == finding_control_id:
            return control.framework_refs
    _log.debug("No control found for finding_control_id=%r", finding_control_id)
    return []
