"""Regression tests for contextual generic datastore detection."""

from __future__ import annotations

import json
from pathlib import Path

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.types import ComponentType


def _datastores(root: Path, extensions: set[str]):
    config = AiSbomConfig(include_extensions=extensions, enable_llm=False)
    doc = AiSbomExtractor().extract_from_path(root, config)
    return [node for node in doc.nodes if node.component_type == ComponentType.DATASTORE]


def _datastore_names(root: Path, extensions: set[str]) -> set[str]:
    return {node.name.lower() for node in _datastores(root, extensions)}


def test_theme_json_neon_text_is_not_a_datastore(tmp_path: Path) -> None:
    (tmp_path / "theme.json").write_text(
        json.dumps(
            {
                "name": "neon",
                "title": "Neon -- the sign in the scene",
                "voice": "A hand-bent neon tube glows at dusk",
            }
        )
    )

    assert "neon" not in _datastore_names(tmp_path, {".json"})


def test_structured_json_neon_provider_is_a_datastore(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"database": {"provider": "neon"}}))

    assert "neon" in _datastore_names(tmp_path, {".json"})


def test_font_provider_neon_is_not_a_datastore(tmp_path: Path) -> None:
    (tmp_path / "font.json").write_text(json.dumps({"font": {"provider": "neon"}}))

    assert "neon" not in _datastore_names(tmp_path, {".json"})


def test_root_datastore_name_key_is_a_datastore(tmp_path: Path) -> None:
    (tmp_path / "services.json").write_text(json.dumps({"postgres": {"enabled": True}}))

    assert "postgres" in _datastore_names(tmp_path, {".json"})


def test_plural_databases_ancestor_is_authoritative(tmp_path: Path) -> None:
    (tmp_path / "services.json").write_text(
        json.dumps({"databases": {"primary": "neon"}})
    )

    assert "neon" in _datastore_names(tmp_path, {".json"})


def test_plural_connections_ancestor_is_authoritative(tmp_path: Path) -> None:
    (tmp_path / "services.json").write_text(
        json.dumps({"connections": {"primary": "redis"}})
    )

    assert "redis" in _datastore_names(tmp_path, {".json"})


def test_root_key_must_exactly_name_datastore(tmp_path: Path) -> None:
    (tmp_path / "theme.json").write_text(json.dumps({"neon-theme": {"enabled": True}}))

    assert "neon" not in _datastore_names(tmp_path, {".json"})


def test_contextual_json_uses_contextual_occurrence_line(tmp_path: Path) -> None:
    (tmp_path / "mixed.json").write_text(
        "{\n"
        '  "theme": "neon",\n'
        '  "database": {\n'
        '    "provider": "neon"\n'
        "  }\n"
        "}\n"
    )

    neon = next(node for node in _datastores(tmp_path, {".json"}) if node.name.lower() == "neon")
    assert {evidence.location.line for evidence in neon.evidence} == {4}


def test_multiple_datastores_in_one_source_are_emitted_separately(tmp_path: Path) -> None:
    (tmp_path / "persistence.py").write_text('BACKENDS = ("postgres", "sqlite")\n')

    names = _datastore_names(tmp_path, {".py"})
    assert {"postgres", "sqlite"} <= names


def test_generic_and_ast_postgres_detection_deduplicate(tmp_path: Path) -> None:
    (tmp_path / "database.py").write_text(
        "from sqlalchemy import create_engine\n"
        'engine = create_engine("postgres://localhost/app")\n'
    )

    datastores = _datastores(tmp_path, {".py"})
    assert len(datastores) == 1
    assert datastores[0].metadata.extras["adapter"] == "python_datastores"
