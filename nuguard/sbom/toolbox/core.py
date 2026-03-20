from __future__ import annotations

import logging
from typing import Any

from .models import ToolResult
from .plugin_base import ToolPlugin
from .plugins.cyclonedx_exporter import CycloneDxExporter
from .plugins.dependency import DependencyAnalyzerPlugin
from .plugins.license_checker import LicenseCheckerPlugin
from .plugins.markdown_exporter import MarkdownExporterPlugin
from .plugins.sarif_exporter import SarifExporterPlugin
from .plugins.vulnerability import VulnerabilityScannerPlugin
from .plugins.atlas_annotator import AtlasAnnotatorPlugin
from .plugins.aws_security_hub import AwsSecurityHubPlugin
from .plugins.ghas_uploader import GhasUploaderPlugin
from .plugins.xray import XrayPlugin

_log = logging.getLogger("toolbox.core")


class Toolbox:
    def __init__(self) -> None:
        self._plugins: dict[str, ToolPlugin] = {
            AtlasAnnotatorPlugin.name: AtlasAnnotatorPlugin(),
            AwsSecurityHubPlugin.name: AwsSecurityHubPlugin(),
            GhasUploaderPlugin.name: GhasUploaderPlugin(),
            CycloneDxExporter.name: CycloneDxExporter(),
            MarkdownExporterPlugin.name: MarkdownExporterPlugin(),
            SarifExporterPlugin.name: SarifExporterPlugin(),
            XrayPlugin.name: XrayPlugin(),
            VulnerabilityScannerPlugin.name: VulnerabilityScannerPlugin(),
            DependencyAnalyzerPlugin.name: DependencyAnalyzerPlugin(),
            LicenseCheckerPlugin.name: LicenseCheckerPlugin(),
        }
        _log.debug(
            "toolbox initialised with %d plugin(s): %s",
            len(self._plugins),
            ", ".join(sorted(self._plugins)),
        )

    def run(self, tool_name: str, input_doc: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        plugin = self._plugins.get(tool_name)
        if plugin is None:
            supported = ", ".join(sorted(self._plugins.keys()))
            raise ValueError(f"unsupported tool '{tool_name}'. Supported: {supported}")

        _log.info("running plugin '%s'", tool_name)
        try:
            result = plugin.run(input_doc, config)
        except Exception as exc:
            _log.error("plugin '%s' raised an unexpected error: %s", tool_name, exc)
            raise

        _log.info("plugin '%s' finished: status=%s  %s", tool_name, result.status, result.message)
        return result
