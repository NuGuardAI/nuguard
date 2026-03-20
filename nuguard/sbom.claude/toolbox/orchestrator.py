"""Plugin orchestrator — run one or all toolbox plugins on an AI-SBOM."""

from __future__ import annotations

from nuguard.models.sbom import AiSbomDocument
from nuguard.sbom.toolbox.plugins._base import ToolResult
from nuguard.sbom.toolbox.plugins.atlas_annotator import AtlasAnnotatorPlugin
from nuguard.sbom.toolbox.plugins.dependency_analyzer import DependencyAnalyzerPlugin
from nuguard.sbom.toolbox.plugins.license_checker import LicenseCheckerPlugin
from nuguard.sbom.toolbox.plugins.markdown_exporter import MarkdownExporterPlugin
from nuguard.sbom.toolbox.plugins.sarif_exporter import SarifExporterPlugin
from nuguard.sbom.toolbox.plugins.vulnerability import VulnerabilityScannerPlugin

PLUGIN_REGISTRY: dict[str, type] = {
    "vulnerability": VulnerabilityScannerPlugin,
    "atlas": AtlasAnnotatorPlugin,
    "sarif": SarifExporterPlugin,
    "markdown": MarkdownExporterPlugin,
    "license": LicenseCheckerPlugin,
    "dependency": DependencyAnalyzerPlugin,
}


class PluginOrchestrator:
    """Run toolbox plugins against an AI-SBOM document.

    Example::

        orchestrator = PluginOrchestrator()
        result = orchestrator.run("vulnerability", doc)
        all_results = orchestrator.run_all(doc)
    """

    def run(
        self,
        plugin_name: str,
        sbom: AiSbomDocument | dict,
        config: dict | None = None,
    ) -> ToolResult:
        """Run a single plugin by name.

        Args:
            plugin_name: Key in :data:`PLUGIN_REGISTRY`.
            sbom: SBOM document or plain dict.
            config: Plugin-specific configuration.

        Returns:
            :class:`~nuguard.sbom.toolbox.plugins._base.ToolResult`.

        Raises:
            :class:`ValueError`: When *plugin_name* is not registered.
        """
        if plugin_name not in PLUGIN_REGISTRY:
            raise ValueError(
                f"Unknown plugin '{plugin_name}'. "
                f"Available: {list(PLUGIN_REGISTRY)}"
            )
        plugin_cls = PLUGIN_REGISTRY[plugin_name]
        plugin = plugin_cls()
        return plugin.run(sbom, config or {})  # type: ignore[attr-defined]

    def run_all(
        self,
        sbom: AiSbomDocument | dict,
        config: dict | None = None,
    ) -> dict[str, ToolResult]:
        """Run all registered plugins and return a name→result mapping.

        Args:
            sbom: SBOM document or plain dict.
            config: Shared config passed to every plugin.

        Returns:
            Dict mapping plugin name to its :class:`ToolResult`.
        """
        results: dict[str, ToolResult] = {}
        for name in PLUGIN_REGISTRY:
            try:
                results[name] = self.run(name, sbom, config)
            except Exception as exc:  # noqa: BLE001
                results[name] = ToolResult(
                    status="fail",
                    message=f"Plugin '{name}' raised an exception: {exc}",
                    details=[],
                )
        return results

    def list_plugins(self) -> list[str]:
        """Return the list of registered plugin names."""
        return list(PLUGIN_REGISTRY)
