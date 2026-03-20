"""AI-SBOM serialization: JSON round-trip, CycloneDX 1.6, and SPDX 3.0.1 export."""

from __future__ import annotations

import json
import uuid
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

    @staticmethod
    def to_spdx(doc: AiSbomDocument) -> dict[str, Any]:
        """Export *doc* as an SPDX 3.0.1 JSON-LD document.

        Maps each :class:`~nuguard.models.sbom.PackageDep` to an SPDX Package
        element.  AI-specific node types are omitted from the SPDX output as
        they have no standard SPDX equivalent.

        Returns:
            SPDX 3.0.1 document as a plain dict (JSON-serializable).
        """

        def _dep_to_spdx_package(dep: PackageDep) -> dict[str, Any]:
            pkg_id = f"SPDXRef-{dep.name.replace(' ', '-').replace('/', '-')}"
            pkg: dict[str, Any] = {
                "type": "software_Package",
                "spdxId": pkg_id,
                "name": dep.name,
                "downloadLocation": "https://pypi.org/project/" + dep.name
                if dep.ecosystem in (None, "pypi", "PyPI")
                else "NOASSERTION",
            }
            if dep.version_spec:
                pkg["versionInfo"] = dep.version_spec.lstrip(">=<~^!=").split(",")[0].strip()

            # Build PURL external ref
            version_part = ""
            if dep.version_spec:
                v = dep.version_spec.lstrip(">=<~^!=").split(",")[0].strip()
                if v:
                    version_part = f"@{v}"
            if dep.purl:
                purl = dep.purl
            elif dep.ecosystem:
                purl = f"pkg:{dep.ecosystem}/{dep.name}{version_part}"
            else:
                purl = f"pkg:pypi/{dep.name}{version_part}"

            pkg["externalRefs"] = [
                {
                    "externalRefType": "packageManager",
                    "locator": purl,
                }
            ]
            return pkg

        packages = [_dep_to_spdx_package(dep) for dep in doc.deps]

        doc_spdx_id = f"SPDXRef-DOCUMENT-{uuid.uuid4().hex[:8]}"

        return {
            "@context": "https://spdx.org/rdf/3.0.1/spdx-context.jsonld",
            "@graph": [
                {
                    "type": "SpdxDocument",
                    "spdxId": doc_spdx_id,
                    "name": doc.target,
                    "creationInfo": {
                        "created": doc.generated_at.isoformat(),
                        "createdBy": [
                            {
                                "type": "Tool",
                                "name": "nuguard",
                                "version": "0.1.0",
                            }
                        ],
                        "specVersion": "3.0.1",
                    },
                    "packages": packages,
                }
            ],
        }
