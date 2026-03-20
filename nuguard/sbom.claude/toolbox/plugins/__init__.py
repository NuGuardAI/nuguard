"""Stub plugin classes for the SBOM toolbox."""

from nuguard.sbom.toolbox.plugins.vulnerability import VulnerabilityScannerPlugin
from nuguard.sbom.toolbox.plugins.atlas_annotator import AtlasAnnotatorPlugin
from nuguard.sbom.toolbox.plugins.sarif_exporter import SarifExporterPlugin
from nuguard.sbom.toolbox.plugins.markdown_exporter import MarkdownExporterPlugin
from nuguard.sbom.toolbox.plugins._base import ToolResult

__all__ = [
    "VulnerabilityScannerPlugin",
    "AtlasAnnotatorPlugin",
    "SarifExporterPlugin",
    "MarkdownExporterPlugin",
    "ToolResult",
]
