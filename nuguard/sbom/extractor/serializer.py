"""AI-SBOM serialization: JSON round-trip and CycloneDX 1.6 export."""

from __future__ import annotations

import json
from typing import Any

from nuguard.models.sbom import AiSbomDocument, PackageDep


class AiSbomSerializer:
    """Serialize and deserialize :class:`~nuguard.models.sbom.AiSbomDocument`."""

    @staticmethod
    def to_json(doc: AiSbomDocument) -> str:
        """Serialize *doc* to a JSON string.

        Uses Pydantic's ``model_dump`` with mode ``"json"`` so that datetimes
        and enums are serialized to their JSON-safe forms.
        """
        data = doc.model_dump(mode="json")
        return json.dumps(data, indent=2, ensure_ascii=False)

    @staticmethod
    def from_json(data: str | dict[str, Any]) -> AiSbomDocument:
        """Deserialize *data* (JSON string or dict) into an
        :class:`~nuguard.models.sbom.AiSbomDocument`.
        """
        if isinstance(data, str):
            data = json.loads(data)
        return AiSbomDocument.model_validate(data)

    @staticmethod
    def to_cyclonedx(doc: AiSbomDocument) -> dict[str, Any]:
        """Export *doc* as a CycloneDX 1.6 BOM dict.

        Only the ``deps`` (package dependencies) are mapped to CycloneDX
        components.  AI-specific node types have no standard CycloneDX
        equivalent and are omitted.
        """

        def _dep_to_component(dep: PackageDep) -> dict[str, Any]:
            component: dict[str, Any] = {
                "type": "library",
                "name": dep.name,
            }
            if dep.version_spec:
                component["version"] = dep.version_spec.lstrip(">=<~^!=")
            if dep.purl:
                component["purl"] = dep.purl
            elif dep.ecosystem:
                # Build a minimal purl when not provided
                version_part = (
                    f"@{dep.version_spec.lstrip('>=<~^!=')}" if dep.version_spec else ""
                )
                component["purl"] = f"pkg:{dep.ecosystem}/{dep.name}{version_part}"
            return component

        components = [_dep_to_component(dep) for dep in doc.deps]
        return {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "version": 1,
            "metadata": {
                "timestamp": doc.generated_at.isoformat(),
                "tools": [{"vendor": "NuGuardAI", "name": "nuguard", "version": "0.1.0"}],
                "component": {
                    "type": "application",
                    "name": doc.target,
                },
            },
            "components": components,
        }
