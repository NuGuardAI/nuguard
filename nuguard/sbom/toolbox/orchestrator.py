"""PluginOrchestrator — runs named toolbox plugins against an AiSbomDocument."""

from __future__ import annotations

import logging
from typing import Any

from .models import ToolResult
from .plugin_base import ToolPlugin
from .plugins.dependency import DependencyAnalyzerPlugin
from .plugins.license_checker import LicenseCheckerPlugin
from .plugins.markdown_exporter import MarkdownExporterPlugin
from .plugins.sarif_exporter import SarifExporterPlugin

_log = logging.getLogger(__name__)

_DEFAULT_PLUGINS: list[type[ToolPlugin]] = [
    DependencyAnalyzerPlugin,
    LicenseCheckerPlugin,
    MarkdownExporterPlugin,
    SarifExporterPlugin,
]

# Public registry: maps canonical plugin name → plugin class
PLUGIN_REGISTRY: dict[str, type[ToolPlugin]] = {
    cls().name: cls for cls in _DEFAULT_PLUGINS  # type: ignore[abstract]
}


class PluginOrchestrator:
    """Thin registry that maps plugin names to plugin instances."""

    def __init__(self) -> None:
        self._plugins: dict[str, ToolPlugin] = {}
        for cls in _DEFAULT_PLUGINS:
            try:
                instance = cls()
                self._plugins[instance.name] = instance
            except Exception as exc:  # noqa: BLE001
                _log.warning("failed to load plugin %s: %s", cls.__name__, exc)

    def list_plugins(self) -> list[str]:
        return sorted(self._plugins)

    def run(self, plugin_name: str, doc: Any, config: dict[str, Any] | None = None) -> ToolResult:
        plugin = self._plugins.get(plugin_name)
        if plugin is None:
            supported = ", ".join(sorted(self._plugins))
            raise ValueError(f"Unknown plugin '{plugin_name}'. Available: {supported}")
        from ..serializer import AiSbomSerializer
        raw = AiSbomSerializer.to_dict(doc)
        return plugin.run(raw, config or {})

    def run_all(self, doc: Any, config: dict[str, Any] | None = None) -> dict[str, ToolResult]:
        """Run all registered plugins and return a name → result mapping."""
        return {name: self.run(name, doc, config) for name in self._plugins}
