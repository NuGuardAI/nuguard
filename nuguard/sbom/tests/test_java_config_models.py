from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.adapters.java.java_config import (
    extract_java_config_models,
    is_java_app_config_name,
    link_config_models,
)
from nuguard.sbom.types import ComponentType

_ASSISTANT = """package demo;
import dev.langchain4j.service.AiServices;
public class Assistant {
  Object build() { return AiServices.create(Object.class); }
}
"""


def _models(content: str, path: str = "src/main/resources/application.properties") -> list[tuple]:
    return [
        (d.display_name, d.line, d.metadata["provider"], d.metadata["framework"])
        for d in extract_java_config_models(content, path)
    ]


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_config_name_matches_application_files_only() -> None:
    assert is_java_app_config_name("application.properties")
    assert is_java_app_config_name("application-prod.yml")
    assert is_java_app_config_name("application.yaml")
    assert not is_java_app_config_name("gradle.properties")
    assert not is_java_app_config_name("log4j.properties")


def test_config_langchain4j_properties_key() -> None:
    content = "langchain4j.open-ai.chat-model.api-key=${OPENAI_API_KEY}\n"
    content += "langchain4j.open-ai.chat-model.model-name=gpt-4o-mini\n"
    assert _models(content) == [("gpt-4o-mini", 2, "openai", "langchain4j")]


def test_config_quarkus_key_with_profile_prefix_and_named_model() -> None:
    content = "%prod.quarkus.langchain4j.openai.chat-model.model-name=gpt-4o\n"
    content += "quarkus.langchain4j.azure-openai.mine.chat-model.deployment-name = dep1\n"
    assert _models(content) == [
        ("gpt-4o", 1, "openai", "quarkus-langchain4j"),
        ("dep1", 2, "azure-openai", "quarkus-langchain4j"),
    ]


def test_config_spring_ai_yaml_uses_env_default_and_camel_case() -> None:
    content = """spring:
  ai:
    openai:
      chat:
        options:
          model: ${OPENAI_MODEL:gpt-4o}
langchain4j:
  anthropic:
    chatModel:
      modelName: claude-3-5-sonnet
"""
    assert _models(content, "application.yml") == [
        ("gpt-4o", 6, "openai", "spring-ai"),
        ("claude-3-5-sonnet", 10, "anthropic", "langchain4j"),
    ]


def test_config_placeholder_without_default_and_other_keys_are_ignored() -> None:
    content = "langchain4j.open-ai.chat-model.model-name=${OPENAI_MODEL}\n"
    content += "langchain4j.open-ai.chat-model.temperature=0.0\n"
    content += "# langchain4j.open-ai.chat-model.model-name=gpt-4o\n"
    assert _models(content) == []


def test_config_link_is_scoped_to_matching_framework_and_source_root() -> None:
    detection = extract_java_config_models(
        "langchain4j.open-ai.chat-model.model-name=gpt-4o-mini\n",
        "app/src/main/resources/application.properties",
    )[0]

    def agent(path: str, framework: str) -> SimpleNamespace:
        return SimpleNamespace(
            component_type=ComponentType.AGENT,
            canonical_name=path,
            metadata={"framework": framework},
            evidence=[SimpleNamespace(location=SimpleNamespace(path=path))],
            relationships=[],
        )

    inside = agent("app/src/main/java/A.java", "langchain4j")
    test_src = agent("app/src/test/java/ATest.java", "langchain4j")
    other_module = agent("other/src/main/java/B.java", "langchain4j")
    other_fw = agent("app/src/main/java/C.java", "spring-boot")
    node_map = {
        (ComponentType.MODEL, detection.canonical_name): SimpleNamespace(
            component_type=ComponentType.MODEL
        ),
        (ComponentType.AGENT, "a"): inside,
        (ComponentType.AGENT, "b"): test_src,
        (ComponentType.AGENT, "c"): other_module,
        (ComponentType.AGENT, "d"): other_fw,
    }

    assert link_config_models(node_map, [detection]) == 1
    assert link_config_models(node_map, [detection]) == 0  # idempotent
    assert [h.target_canonical for h in inside.relationships] == [detection.canonical_name]
    assert not (test_src.relationships or other_module.relationships or other_fw.relationships)


def test_extractor_reads_model_from_application_properties(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/demo/Assistant.java", _ASSISTANT)
    _write(
        tmp_path,
        "src/main/resources/application.properties",
        "langchain4j.open-ai.chat-model.model-name=gpt-4o-mini\n",
    )

    document = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))

    models = [n for n in document.nodes if n.component_type == ComponentType.MODEL]
    assert [m.name for m in models] == ["gpt-4o-mini"]
    location = models[0].evidence[0].location
    assert location.path.endswith("application.properties")
    assert location.line == 1
    agent = next(
        n
        for n in document.nodes
        if n.component_type == ComponentType.AGENT
        and any(ev.location.path.endswith("Assistant.java") for ev in n.evidence)
    )
    assert any(
        e.source == agent.id
        and e.target == models[0].id
        and getattr(e.relationship_type, "value", e.relationship_type) == "USES"
        and e.derivation == "hint"
        for e in document.edges
    )


def test_extractor_links_yaml_model_only_to_agents_of_the_same_module(tmp_path: Path) -> None:
    _write(tmp_path, "app/src/main/java/demo/Assistant.java", _ASSISTANT)
    other = _ASSISTANT.replace("Assistant", "Other")
    _write(tmp_path, "other/src/main/java/demo/Other.java", other)
    _write(
        tmp_path,
        "app/src/main/resources/application.yml",
        "langchain4j:\n  open-ai:\n    chat-model:\n"
        "      model-name: ${OPENAI_MODEL:gpt-4o-mini}\n",
    )

    document = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))

    model = next(n for n in document.nodes if n.component_type == ComponentType.MODEL)

    def hinted(file_name: str) -> bool:
        agent = next(
            n
            for n in document.nodes
            if n.component_type == ComponentType.AGENT
            and any(ev.location.path.endswith(file_name) for ev in n.evidence)
        )
        return any(
            e.source == agent.id and e.target == model.id and e.derivation == "hint"
            for e in document.edges
        )

    assert hinted("Assistant.java")
    assert not hinted("Other.java")


def test_extractor_ignores_unresolvable_placeholder(tmp_path: Path) -> None:
    _write(tmp_path, "src/main/java/demo/Assistant.java", _ASSISTANT)
    _write(
        tmp_path,
        "src/main/resources/application.properties",
        "langchain4j.open-ai.chat-model.model-name=${OPENAI_MODEL}\n",
    )

    document = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))

    assert not [n for n in document.nodes if n.component_type == ComponentType.MODEL]


def test_config_quarkus_quoted_named_model_segment() -> None:
    content = 'quarkus.langchain4j.azure-openai."my-model".chat-model.deployment-name=dep1\n'

    assert _models(content) == [
        ("dep1", 1, "azure-openai", "quarkus-langchain4j"),
    ]


def test_config_properties_accepts_whitespace_separator() -> None:
    content = "langchain4j.open-ai.chat-model.model-name gpt-4o-mini\n"

    assert _models(content) == [
        ("gpt-4o-mini", 1, "openai", "langchain4j"),
    ]


def test_config_yaml_skips_recursive_aliases() -> None:
    content = """mapping-loop: &mapping-loop
  again: *mapping-loop
list-loop: &list-loop
  - *list-loop
langchain4j:
  open-ai:
    chat-model:
      model-name: gpt-4o
"""

    assert _models(content, "application.yml") == [
        ("gpt-4o", 8, "openai", "langchain4j"),
    ]


def test_config_yaml_uses_path_aware_marks_for_substrings_and_dotted_keys() -> None:
    content = """spring:
  ai:
    openai:
      chat:
        options:
          model: chatgpt-4o-latest
---
"spring.ai.openai.chat.options.model": gpt-4o
"""

    assert _models(content, "application.yml") == [
        ("chatgpt-4o-latest", 6, "openai", "spring-ai"),
        ("gpt-4o", 8, "openai", "spring-ai"),
    ]


def test_config_yaml_uses_path_aware_marks_for_repeated_values() -> None:
    content = """other:
  model: gpt-4o
"spring.ai.openai.chat.options.model": gpt-4o
"""

    assert _models(content, "application.yml") == [
        ("gpt-4o", 3, "openai", "spring-ai"),
    ]


def test_config_yaml_preserves_merge_keys_and_source_marks() -> None:
    content = """defaults: &defaults
  chat-model:
    model-name: gpt-4o
langchain4j:
  open-ai:
    <<: *defaults
"""

    assert _models(content, "application.yml") == [
        ("gpt-4o", 3, "openai", "langchain4j"),
    ]


@pytest.mark.parametrize(
    ("config_path", "content"),
    [
        (
            "src/main/resources/application.properties",
            "langchain4j.open-ai.chat-model.model-name=gpt-4o\n"
            "langchain4j.open-ai.streaming-chat-model.model-name=gpt-4o-mini\n",
        ),
        (
            "src/main/resources/application.yml",
            "langchain4j: {open-ai: {chat-model: {model-name: gpt-4o}, "
            "streaming-chat-model: {model-name: gpt-4o-mini}}}\n",
        ),
    ],
)
def test_extractor_preserves_distinct_exact_prefix_models(
    tmp_path: Path,
    config_path: str,
    content: str,
) -> None:
    _write(tmp_path, "src/main/java/demo/Assistant.java", _ASSISTANT)
    _write(tmp_path, config_path, content)

    document = AiSbomExtractor().extract_from_path(
        tmp_path,
        AiSbomConfig(enable_llm=False),
    )

    models = {
        node.name: node for node in document.nodes if node.component_type == ComponentType.MODEL
    }
    assert set(models) == {"gpt-4o", "gpt-4o-mini"}

    agent = next(
        node
        for node in document.nodes
        if node.component_type == ComponentType.AGENT
        and any(
            evidence.location is not None and evidence.location.path.endswith("Assistant.java")
            for evidence in node.evidence
        )
    )

    hint_targets = {
        edge.target
        for edge in document.edges
        if edge.source == agent.id and edge.derivation == "hint"
    }
    assert {
        models["gpt-4o"].id,
        models["gpt-4o-mini"].id,
    } <= hint_targets
