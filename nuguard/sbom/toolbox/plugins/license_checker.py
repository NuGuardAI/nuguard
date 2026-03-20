"""LicenseCheckerPlugin — offline license risk analysis for package dependencies."""

from __future__ import annotations

from nuguard.models.sbom import AiSbomDocument
from nuguard.sbom.toolbox.plugins._base import ToolResult

# Risk level classification for known SPDX identifiers
_LICENSE_RISK: dict[str, tuple[str, str]] = {
    # safe
    "MIT": ("safe", "Permissive — no restrictions"),
    "BSD-2-Clause": ("safe", "Permissive — no restrictions"),
    "BSD-3-Clause": ("safe", "Permissive — no restrictions"),
    "Apache-2.0": ("safe", "Permissive with patent grant"),
    "ISC": ("safe", "Permissive — no restrictions"),
    "Unlicense": ("safe", "Public domain"),
    "CC0-1.0": ("safe", "Public domain dedication"),
    # weak copyleft
    "LGPL-2.1": ("info", "Weak copyleft — linking may require disclosure"),
    "LGPL-3.0": ("info", "Weak copyleft — linking may require disclosure"),
    "LGPL-2.1-only": ("info", "Weak copyleft"),
    "LGPL-3.0-only": ("info", "Weak copyleft"),
    # copyleft
    "GPL-2.0": ("warn", "Copyleft — distributing requires source disclosure"),
    "GPL-3.0": ("warn", "Copyleft — distributing requires source disclosure"),
    "GPL-2.0-only": ("warn", "Copyleft"),
    "GPL-3.0-only": ("warn", "Copyleft"),
    "AGPL-3.0": ("warn", "Strong copyleft — network use triggers copyleft"),
    "AGPL-3.0-only": ("warn", "Strong copyleft — network use triggers copyleft"),
    # proprietary restriction
    "SSPL-1.0": ("warn", "Proprietary restriction — SaaS use may require open-sourcing"),
    "Commons-Clause": ("warn", "Commercial restriction"),
    # unknown
    "UNKNOWN": ("warn", "Cannot determine license"),
}

_PYPI_API = "https://pypi.org/pypi/{name}/json"


class LicenseCheckerPlugin:
    """Check licenses for all package dependencies in the SBOM."""

    def run(self, sbom: AiSbomDocument | dict, config: dict | None = None) -> ToolResult:
        """Check license risk for all dependencies.

        Args:
            sbom: :class:`~nuguard.models.sbom.AiSbomDocument` or plain dict.
            config: ``check_pypi`` (bool, default True) to query PyPI API.

        Returns:
            :class:`~nuguard.sbom.toolbox.plugins._base.ToolResult`.
        """
        config = config or {}
        if isinstance(sbom, dict):
            from nuguard.sbom.extractor.serializer import AiSbomSerializer
            doc = AiSbomSerializer.from_json(sbom)
        else:
            doc = sbom

        check_pypi: bool = config.get("check_pypi", True)
        findings: list[dict] = []

        for dep in doc.deps:
            if not dep.name:
                continue

            # Try to get license from PyPI if online
            license_id: str | None = None
            if check_pypi and dep.ecosystem in (None, "pypi", "PyPI"):
                license_id = self._fetch_pypi_license(dep.name)

            if not license_id:
                license_id = "UNKNOWN"

            risk_level, description = _LICENSE_RISK.get(
                license_id, ("warn", f"Unrecognized license: {license_id}")
            )

            findings.append(
                {
                    "package": dep.name,
                    "version": dep.version_spec,
                    "license": license_id,
                    "risk": risk_level,
                    "description": description,
                }
            )

        warn_count = sum(1 for f in findings if f["risk"] == "warn")
        info_count = sum(1 for f in findings if f["risk"] == "info")

        if not findings:
            return ToolResult(status="pass", message="No dependencies to check.", details=[])

        status = "warn" if warn_count > 0 else "pass"
        msg = (
            f"License check complete: {len(findings)} deps, "
            f"{warn_count} warnings, {info_count} informational."
        )
        return ToolResult(status=status, message=msg, details=findings)

    @staticmethod
    def _fetch_pypi_license(name: str) -> str | None:
        try:
            import httpx
            url = _PYPI_API.format(name=name)
            resp = httpx.get(url, timeout=5.0, follow_redirects=True)
            if resp.status_code == 200:
                data = resp.json()
                info = data.get("info", {})
                license_field = info.get("license") or ""
                # PyPI often has "License :: OSI Approved :: MIT License" classifiers
                classifiers = info.get("classifiers", [])
                for clf in classifiers:
                    if "License ::" in clf:
                        parts = clf.split(" :: ")
                        if len(parts) >= 3:
                            spdx = _normalize_license(parts[-1])
                            if spdx:
                                return spdx
                return _normalize_license(license_field) or license_field or None
        except Exception:
            pass
        return None


def _normalize_license(raw: str) -> str | None:
    """Map a raw license string to a known SPDX identifier."""
    raw = raw.strip()
    # Direct SPDX match
    if raw in _LICENSE_RISK:
        return raw
    # Common aliases
    mapping = {
        "MIT License": "MIT",
        "BSD License": "BSD-3-Clause",
        "BSD 2-Clause License": "BSD-2-Clause",
        "BSD 3-Clause License": "BSD-3-Clause",
        "Apache Software License": "Apache-2.0",
        "Apache 2.0": "Apache-2.0",
        "ISC License": "ISC",
        "GNU General Public License v2 (GPLv2)": "GPL-2.0",
        "GNU General Public License v3 (GPLv3)": "GPL-3.0",
        "GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.1",
        "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
        "GNU Affero General Public License v3": "AGPL-3.0",
    }
    return mapping.get(raw)
