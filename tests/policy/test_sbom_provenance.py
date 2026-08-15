"""Tests for nuguard.policy.sbom_provenance.build_component_evidence."""

from __future__ import annotations

import json
from pathlib import Path

from nuguard.policy.sbom_provenance import build_component_evidence


def _write_sbom(path: Path, nodes: list[dict]) -> None:
    path.write_text(json.dumps({"nodes": nodes}), encoding="utf-8")


def test_build_component_evidence_extracts_location(tmp_path: Path) -> None:
    sbom = tmp_path / "app.sbom.json"
    _write_sbom(
        sbom,
        [
            {
                "name": "payment_tool",
                "component_type": "TOOL",
                "evidence": [
                    {
                        "kind": "ast",
                        "confidence": 0.9,
                        "detail": "x",
                        "location": {"path": "src/tools/payment.py", "line": 42},
                    }
                ],
            }
        ],
    )
    result = build_component_evidence(sbom)
    assert result["payment_tool"].path == "src/tools/payment.py"
    assert result["payment_tool"].line == 42


def test_build_component_evidence_skips_irrelevant_types(tmp_path: Path) -> None:
    sbom = tmp_path / "app.sbom.json"
    _write_sbom(
        sbom,
        [
            {
                "name": "some_prompt",
                "component_type": "PROMPT",
                "evidence": [
                    {
                        "kind": "ast",
                        "confidence": 0.9,
                        "detail": "x",
                        "location": {"path": "src/prompts.py", "line": 1},
                    }
                ],
            }
        ],
    )
    result = build_component_evidence(sbom)
    assert result == {}


def test_build_component_evidence_skips_nodes_without_evidence(tmp_path: Path) -> None:
    sbom = tmp_path / "app.sbom.json"
    _write_sbom(sbom, [{"name": "no_evidence_tool", "component_type": "TOOL", "evidence": []}])
    result = build_component_evidence(sbom)
    assert result == {}


def test_build_component_evidence_missing_file_returns_empty(tmp_path: Path) -> None:
    result = build_component_evidence(tmp_path / "does-not-exist.json")
    assert result == {}


def test_build_component_evidence_malformed_json_returns_empty(tmp_path: Path) -> None:
    sbom = tmp_path / "bad.sbom.json"
    sbom.write_text("not json", encoding="utf-8")
    result = build_component_evidence(sbom)
    assert result == {}
