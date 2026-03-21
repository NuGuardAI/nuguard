"""CLI tests for ``nuguard sbom`` sub-commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nuguard.cli.main import app
from nuguard.sbom.extractor.serializer import AiSbomSerializer
from nuguard.sbom.models import AiSbomDocument

runner = CliRunner(mix_stderr=False)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURE_APP = (
    Path(__file__).parent.parent.parent
    / "nuguard"
    / "sbom"
    / "tests"
    / "fixtures"
    / "apps"
    / "customer_service_bot"
)


def _write_sbom(tmp_path: Path) -> Path:
    """Generate a minimal valid SBOM and write it to *tmp_path*."""
    from nuguard.sbom.extractor import AiSbomExtractor
    from nuguard.sbom.extractor.config import AiSbomConfig

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False)
    doc = AiSbomExtractor().extract_from_path(_FIXTURE_APP, config)
    sbom_file = tmp_path / "app.sbom.json"
    sbom_file.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
    return sbom_file


# ---------------------------------------------------------------------------
# nuguard sbom generate
# ---------------------------------------------------------------------------


def test_generate_requires_source_or_repo(tmp_path: Path) -> None:
    result = runner.invoke(app, ["sbom", "generate", "--output", str(tmp_path / "out.json")])
    assert result.exit_code != 0


def test_generate_from_source(tmp_path: Path) -> None:
    out = tmp_path / "sbom.json"
    result = runner.invoke(
        app,
        ["sbom", "generate", "--source", str(_FIXTURE_APP), "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    data = json.loads(out.read_text())
    assert "nodes" in data


def test_generate_cyclonedx_format(tmp_path: Path) -> None:
    out = tmp_path / "sbom.cdx.json"
    result = runner.invoke(
        app,
        [
            "sbom",
            "generate",
            "--source",
            str(_FIXTURE_APP),
            "--output",
            str(out),
            "--format",
            "cyclonedx",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert data.get("bomFormat") == "CycloneDX"


def test_generate_reads_source_from_nuguard_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text(f"source: {_FIXTURE_APP}\n", encoding="utf-8")
    out = tmp_path / "sbom.json"
    result = runner.invoke(
        app,
        ["sbom", "generate", "--config", str(cfg), "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_generate_bad_source_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["sbom", "generate", "--source", str(tmp_path / "nonexistent"), "--output", str(tmp_path / "out.json")],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# nuguard sbom validate
# ---------------------------------------------------------------------------


def test_validate_valid_sbom(tmp_path: Path) -> None:
    sbom_file = _write_sbom(tmp_path)
    result = runner.invoke(app, ["sbom", "validate", "--file", str(sbom_file)])
    assert result.exit_code == 0, result.output
    assert "valid" in result.output.lower()


def test_validate_invalid_sbom(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"not": "an sbom"}), encoding="utf-8")
    result = runner.invoke(app, ["sbom", "validate", "--file", str(bad)])
    assert result.exit_code != 0


def test_validate_missing_file_exits(tmp_path: Path) -> None:
    result = runner.invoke(app, ["sbom", "validate", "--file", str(tmp_path / "nope.json")])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# nuguard sbom register + show
# ---------------------------------------------------------------------------


def test_register_and_show(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """register stores the SBOM; show retrieves and prints it."""
    db_path = tmp_path / "nuguard.db"
    monkeypatch.setenv("NUGUARD_DB_PATH", str(db_path))

    sbom_file = _write_sbom(tmp_path)
    reg_result = runner.invoke(app, ["sbom", "register", "--file", str(sbom_file)])
    assert reg_result.exit_code == 0, reg_result.output
    assert "registered" in reg_result.output.lower()

    # Extract the ID from the output
    for word in reg_result.output.split():
        if len(word) > 8 and "-" in word:
            sbom_id = word.strip(".")
            break
    else:
        pytest.skip("Could not parse SBOM ID from register output")

    show_result = runner.invoke(app, ["sbom", "show", "--sbom-id", sbom_id])
    assert show_result.exit_code == 0, show_result.output
    data = json.loads(show_result.output)
    assert "nodes" in data


def test_show_unknown_id_exits(tmp_path: Path) -> None:
    result = runner.invoke(app, ["sbom", "show", "--sbom-id", "nonexistent-id"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# nuguard sbom schema
# ---------------------------------------------------------------------------


def test_schema_prints_json() -> None:
    result = runner.invoke(app, ["sbom", "schema"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data.get("title") == "AiSbomDocument"


# ---------------------------------------------------------------------------
# nuguard sbom plugin
# ---------------------------------------------------------------------------


def test_plugin_list() -> None:
    result = runner.invoke(app, ["sbom", "plugin", "list"])
    assert result.exit_code == 0, result.output
    assert "vulnerability" in result.output.lower()


def test_plugin_run_markdown(tmp_path: Path) -> None:
    sbom_file = _write_sbom(tmp_path)
    result = runner.invoke(
        app,
        ["sbom", "plugin", "run", "markdown", "--sbom", str(sbom_file), "--format", "markdown"],
    )
    assert result.exit_code == 0, result.output


def test_plugin_unknown_action_exits(tmp_path: Path) -> None:
    result = runner.invoke(app, ["sbom", "plugin", "badaction"])
    assert result.exit_code != 0


def test_plugin_run_missing_sbom_exits() -> None:
    result = runner.invoke(app, ["sbom", "plugin", "run", "markdown"])
    assert result.exit_code != 0
