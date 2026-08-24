from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom.public_api import (
    SbomExportRequest,
    SbomGenerateRequest,
    SbomParseRequest,
    SbomRenderRequest,
    export_sbom,
    generate_sbom,
    parse_sbom_json,
    render_sbom,
)
from nuguard.sbom.toolbox.public_api import (
    ToolboxListPluginsRequest,
    ToolboxRunPluginRequest,
    list_toolbox_plugins,
    run_toolbox_plugin,
)

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "app_relpath",
    [
        "tests/apps/openai-cs-agents-demo",
        "tests/apps/Gemini-Auto-app",
        "tests/apps/pinnacle-bank-app",
    ],
)
async def test_public_sbom_api_end_to_end_smoke(app_relpath: str, tmp_path: Path) -> None:
    """Exercise SBOM public APIs against representative real-world fixtures."""
    repo_root = Path(__file__).resolve().parents[2]
    source_path = repo_root / app_relpath
    assert source_path.exists(), f"Missing fixture source path: {source_path}"

    generated = await generate_sbom(SbomGenerateRequest(source_path=str(source_path)))
    assert generated.node_count > 0
    assert generated.edge_count >= 0

    rendered_json = await render_sbom(SbomRenderRequest(format="json"), sbom=generated.sbom)
    assert rendered_json.media_type == "application/json"
    assert rendered_json.content.strip().startswith("{")

    parsed = await parse_sbom_json(SbomParseRequest(raw_json=rendered_json.content))
    assert parsed.node_count == generated.node_count
    assert parsed.edge_count == generated.edge_count

    rendered_markdown = await render_sbom(SbomRenderRequest(format="markdown"), sbom=generated.sbom)
    assert rendered_markdown.media_type == "text/markdown"
    assert len(rendered_markdown.content) > 0

    rendered_cyclonedx = await render_sbom(SbomRenderRequest(format="cyclonedx"), sbom=generated.sbom)
    assert rendered_cyclonedx.media_type == "application/json"
    assert '"bomFormat": "CycloneDX"' in rendered_cyclonedx.content

    export_path = tmp_path / f"{source_path.name}.public-api.sbom.json"
    exported = await export_sbom(
        SbomExportRequest(format="json", output_path=str(export_path)),
        sbom=generated.sbom,
    )
    assert exported.output_path == str(export_path)
    assert exported.bytes_written > 0
    assert export_path.exists()

    plugins = await list_toolbox_plugins(ToolboxListPluginsRequest())
    assert "dependency_analyze" in plugins.plugins

    dependency_result = await run_toolbox_plugin(
        ToolboxRunPluginRequest(plugin_name="dependency_analyze"),
        sbom=generated.sbom,
    )
    assert dependency_result.status == "ok"
    assert dependency_result.plugin_name == "dependency_analyze"
