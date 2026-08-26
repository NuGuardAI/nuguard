"""Public Pydantic APIs for SBOM toolbox plugin execution."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from nuguard.sbom.toolbox.orchestrator import PluginOrchestrator

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument


ToolboxOutputFormat = Literal["json", "markdown"]


class ToolboxListPluginsRequest(BaseModel):
    """JSON-safe request for listing toolbox plugins."""


class ToolboxListPluginsResult(BaseModel):
    """Available toolbox plugins."""

    plugins: list[str] = Field(default_factory=list)


class ToolboxRunPluginRequest(BaseModel):
    """JSON-safe request for running one toolbox plugin."""

    plugin_name: str
    config: dict[str, Any] = Field(default_factory=dict)
    output_format: ToolboxOutputFormat = "json"


class ToolboxRunPluginResult(BaseModel):
    """Normalized plugin execution result."""

    plugin_name: str
    status: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    content: str | None = None
    media_type: str | None = None


class ToolboxRunAllRequest(BaseModel):
    """JSON-safe request for running all toolbox plugins."""

    config: dict[str, Any] = Field(default_factory=dict)


class ToolboxRunAllResult(BaseModel):
    """Name to normalized plugin result mapping."""

    results: dict[str, ToolboxRunPluginResult] = Field(default_factory=dict)


def _normalize_plugin_content(plugin_name: str, details: dict[str, Any]) -> tuple[str | None, str | None]:
    if plugin_name in {
        "sarif_export",
        "cyclonedx_export",
        "cyclonedx_ext_export",
        "spdx_export",
    }:
        return json.dumps(details, indent=2, default=str), "application/json"

    if plugin_name == "markdown_export":
        return str(details.get("markdown", "")), "text/markdown"

    return json.dumps(details, indent=2, default=str), "application/json"


async def list_toolbox_plugins(request: ToolboxListPluginsRequest) -> ToolboxListPluginsResult:
    """List registered SBOM toolbox plugin names."""
    _ = request
    orchestrator = PluginOrchestrator()
    return ToolboxListPluginsResult(plugins=orchestrator.list_plugins())


async def run_toolbox_plugin(
    request: ToolboxRunPluginRequest,
    *,
    sbom: "AiSbomDocument",
) -> ToolboxRunPluginResult:
    """Run one toolbox plugin against an AiSbomDocument."""
    orchestrator = PluginOrchestrator()
    result = orchestrator.run(request.plugin_name, sbom, request.config)

    content: str | None = None
    media_type: str | None = None
    if request.output_format == "json" or request.plugin_name in {
        "sarif_export",
        "cyclonedx_export",
        "cyclonedx_ext_export",
        "spdx_export",
        "markdown_export",
    }:
        content, media_type = _normalize_plugin_content(request.plugin_name, result.details)

    return ToolboxRunPluginResult(
        plugin_name=request.plugin_name,
        status=result.status,
        message=result.message,
        details=result.details,
        content=content,
        media_type=media_type,
    )


async def run_toolbox_all(
    request: ToolboxRunAllRequest,
    *,
    sbom: "AiSbomDocument",
) -> ToolboxRunAllResult:
    """Run all registered toolbox plugins."""
    orchestrator = PluginOrchestrator()
    raw_results = orchestrator.run_all(sbom, request.config)

    normalized: dict[str, ToolboxRunPluginResult] = {}
    for name, result in raw_results.items():
        content, media_type = _normalize_plugin_content(name, result.details)
        normalized[name] = ToolboxRunPluginResult(
            plugin_name=name,
            status=result.status,
            message=result.message,
            details=result.details,
            content=content,
            media_type=media_type,
        )

    return ToolboxRunAllResult(results=normalized)
