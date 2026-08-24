"""Public Pydantic APIs for SBOM generation, parsing, rendering, and export."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.generator import SbomGenerator
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.serializer import AiSbomSerializer


SbomRenderFormat = Literal["json", "cyclonedx", "cyclonedx-ext", "markdown"]


class SbomGenerateRequest(BaseModel):
    """JSON-safe request for SBOM extraction."""

    source_path: str | None = None
    repo_url: str | None = None
    repo_ref: str = "main"
    config: AiSbomConfig = Field(default_factory=AiSbomConfig)

    @model_validator(mode="after")
    def _validate_source_selector(self) -> "SbomGenerateRequest":
        source_set = bool(self.source_path)
        repo_set = bool(self.repo_url)
        if source_set == repo_set:
            raise ValueError("Provide exactly one of source_path or repo_url")
        return self


class SbomGenerateResult(BaseModel):
    """JSON-safe result for SBOM extraction."""

    sbom: AiSbomDocument
    source_ref: str
    node_count: int
    edge_count: int


class SbomParseRequest(BaseModel):
    """JSON-safe request for parsing an SBOM document."""

    raw_json: str


class SbomParseResult(BaseModel):
    """JSON-safe parse result."""

    sbom: AiSbomDocument
    node_count: int
    edge_count: int


class SbomRenderRequest(BaseModel):
    """JSON-safe request for rendering an SBOM to a target format."""

    format: SbomRenderFormat = "json"
    options: dict[str, Any] = Field(default_factory=dict)


class SbomRenderResult(BaseModel):
    """Rendered SBOM content and metadata."""

    format: SbomRenderFormat
    content: str
    media_type: str
    filename_hint: str


class SbomExportRequest(BaseModel):
    """JSON-safe request for rendering + writing an SBOM artifact."""

    format: SbomRenderFormat = "json"
    output_path: str
    options: dict[str, Any] = Field(default_factory=dict)


class SbomExportResult(BaseModel):
    """Written SBOM artifact metadata."""

    format: SbomRenderFormat
    output_path: str
    media_type: str
    bytes_written: int


def _render_markdown(sbom: "AiSbomDocument") -> str:
    from nuguard.sbom.toolbox.plugins.markdown_exporter import (  # noqa: PLC0415
        MarkdownExporterPlugin,
    )

    sbom_dict = AiSbomSerializer.to_dict(sbom)
    result = MarkdownExporterPlugin().run(sbom_dict, {})
    return str(result.details.get("markdown", ""))


async def generate_sbom(request: SbomGenerateRequest) -> SbomGenerateResult:
    """Generate an SBOM from a source path or repository URL."""
    generator = SbomGenerator(config=request.config)

    if request.source_path:
        source = Path(request.source_path)
        doc = generator.from_path(source)
        source_ref = str(source)
    else:
        assert request.repo_url is not None
        doc = generator.from_repo(request.repo_url, ref=request.repo_ref)
        source_ref = request.repo_url

    return SbomGenerateResult(
        sbom=doc,
        source_ref=source_ref,
        node_count=len(doc.nodes),
        edge_count=len(doc.edges),
    )


async def parse_sbom_json(request: SbomParseRequest) -> SbomParseResult:
    """Parse a native NuGuard SBOM JSON string."""
    doc = AiSbomSerializer.from_json(request.raw_json)
    return SbomParseResult(sbom=doc, node_count=len(doc.nodes), edge_count=len(doc.edges))


async def render_sbom(
    request: SbomRenderRequest,
    *,
    sbom: AiSbomDocument,
) -> SbomRenderResult:
    """Render an SBOM to json/cyclonedx/cyclonedx-ext/markdown."""
    format_name = request.format
    options = request.options

    if format_name == "json":
        content = AiSbomSerializer.to_json(sbom)
        return SbomRenderResult(
            format=format_name,
            content=content,
            media_type="application/json",
            filename_hint="app.sbom.json",
        )

    if format_name == "cyclonedx":
        payload = AiSbomSerializer.to_cyclonedx(
            sbom,
            spec_version=str(options.get("spec_version", "1.6")),
        )
        return SbomRenderResult(
            format=format_name,
            content=json.dumps(payload, indent=2),
            media_type="application/json",
            filename_hint="app.cyclonedx.json",
        )

    if format_name == "cyclonedx-ext":
        payload = AiSbomSerializer.to_cyclonedx_extended(
            sbom,
            spec_version=str(options.get("spec_version", "1.6")),
        )
        return SbomRenderResult(
            format=format_name,
            content=json.dumps(payload, indent=2),
            media_type="application/json",
            filename_hint="app.cyclonedx-ext.json",
        )

    content = _render_markdown(sbom)
    return SbomRenderResult(
        format="markdown",
        content=content,
        media_type="text/markdown",
        filename_hint="app.sbom.md",
    )


async def export_sbom(
    request: SbomExportRequest,
    *,
    sbom: AiSbomDocument,
) -> SbomExportResult:
    """Render an SBOM and write it to disk."""
    rendered = await render_sbom(
        SbomRenderRequest(format=request.format, options=request.options),
        sbom=sbom,
    )

    output_path = Path(request.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered.content, encoding="utf-8")

    return SbomExportResult(
        format=rendered.format,
        output_path=str(output_path),
        media_type=rendered.media_type,
        bytes_written=len(rendered.content.encode("utf-8")),
    )
