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
                "metadata": {"description": "Processes a payment for the given account."},
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
    assert len(result) == 1
    cand = result[0]
    assert cand.name == "payment_tool"
    assert cand.location.path == "src/tools/payment.py"
    assert cand.location.line == 42
    assert "processes a payment" in cand.match_text


def test_build_component_evidence_includes_prompt_nodes(tmp_path: Path) -> None:
    sbom = tmp_path / "app.sbom.json"
    _write_sbom(
        sbom,
        [
            {
                "name": "Prompt Registry[Transfer Confirmation]",
                "component_type": "PROMPT",
                "metadata": {
                    "extras": {
                        "content": "The user has requested a fund transfer.",
                        "description": "A prompt confirming a fund transfer.",
                    }
                },
                "evidence": [
                    {
                        "kind": "ast_dict_value",
                        "confidence": 0.8,
                        "detail": "x",
                        "location": {"path": "src/orchestrator/prompt_store.py", "line": 96},
                    }
                ],
            }
        ],
    )
    result = build_component_evidence(sbom)
    assert len(result) == 1
    assert "fund transfer" in result[0].match_text


def test_build_component_evidence_skips_irrelevant_types(tmp_path: Path) -> None:
    sbom = tmp_path / "app.sbom.json"
    _write_sbom(
        sbom,
        [
            {
                "name": "some_endpoint",
                "component_type": "API_ENDPOINT",
                "evidence": [
                    {
                        "kind": "ast",
                        "confidence": 0.9,
                        "detail": "x",
                        "location": {"path": "src/api.py", "line": 1},
                    }
                ],
            }
        ],
    )
    result = build_component_evidence(sbom)
    assert result == []


def test_build_component_evidence_skips_nodes_without_evidence(tmp_path: Path) -> None:
    sbom = tmp_path / "app.sbom.json"
    _write_sbom(sbom, [{"name": "no_evidence_tool", "component_type": "TOOL", "evidence": []}])
    result = build_component_evidence(sbom)
    assert result == []


def test_build_component_evidence_missing_file_returns_empty(tmp_path: Path) -> None:
    result = build_component_evidence(tmp_path / "does-not-exist.json")
    assert result == []


def test_build_component_evidence_malformed_json_returns_empty(tmp_path: Path) -> None:
    sbom = tmp_path / "bad.sbom.json"
    sbom.write_text("not json", encoding="utf-8")
    result = build_component_evidence(sbom)
    assert result == []


def test_build_component_evidence_prefers_enriched_sibling(tmp_path: Path) -> None:
    base = tmp_path / "app.sbom.json"
    enriched = tmp_path / "app.sbom.enriched.json"
    _write_sbom(
        base,
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
    _write_sbom(
        enriched,
        [
            {
                "name": "payment_tool",
                "component_type": "TOOL",
                "metadata": {"description": "Enriched description of payment processing."},
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
    result = build_component_evidence(base)
    assert len(result) == 1
    assert "enriched description" in result[0].match_text
