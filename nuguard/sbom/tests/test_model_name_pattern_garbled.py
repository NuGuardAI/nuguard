"""Regression test for docs/sbom-accuracy-plan.md #1: the Ollama colon-tag
alternative in MODEL_NAME_PATTERNS ("<prefix>:<tag>") must not match a
self-referential "word:word" string like "chat:chat" — a shape that showed
up as a garbled MODEL node name, most likely produced somewhere upstream by
a flattened JSON key-path rather than any real model reference. No real
model is ever named identically on both sides of the colon, so this is a
generic, deterministic exclusion, not an opencode-specific patch.
"""
from __future__ import annotations

from pathlib import Path

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.adapters.registry import MODEL_NAME_PATTERNS
from nuguard.sbom.extractor.core import _strip_non_runtime_toml_model_pricing
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


def _extract_toml_models(tmp_path: Path, rel_path: str, content: str) -> set[str]:
    target = tmp_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    doc = AiSbomExtractor().extract_from_path(
        tmp_path,
        AiSbomConfig(include_extensions={".toml"}, enable_llm=False),
    )
    return {
        node.name
        for node in doc.nodes
        if node.component_type == ComponentType.MODEL
    }


def test_pricing_only_toml_model_is_absent(tmp_path: Path) -> None:
    models = _extract_toml_models(
        tmp_path,
        "config/prices.toml",
        '[pricing]\nclaude-haiku-5-5.long_context = { input = 2.0, output = 10.0 }\n',
    )

    assert models == set()


def test_runtime_default_survives_pricing_tier_without_promotion(tmp_path: Path) -> None:
    models = _extract_toml_models(
        tmp_path,
        "config/prices.toml",
        'default_model = "claude-haiku-5-5"\n'
        "[claude-haiku-5-5.long_context]\n"
        "input = 2.0\n"
        "output = 10.0\n",
    )

    assert models == {"claude-haiku-5-5"}


def test_arbitrary_runtime_toml_model_is_retained(tmp_path: Path) -> None:
    models = _extract_toml_models(
        tmp_path,
        "config/custom.toml",
        'creative = "gpt-5.4"\n',
    )

    assert models == {"gpt-5.4"}


def test_runtime_toml_registry_model_is_retained(tmp_path: Path) -> None:
    models = _extract_toml_models(
        tmp_path,
        "config/models.toml",
        '[models]\nfast = "claude-sonnet-4-6"\n',
    )

    assert models == {"claude-sonnet-4-6"}


def test_pricing_directory_does_not_suppress_nested_runtime_toml(tmp_path: Path) -> None:
    models = _extract_toml_models(
        tmp_path,
        "pricing/runtime.toml",
        '[model.gpt-5.4]\nprovider = "openai"\ncontext_window = 128000\n',
    )

    assert models == {"gpt-5.4"}


def test_runtime_table_price_field_does_not_suppress_model(tmp_path: Path) -> None:
    models = _extract_toml_models(
        tmp_path,
        "config/runtime.toml",
        "[openai.models.gpt-5.4]\nprice = 2.0\n",
    )

    assert models == {"gpt-5.4"}


def test_mixed_prices_toml_preserves_runtime_registry_rows(tmp_path: Path) -> None:
    models = _extract_toml_models(
        tmp_path,
        "config/prices.toml",
        '[model.gpt-5.4]\nprovider = "openai"\n'
        "[runtime]\n"
        "fast = { model = 'gpt-5.4', input = 120000, output = 32000 }\n",
    )

    assert models == {"gpt-5.4"}


def test_design_agent_prices_keep_default_and_drop_price_tier(tmp_path: Path) -> None:
    models = _extract_toml_models(
        tmp_path,
        "config/prices.toml",
        'default_model = "claude-haiku-5-5"\n'
        "[claude-haiku-5-5.long_context]\n"
        "input = 2.0\n"
        "output = 10.0\n"
        "cache_read_price = 0.2\n",
    )

    assert models == {"claude-haiku-5-5"}


def test_design_agent_line_15_model_comment_cannot_promote_default(tmp_path: Path) -> None:
    prefix = ['default_model = "claude-haiku-5-5"'] + [f"# note {index}" for index in range(2, 15)]
    content = "\n".join(
        prefix
        + [
            "# claude-haiku-5-5.long_context uses the higher rate",
            "[claude-haiku-5-5.long_context]",
            "input = 2.0",
            "output = 10.0",
        ]
    ) + "\n"

    models = _extract_toml_models(tmp_path, "config/prices.toml", content)

    assert models == {"claude-haiku-5-5"}


def test_pricing_comment_scrub_preserves_adjacent_comments_and_offsets() -> None:
    content = (
        "# ordinary pricing note\n"
        "# gpt-5.4-mini is priced below\n"
        "# keep this adjacent note\n"
        "[gpt-5.4-mini]\n"
        "input = 0.25\n"
        "output = 2.0\n"
    )

    scrubbed = _strip_non_runtime_toml_model_pricing(content, "config/prices.toml")
    original_lines = content.splitlines(keepends=True)
    scrubbed_lines = scrubbed.splitlines(keepends=True)

    assert scrubbed_lines[0] == original_lines[0]
    assert scrubbed_lines[1].strip() == ""
    assert scrubbed_lines[2] == original_lines[2]
    assert [len(line) for line in scrubbed_lines] == [len(line) for line in original_lines]


def test_model_comment_outside_verified_pricing_file_is_unchanged() -> None:
    content = "# runtime fallback remains gpt-5.4-mini\n"

    assert _strip_non_runtime_toml_model_pricing(content, "pricing/runtime.toml") == content


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
