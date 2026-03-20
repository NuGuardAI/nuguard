"""MarkdownExporterPlugin stub.

TODO: Implement Markdown report export from plugin findings.
"""

from __future__ import annotations

from nuguard.sbom.toolbox.plugins._base import ToolResult


class MarkdownExporterPlugin:
    """Export SBOM toolbox findings as a developer-readable Markdown report.

    TODO: Implement Markdown serialisation for toolbox findings.
    """

    def run(self, sbom_dict: dict, config: dict) -> ToolResult:
        """Render findings from *sbom_dict* as Markdown.

        Args:
            sbom_dict: Parsed AI-SBOM as a plain dict.
            config: Plugin-specific configuration (e.g. ``output_path``).

        Returns:
            :class:`~nuguard.sbom.toolbox.plugins._base.ToolResult`
        """
        # TODO: Implement Markdown report generation.
        return ToolResult(
            status="warn",
            message="MarkdownExporterPlugin not yet implemented.",
            details=[],
        )
