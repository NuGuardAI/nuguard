"""SarifExporterPlugin stub.

TODO: Implement SARIF 2.1.0 export from plugin findings.
"""

from __future__ import annotations

from nuguard.sbom.toolbox.plugins._base import ToolResult


class SarifExporterPlugin:
    """Export SBOM toolbox findings as SARIF 2.1.0.

    TODO: Implement SARIF serialisation for toolbox findings.
    """

    def run(self, sbom_dict: dict, config: dict) -> ToolResult:
        """Export findings from *sbom_dict* to SARIF format.

        Args:
            sbom_dict: Parsed AI-SBOM as a plain dict.
            config: Plugin-specific configuration (e.g. ``output_path``).

        Returns:
            :class:`~nuguard.sbom.toolbox.plugins._base.ToolResult`
        """
        # TODO: Implement SARIF 2.1.0 serialisation.
        return ToolResult(
            status="warn",
            message="SarifExporterPlugin not yet implemented.",
            details=[],
        )
