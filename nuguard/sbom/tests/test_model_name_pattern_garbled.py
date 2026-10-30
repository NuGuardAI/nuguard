"""Regression test for docs/sbom-accuracy-plan.md #1: the Ollama colon-tag
alternative in MODEL_NAME_PATTERNS ("<prefix>:<tag>") must not match a
self-referential "word:word" string like "chat:chat" — a shape that showed
up as a garbled MODEL node name, most likely produced somewhere upstream by
a flattened JSON key-path rather than any real model reference. No real
model is ever named identically on both sides of the colon, so this is a
generic, deterministic exclusion, not an opencode-specific patch.
"""
from __future__ import annotations

from nuguard.sbom.adapters.registry import MODEL_NAME_PATTERNS
from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.types import ComponentType


_JSON_ONLY = AiSbomConfig(include_extensions={".json"}, enable_llm=False)
_SH_ONLY = AiSbomConfig(include_extensions={".sh"}, enable_llm=False)


def _matches(text: str) -> bool:
    return any(p.search(text) for p in MODEL_NAME_PATTERNS)


def test_self_referential_chat_chat_not_matched():
    assert not _matches("chat:chat")


def test_self_referential_mini_mini_not_matched():
    assert not _matches("mini:mini")


def test_self_referential_instruct_instruct_not_matched():
    assert not _matches("instruct:instruct")


def test_legitimate_ollama_tag_still_matched():
    assert _matches("mistral:7b")
    assert _matches("phi:mini")
    assert _matches("qwen:instruct")


def test_cross_word_tag_still_matched():
    # Different words on each side are not self-referential and remain valid.
    assert _matches("hermes:chat")


def _model_names(doc) -> set[str]:
    return {node.name.lower() for node in doc.nodes if node.component_type == ComponentType.MODEL}


def test_base64_like_blob_containing_o3_does_not_emit_model(tmp_path):
    encoded = "A" * 80 + "/O3+" + "B" * 80
    (tmp_path / "artifact.json").write_text(
        '{"image": "' + encoded + '"}\n',
        encoding="utf-8",
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, _JSON_ONLY)

    assert _model_names(doc) == set()


def test_real_o3_outside_base64_like_blob_is_retained(tmp_path):
    encoded = "A" * 80 + "/O3+" + "B" * 80
    (tmp_path / "app.json").write_text(
        '{"image": "' + encoded + '", "model": "o3"}\n',
        encoding="utf-8",
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, _JSON_ONLY)

    assert _model_names(doc) == {"o3"}


def test_generated_skills_lock_json_does_not_emit_model(tmp_path):
    (tmp_path / "skills-lock.json").write_text(
        '{"skill": "dolly-zoom"}\n',
        encoding="utf-8",
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, _JSON_ONLY)

    assert _model_names(doc) == set()


def test_shell_deployment_detection_is_preserved(tmp_path):
    (tmp_path / "deploy.sh").write_text(
        "#!/bin/sh\nkubectl apply -f deployment.yaml\n",
        encoding="utf-8",
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH_ONLY)

    assert any(node.component_type == ComponentType.DEPLOYMENT for node in doc.nodes)
