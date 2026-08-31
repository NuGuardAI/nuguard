"""Regression tests: NuGuard's own generated report/cache artifacts
(redteam-*/behavior-* .json and .md files) must never be scanned as SBOM
evidence — they are tool output, not application source, and their content
(e.g. quoting a target's system prompt for a judge verdict) can trigger
false-positive component detections such as a spurious ``prompt:generic``
PROMPT node.

See nuguard/sbom/extractor/core.py::_iter_files for the exclusion rule and
nuguard/sbom/adapters/registry.py's ``prompt_generic`` adapter for the
complementary skip_extensions={".json"} defense.
"""
from __future__ import annotations

from pathlib import Path

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.types import ComponentType

_CONFIG = AiSbomConfig(include_extensions={".py", ".json", ".md"}, enable_llm=False)


def _write_app_source(root: Path) -> None:
    (root / "main.py").write_text(
        "from openai import OpenAI\n"
        "client = OpenAI()\n"
        "def handler():\n"
        "    return client.chat.completions.create(model='gpt-4o', messages=[])\n"
    )


def test_redteam_judge_artifact_is_not_scanned(tmp_path: Path) -> None:
    _write_app_source(tmp_path)
    (tmp_path / "redteam-judge-abc123.json").write_text(
        '{"evidence": "The target\'s system_prompt instructs it to refuse PII requests."}'
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _CONFIG)
    for node in doc.nodes:
        for ev in node.evidence:
            assert "redteam-judge-" not in (ev.location.path if ev.location else ""), (
                f"Node {node.name!r} has evidence from a redteam-judge- artifact: {ev}"
            )


def test_behavior_judge_and_redteam_prompts_artifacts_are_not_scanned(tmp_path: Path) -> None:
    _write_app_source(tmp_path)
    (tmp_path / "behavior-judge-xyz.json").write_text(
        '{"prompt_template": "system prompt used for evaluation"}'
    )
    (tmp_path / "redteam-prompts-gpt-4o-xyz.json").write_text(
        '{"chain_of_thought": "few shot prompt injection example"}'
    )
    (tmp_path / "redteam-run1.md").write_text("# Redteam Report\nsystem prompt disclosed")
    doc = AiSbomExtractor().extract_from_path(tmp_path, _CONFIG)
    scanned_paths = {
        ev.location.path
        for node in doc.nodes
        for ev in node.evidence
        if ev.location is not None
    }
    assert not any(p.startswith(("behavior-judge-", "redteam-prompts-", "redteam-run1")) for p in scanned_paths)


def test_generic_prompt_adapter_does_not_fire_on_json_files(tmp_path: Path) -> None:
    """Defense-in-depth: even a non-NuGuard-named .json file should not be
    scanned by the keyword-only prompt_generic fallback adapter."""
    _write_app_source(tmp_path)
    (tmp_path / "some_report.json").write_text(
        '{"note": "this system prompt discusses prompt injection and few shot examples"}'
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _CONFIG)
    generic_prompts = [
        n
        for n in doc.nodes
        if n.component_type == ComponentType.PROMPT
        and n.metadata.extras.get("adapter") == "prompt_generic"
        and any(
            ev.location and ev.location.path == "some_report.json" for ev in n.evidence
        )
    ]
    assert not generic_prompts
