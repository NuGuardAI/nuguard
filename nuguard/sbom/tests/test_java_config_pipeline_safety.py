"""Regression tests for configuration safety across the full extractor pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.adapters import workflow_exports
from nuguard.sbom.adapters.java import java_config
from nuguard.sbom.models import AiSbomDocument, is_soft_rejected
from nuguard.sbom.types import ComponentType, RelationshipType

_JAVA = """package demo;
import io.quarkiverse.langchain4j.RegisterAiService;
@RegisterAiService
public interface Assistant { String chat(String message); }
"""


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize("name", ["application.yml", "application-prod.yaml", "APPLICATION.YML"])
@pytest.mark.parametrize("flow", [False, True])
def test_yaml_credentials_stay_out_of_all_llm_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    flow: bool,
) -> None:
    from nuguard.common import llm_client

    secret = "PR665_SYNTHETIC_YAML_PASSWORD"
    if flow:
        content = (
            'spring: {datasource: {password: "' + secret + '"}, '
            'ai: {openai: {chat: {options: {model: "gpt-4o-mini"}}}}}\n'
        )
    else:
        content = (
            "spring:\n  datasource:\n    password: " + secret + "\n"
            "  ai:\n    openai:\n      chat:\n        options:\n          model: gpt-4o-mini\n"
        )
    path = "src/main/resources/" + name
    _write(tmp_path, path, content)
    _write(tmp_path, "support.py", "# password integration PR665_BENIGN_CONTEXT\nvalue = 1\n")
    prompts: list[tuple[str, str]] = []
    caches: list[dict[str, str]] = []

    class Recorder:
        token_counts = (0, 0)
        call_token_counts = (0, 0)

        def __init__(self, **kwargs: Any) -> None:
            pass

        async def complete(self, prompt: str, system: str = "", **kwargs: Any) -> str:
            prompts.append((system, prompt))
            if "## AIBOM Node to Verify" in prompt:
                return '{"verified": true, "confidence": 0.95, "reason": "fixture"}'
            return "[]"

    original = AiSbomExtractor._llm_enrich

    async def capture(
        self: AiSbomExtractor,
        doc: AiSbomDocument,
        file_contents: dict[str, str],
        config: AiSbomConfig,
    ) -> AiSbomDocument:
        caches.append(dict(file_contents))
        return await original(self, doc, file_contents, config)

    monkeypatch.setattr(llm_client, "LLMClient", Recorder)
    monkeypatch.setattr(AiSbomExtractor, "_llm_enrich", capture)
    document = AiSbomExtractor().extract_from_path(
        tmp_path,
        AiSbomConfig(
            enable_llm=True, llm_api_key="offline", llm_model="offline", gap_fill_max_calls=40
        ),
    )
    assert any("## Target category: AUTH" in p and "PR665_BENIGN_CONTEXT" in p for _, p in prompts)
    assert all(secret not in system + prompt for system, prompt in prompts)
    assert len(caches) == 1 and path not in caches[0]
    assert secret not in document.model_dump_json()
    assert any(
        n.component_type == ComponentType.MODEL and n.name == "gpt-4o-mini" for n in document.nodes
    )
    assert (tmp_path / path).read_text(encoding="utf-8") == content


def _unsafe_yaml(kind: str) -> str:
    if kind == "cycle":
        return "loop: &loop {again: *loop}\n"
    if kind == "depth":
        return "nested: " + "[" * 12 + "seed" + "]" * 12 + "\n"
    if kind == "timestamp":
        return "release_date: !!timestamp invalid\n"
    if kind == "multidocument":
        return "---\n".join(["a: b\n"] * 30)
    lines = ["level0: &level0 {value: seed}"]
    for i in range(1, 6):
        alias = f"*level{i - 1}"
        body = ", ".join([alias] * 4)
        if kind == "merge":
            body = "{<<: [" + body + "]}"
        else:
            body = "[" + body + "]"
        lines.append(f"level{i}: &level{i} {body}")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    "kind", ["sequence", "merge", "cycle", "depth", "timestamp", "multidocument"]
)
@pytest.mark.parametrize("suffix", ["yml", "yaml"])
def test_rejected_application_yaml_never_reaches_secondary_parsers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    suffix: str,
) -> None:
    bad = f"src/main/resources/application.{suffix}"
    good = "src/main/resources/application-prod.yaml"
    _write(tmp_path, bad, _unsafe_yaml(kind))
    _write(tmp_path, good, "quarkus.langchain4j.openai.chat-model.model-name: gpt-4o-mini\n")
    _write(tmp_path, "src/main/java/demo/Assistant.java", _JAVA)
    monkeypatch.setattr(java_config, "_YAML_MAX_VISITS", 64)
    monkeypatch.setattr(java_config, "_YAML_MAX_DEPTH", 8)
    downstream: list[str] = []
    original = workflow_exports.CopilotStudioYAMLAdapter.scan

    def record(self: Any, content: str, file_path: str) -> Any:
        downstream.append(file_path)
        return original(self, content, file_path)

    monkeypatch.setattr(workflow_exports.CopilotStudioYAMLAdapter, "scan", record)
    document = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    assert bad not in downstream
    assert good in downstream, "Safe YAML must retain ordinary adapter dispatch"
    assert any(
        n.component_type == ComponentType.MODEL and n.name == "gpt-4o-mini" for n in document.nodes
    )


def test_valid_aliases_and_merge_keys_remain_supported(tmp_path: Path) -> None:
    content = """defaults: &defaults
  model-name: gpt-4o-mini
quarkus:
  langchain4j:
    openai:
      chat-model:
        <<: *defaults
      streaming-chat-model: *defaults
"""
    _write(tmp_path, "src/main/resources/application.yml", content)
    _write(tmp_path, "src/main/java/demo/Assistant.java", _JAVA)
    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    models = [n for n in doc.nodes if n.component_type == ComponentType.MODEL]
    assert [n.name for n in models] == ["gpt-4o-mini"]
    assert any(e.target == models[0].id and e.derivation == "hint" for e in doc.edges)


@pytest.mark.parametrize(
    "names",
    [
        ("team-a/custom-model", "team/a-custom-model"),
        ("model.v1", "model-v1"),
        ("ModelA", "modela"),
    ],
)
@pytest.mark.parametrize("suffix", ["properties", "yaml"])
def test_exact_model_keys_are_distinct_and_stable(
    tmp_path: Path,
    names: tuple[str, str],
    suffix: str,
) -> None:
    separator = "=" if suffix == "properties" else ": "
    _write(tmp_path, "src/main/java/demo/Assistant.java", _JAVA)
    configs = [
        f"quarkus.langchain4j.openai.client{i}.chat-model.model-name{separator}{name}\n"
        for i, name in enumerate(names)
    ]

    def scan(order: list[str]) -> AiSbomDocument:
        _write(tmp_path, f"src/main/resources/application.{suffix}", "".join(order))
        # Identical declaration in another file must contribute evidence, not another node.
        _write(
            tmp_path,
            "src/main/resources/application-extra.properties",
            "quarkus.langchain4j.openai.chat-model.model-name=" + names[0] + "\n",
        )
        return AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))

    first = scan(configs)
    second = scan(list(reversed(configs)))
    for doc in (first, second):
        models = [n for n in doc.nodes if n.component_type == ComponentType.MODEL]
        assert len(models) == 2 and {n.name for n in models} == set(names)
        assert len({n.metadata.extras["canonical_name"] for n in models}) == 2
        assert all(not is_soft_rejected(n) for n in models)
        assert {n.id for n in models} <= {
            e.target
            for e in doc.edges
            if e.relationship_type == RelationshipType.USES and e.derivation == "hint"
        }
    assert {
        n.name: n.metadata.extras["canonical_name"]
        for n in first.nodes
        if n.component_type == ComponentType.MODEL
    } == {
        n.name: n.metadata.extras["canonical_name"]
        for n in second.nodes
        if n.component_type == ComponentType.MODEL
    }


def test_config_and_java_literal_for_same_model_keep_one_node_and_hints(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/main/resources/application.properties",
        "langchain4j.open-ai.chat-model.model-name=gpt-4o-mini\n",
    )
    _write(
        tmp_path,
        "src/main/java/demo/Worker.java",
        """package demo;
import dev.langchain4j.model.openai.OpenAiChatModel;
class Worker {
  Object answer(String message) {
    var model = OpenAiChatModel.builder().modelName("gpt-4o-mini").build();
    return model.generate(message);
  }
}
""",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    models = [n for n in doc.nodes if n.component_type == ComponentType.MODEL]
    assert len(models) == 1 and models[0].name == "gpt-4o-mini"
    assert {e.location.path for e in models[0].evidence if e.location} == {
        "src/main/java/demo/Worker.java",
        "src/main/resources/application.properties",
    }
    assert any(e.target == models[0].id and e.derivation == "hint" for e in doc.edges)


def test_evidence_fallback_cannot_reintroduce_config_secret() -> None:
    from nuguard.sbom.adapters.base import ComponentDetection
    from nuguard.sbom.core.verification import build_verification_prompt
    from nuguard.sbom.extractor.core import _NodeAccumulator
    from nuguard.sbom.models import Node

    nodes: dict[tuple[ComponentType, str], _NodeAccumulator] = {}
    secret = "PR665_SYNTHETIC_EVIDENCE_PASSWORD"
    AiSbomExtractor()._merge_detection(
        nodes,
        ComponentDetection(
            component_type=ComponentType.MODEL,
            canonical_name="model_test",
            display_name="test",
            adapter_name="fixture",
            priority=50,
            confidence=0.75,
            metadata={},
            file_path="src/main/resources/application.yml",
            line=1,
            snippet=f"model: test, password: {secret}",
            evidence_kind="regex",
        ),
    )
    accumulator = next(iter(nodes.values()))
    system, prompt = build_verification_prompt(
        Node(name="test", component_type=ComponentType.MODEL, confidence=0.75),
        accumulator.evidence,
    )
    assert secret not in system + prompt
    assert "application.yml" in prompt
    assert "application config" in prompt


@pytest.mark.parametrize("config_first", [False, True])
def test_equal_value_merge_retargets_typed_and_file_qualified_hints(config_first: bool) -> None:
    from nuguard.sbom.adapters.base import ComponentDetection, RelationshipHint
    from nuguard.sbom.extractor.core import _NodeAccumulator
    from nuguard.sbom.extractor.postprocess import _dedup_exact_config_models

    nodes: dict[tuple[ComponentType, str], _NodeAccumulator] = {}
    config = java_config.extract_java_config_models(
        "quarkus.langchain4j.openai.chat-model.model-name=model.v1\n",
        "application.properties",
    )[0]
    other = ComponentDetection(
        component_type=ComponentType.MODEL,
        canonical_name="vendor:model",
        display_name="model.v1",
        adapter_name="fixture",
        priority=10,
        confidence=0.95,
        metadata={"source": "code"},
        file_path="code.py",
        line=1,
        snippet="model.v1",
        evidence_kind="ast",
    )
    extractor = AiSbomExtractor()
    for det in [config, other] if config_first else [other, config]:
        extractor._merge_detection(nodes, det)
    # Reuse a model's relationships to exercise rewriting, independently of
    # graph fallbacks. Non-MODEL source identifiers must remain unchanged.
    hint = RelationshipHint(
        source_canonical="vendor:model",
        source_type=ComponentType.TOOL,
        target_canonical="vendor:model",
        target_type=ComponentType.MODEL,
        relationship_type="USES",
        target_files=(("code.py", "vendor:model"),),
    )
    nodes[(ComponentType.MODEL, "vendor_model")].relationships.append(hint)
    _dedup_exact_config_models(nodes)
    assert list(nodes) == [(ComponentType.MODEL, config.canonical_name)]
    merged = next(iter(nodes.values()))
    assert len(merged.evidence) == 2
    assert merged.metadata["config_key"]
    assert merged.confidence == 0.95
    assert merged.relationships[0].source_canonical == "vendor:model"
    assert merged.relationships[0].target_canonical == config.canonical_name
    assert merged.relationships[0].target_files == (("code.py", config.canonical_name),)
    _dedup_exact_config_models(nodes)
    assert len(merged.evidence) == 2, "Coalescing must be idempotent"
