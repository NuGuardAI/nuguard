"""PR #665: credential isolation, malformed YAML and exact-model retention."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.adapters.java.java_config import extract_java_config_models
from nuguard.sbom.extractor.core import _NodeAccumulator
from nuguard.sbom.extractor.postprocess import _collapse_bulk_catalog_files
from nuguard.sbom.models import AiSbomDocument, Evidence, SourceLocation, is_soft_rejected
from nuguard.sbom.types import ComponentType, RelationshipType

_JAVA = """package demo;
import io.quarkiverse.langchain4j.RegisterAiService;
@RegisterAiService
public interface Assistant {
    String chat(String message);
}
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize("name", ["application.properties", "application-prod.properties"])
@pytest.mark.parametrize("with_model", [False, True])
def test_properties_credentials_never_reach_llm_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    with_model: bool,
) -> None:
    from nuguard.common import llm_client

    secrets = ("PR665_FAKE_DB_PASSWORD", "PR665_FAKE_PROVIDER_KEY")
    config_path = f"src/main/resources/{name}"
    content = (
        f"spring.datasource.password={secrets[0]}\n"
        f"quarkus.langchain4j.openai.api-key={secrets[1]}\n"
    )
    if with_model:
        content += "quarkus.langchain4j.openai.chat-model.model-name=gpt-4o-mini\n"
    _write(tmp_path, config_path, content)
    _write(tmp_path, "src/main/java/demo/Assistant.java", _JAVA)
    # Keep a benign AUTH signal so the safety assertion cannot pass merely
    # because gap-fill stopped making requests altogether.
    _write(
        tmp_path,
        "support.py",
        "# password integration: PR665_BENIGN_AUTH_CONTEXT\nvalue = 1\n",
    )
    prompts: list[tuple[str, str]] = []
    caches: list[dict[str, str]] = []

    class RecordingLLM:
        token_counts = (0, 0)
        call_token_counts = (0, 0)

        def __init__(self, **kwargs: Any) -> None:
            pass

        async def complete(self, prompt: str, system: str = "", **kwargs: Any) -> str:
            prompts.append((system, prompt))
            if "## AIBOM Node to Verify" in prompt:
                return '{"verified": true, "confidence": 0.95, "reason": "fixture"}'
            return "[]"

    original_enrich = AiSbomExtractor._llm_enrich

    async def capture_cache(
        self: AiSbomExtractor,
        doc: AiSbomDocument,
        file_contents: dict[str, str],
        config: AiSbomConfig,
    ) -> AiSbomDocument:
        caches.append(dict(file_contents))
        return await original_enrich(self, doc, file_contents, config)

    # Substitute only the provider boundary; run real enrichment/prompt builders.
    monkeypatch.setattr(llm_client, "LLMClient", RecordingLLM)
    monkeypatch.setattr(AiSbomExtractor, "_llm_enrich", capture_cache)
    document = AiSbomExtractor().extract_from_path(
        tmp_path,
        AiSbomConfig(
            enable_llm=True,
            llm_api_key="offline-test-only",
            llm_model="offline-test",
            gap_fill_max_calls=40,
        ),
    )
    assert prompts, "The test must exercise the actual LLM prompt boundary"
    assert any(
        "## Target category: AUTH" in prompt and "PR665_BENIGN_AUTH_CONTEXT" in prompt
        for _, prompt in prompts
    )
    for system, prompt in prompts:
        assert all(secret not in system + prompt for secret in secrets)
    assert len(caches) == 1
    assert config_path not in caches[0]
    assert "support.py" in caches[0]
    assert all(secret not in document.model_dump_json() for secret in secrets)
    models = [node for node in document.nodes if node.component_type == ComponentType.MODEL]
    assert {node.name for node in models} == ({"gpt-4o-mini"} if with_model else set())
    if with_model:
        assert not is_soft_rejected(models[0])
        assert any(
            evidence.location and evidence.location.path == config_path
            for evidence in models[0].evidence
        )


@pytest.mark.parametrize("suffix", ["yml", "yaml"])
@pytest.mark.parametrize("invalid", ["!!timestamp invalid", "2026-02-30"])
def test_invalid_yaml_constructor_does_not_abort_other_config_files(
    tmp_path: Path,
    suffix: str,
    invalid: str,
) -> None:
    bad_content = f"release_date: {invalid}\n"
    assert extract_java_config_models(bad_content, f"application.{suffix}") == []
    _write(tmp_path, f"src/main/resources/application.{suffix}", bad_content)
    _write(
        tmp_path,
        "src/main/resources/application.properties",
        "quarkus.langchain4j.openai.chat-model.model-name=gpt-4o-mini\n",
    )
    _write(tmp_path, "src/main/java/demo/Assistant.java", _JAVA)
    document = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    models = [node for node in document.nodes if node.component_type == ComponentType.MODEL]
    assert {node.name for node in models} == {"gpt-4o-mini"}
    assert not is_soft_rejected(models[0])
    assert any(
        edge.target == models[0].id
        and edge.relationship_type == RelationshipType.USES
        and edge.derivation == "hint"
        for edge in document.edges
    )


@pytest.mark.parametrize("suffix", ["properties", "yml"])
@pytest.mark.parametrize("count", [15, 16, 20])
def test_all_exact_config_models_remain_effective_and_linked(
    tmp_path: Path,
    suffix: str,
    count: int,
) -> None:
    names = {f"configured-model-{index:02d}" for index in range(count)}
    separator = "=" if suffix == "properties" else ": "
    content = "".join(
        f"quarkus.langchain4j.openai.client{index}.chat-model.model-name"
        f"{separator}configured-model-{index:02d}\n"
        for index in range(count)
    )
    _write(tmp_path, f"src/main/resources/application.{suffix}", content)
    _write(tmp_path, "src/main/java/demo/Assistant.java", _JAVA)
    config = AiSbomConfig(enable_llm=False)
    assert config.bulk_catalog_threshold == 15
    assert config.bulk_catalog_keep == 3
    document = AiSbomExtractor().extract_from_path(tmp_path, config)
    models = [node for node in document.nodes if node.component_type == ComponentType.MODEL]
    assert {node.name for node in models} == names
    assert all(node.metadata.extras.get("config_key") for node in models)
    assert all(not is_soft_rejected(node) for node in models)
    assert all("bulk_catalog_summary" not in node.metadata.extras for node in models)
    assert document.summary is not None
    assert document.summary.node_counts.get("MODEL") == count
    assert document.summary.node_counts_soft_rejected.get("MODEL", 0) == 0
    agent = next(
        node
        for node in document.nodes
        if node.component_type == ComponentType.AGENT
        and any(
            evidence.location and evidence.location.path.endswith("Assistant.java")
            for evidence in node.evidence
        )
    )
    linked = {
        edge.target
        for edge in document.edges
        if edge.source == agent.id
        and edge.relationship_type == RelationshipType.USES
        and edge.derivation == "hint"
    }
    assert {node.id for node in models} <= linked


def test_bulk_catalog_still_truncates_non_config_models() -> None:
    node_map: dict[tuple[ComponentType, str], _NodeAccumulator] = {}
    for exact in (False, True):
        for index in range(16):
            name = f"{'exact' if exact else 'heuristic'}_{index:02d}"
            node_map[(ComponentType.MODEL, name)] = _NodeAccumulator(
                component_type=ComponentType.MODEL,
                canonical_name=name,
                display_name=name,
                adapter_name="java_config",
                priority=20,
                confidence=0.9,
                metadata={"config_key": f"client{index}.model-name"} if exact else {},
                evidence=[
                    Evidence(
                        kind="regex",
                        confidence=0.9,
                        detail="fixture",
                        location=SourceLocation(path="application.properties", line=index + 1),
                    )
                ],
            )
    _collapse_bulk_catalog_files(node_map, threshold=15, keep=3)
    exact_nodes = [acc for acc in node_map.values() if acc.metadata.get("config_key")]
    heuristic_nodes = [acc for acc in node_map.values() if not acc.metadata.get("config_key")]
    assert len(exact_nodes) == len(heuristic_nodes) == 16
    assert all("bulk_catalog_truncated" not in acc.metadata for acc in exact_nodes)
    assert all("bulk_catalog_summary" not in acc.metadata for acc in exact_nodes)
    assert sum(bool(acc.metadata.get("bulk_catalog_truncated")) for acc in heuristic_nodes) == 13
