"""PR #665: CloudFormation tags, dotted clients and AST-backed model identity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.adapters import iac, workflow_exports
from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.java import java_config
from nuguard.sbom.extractor.core import _NodeAccumulator
from nuguard.sbom.extractor.postprocess import (
    _dedup_by_location,
    _dedup_by_name_prefix,
    _dedup_exact_config_models,
)
from nuguard.sbom.models import is_soft_rejected
from nuguard.sbom.types import ComponentType, RelationshipType

_CFN = """AWSTemplateFormatVersion: "2010-09-09"
Description: !Sub "${AWS::StackName}-application"
Metadata:
  Components: !Join [",", [service, worker]]
Resources:
  Role:
    Type: AWS::IAM::Role
    Properties:
      RoleName: worker-role
      AssumeRolePolicyDocument:
        Statement:
          - Effect: Allow
            Principal: {Service: lambda.amazonaws.com}
            Action: sts:AssumeRole
  Function:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: worker
      Runtime: python3.12
      Role: !GetAtt Role.Arn
      Environment:
        Variables: {STAGE: !Ref Stage}
"""
_JAVA_SERVICE = """package demo;
import io.quarkiverse.langchain4j.RegisterAiService;
@RegisterAiService
public interface Assistant { String chat(String message); }
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    "filename", ["application.yaml", "application.yml", "application-prod.yaml"]
)
def test_cloudformation_application_file_keeps_deployment_iam_and_workload(
    tmp_path: Path,
    filename: str,
) -> None:
    def scan(name: str) -> set[tuple[ComponentType, str]]:
        root = tmp_path / name
        _write(root, name, _CFN)
        doc = AiSbomExtractor().extract_from_path(root, AiSbomConfig(enable_llm=False))
        cfn = [
            node for node in doc.nodes if node.metadata.extras.get("iac_format") == "cloudformation"
        ]
        assert any(node.component_type == ComponentType.IAM for node in cfn)
        assert any(node.component_type == ComponentType.DEPLOYMENT for node in cfn)
        assert any(node.metadata.workload for node in cfn)
        assert all(not is_soft_rejected(node) for node in cfn)
        return {(node.component_type, node.name) for node in cfn}

    assert scan(filename) == scan("stack.yaml")


@pytest.mark.parametrize(
    "tagged",
    [
        "!Ref Stage",
        '!Sub "${AWS::StackName}-application"',
        "!GetAtt Role.Arn",
        "!GetAtt [Role, Arn]",
        "!Join [',', [one, two]]",
        "!If [Enabled, yes, no]",
        "!Sub {Name: value}",
    ],
)
def test_preflight_accepts_iac_tags_without_changing_java_or_global_loaders(tagged: str) -> None:
    content = "key: " + tagged + "\n"
    assert java_config.application_yaml_allows_scan(content, "application.yaml")
    # The IaC loader's opaque constructor is used only for preflight. A Ref
    # below a model setting must not become a supposedly resolved model name.
    model_content = "quarkus.langchain4j.openai.chat-model.model-name: " + tagged + "\n"
    assert java_config.extract_java_config_models(model_content, "application.yaml") == []
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.safe_load(content)


@pytest.mark.parametrize("kind", ["cycle", "depth", "aliases", "merge", "python-tag", "timestamp"])
def test_intrinsic_tags_do_not_bypass_preflight_limits_or_secondary_parser_exclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    if kind == "cycle":
        payload = "data: &loop !Join [*loop]\n"
    elif kind == "depth":
        payload = "data: !Join " + "[" * 12 + "seed" + "]" * 12 + "\n"
    elif kind in {"aliases", "merge"}:
        # Aliases are small on the wire, but expensive if revisited unboundedly.
        lines = ["base0: &base0 {value: seed}"]
        for level in range(1, 5):
            refs = ", ".join([f"*base{level - 1}"] * 4)
            body = "{<<: [" + refs + "]}" if kind == "merge" else "[" + refs + "]"
            lines.append(f"base{level}: &base{level} {body}")
        payload = "\n".join(lines) + "\ndata: !Join [',', *base4]\n"
    elif kind == "python-tag":
        payload = "data: !!python/object/apply:builtins.str [seed]\n"
    else:
        payload = "release_date: !!timestamp invalid\n"
    bad = "application.yaml"
    good = "application-prod.yaml"
    _write(tmp_path, bad, _CFN + "\n" + payload)
    _write(tmp_path, good, "spring.ai.openai.chat.options.model: local-chat\n")
    monkeypatch.setattr(java_config, "_YAML_MAX_VISITS", 250)
    monkeypatch.setattr(java_config, "_YAML_MAX_DEPTH", 10)
    seen: list[tuple[str, str]] = []
    for cls in (iac.CloudFormationAdapter, workflow_exports.CopilotStudioYAMLAdapter):
        original = cls.scan

        def record(
            self: Any,
            content: str,
            file_path: str,
            _original: Any = original,
        ) -> Any:
            seen.append((self.name, file_path))
            return _original(self, content, file_path)

        monkeypatch.setattr(cls, "scan", record)
    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    assert not any(path == bad for _, path in seen)
    assert {name for name, path in seen if path == good} == {
        iac.CloudFormationAdapter.name,
        workflow_exports.CopilotStudioYAMLAdapter.name,
    }
    assert any(
        n.component_type == ComponentType.MODEL and n.name == "local-chat" for n in doc.nodes
    )


def test_preflight_still_supports_valid_aliases_and_merges_with_tags() -> None:
    content = """defaults: &defaults
  environment: prod
copy:
  <<: *defaults
  name: !Sub "${AWS::StackName}"
other: *defaults
"""
    assert java_config.application_yaml_allows_scan(content, "application.yaml")


@pytest.mark.parametrize("layout", ["nested", "dotted-prefix", "mixed-prefix", "profile"])
@pytest.mark.parametrize("client", ["tenant.one", "tenant.one.eu"])
@pytest.mark.parametrize("streaming", [False, True])
def test_nested_dotted_quarkus_clients_keep_their_segment_and_source_line(
    layout: str,
    client: str,
    streaming: bool,
) -> None:
    group = "streaming-chat-model" if streaming else "chat-model"
    provider: dict[str, Any] = {client: {group: {"model-name": "local-chat"}}}
    data: dict[str, Any]
    if layout == "dotted-prefix":
        data = {"quarkus.langchain4j.openai": provider}
    elif layout == "mixed-prefix":
        data = {"quarkus": {"langchain4j.openai": provider}}
    else:
        data = {"quarkus": {"langchain4j": {"openai": provider}}}
        if layout == "profile":
            data = {"%prod,staging": data}
    content = yaml.safe_dump(data, sort_keys=False)
    detections = java_config.extract_java_config_models(content, "application.yml")
    assert len(detections) == 1
    detection = detections[0]
    assert detection.display_name == "local-chat"
    assert (
        detection.metadata["config_key"]
        == f'quarkus.langchain4j.openai."{client}".{group}.model-name'
    )
    assert detection.line == next(
        i for i, line in enumerate(content.splitlines(), 1) if "model-name:" in line
    )


@pytest.mark.parametrize(
    "content",
    [
        "quarkus.langchain4j.openai.chat-model.model-name: local-chat\n",
        'quarkus.langchain4j.openai."tenant.one".chat-model.model-name: local-chat\n',
        "quarkus.langchain4j.openai:\n  chat-model.model-name: local-chat\n",
        "quarkus.langchain4j.openai:\n  tenant.chat-model.model-name: local-chat\n",
        "quarkus.langchain4j.openai:\n  tenant.chat-model:\n    model-name: local-chat\n",
        "quarkus.langchain4j.openai:\n  '\"tenant.one\"':\n    chat-model.model-name: local-chat\n",
        "spring.ai.openai.chat.options.model: local-chat\n",
        "quarkus.langchain4j.openai:\n  tenant.one:\n    chat-model.model-name: local-chat\n",
    ],
)
def test_intentionally_dotted_keys_are_not_quoted_as_whole_paths(content: str) -> None:
    detections = java_config.extract_java_config_models(content, "application.yml")
    assert [d.display_name for d in detections] == ["local-chat"]


@pytest.mark.parametrize("quoted", [False, True])
def test_dotted_named_clients_keep_scoped_model_hints(tmp_path: Path, quoted: bool) -> None:
    client = '"tenant.one"' if quoted else "tenant.one"
    content = (
        "defaults: &defaults {model-name: local-chat}\n"
        "quarkus:\n  langchain4j:\n    openai:\n"
        f"      {client}:\n        chat-model: *defaults\n"
    )
    _write(tmp_path, "one/src/main/resources/application.yml", content)
    _write(tmp_path, "one/src/main/java/demo/Assistant.java", _JAVA_SERVICE)
    _write(
        tmp_path, "two/src/main/java/demo/Other.java", _JAVA_SERVICE.replace("Assistant", "Other")
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    model = next(
        n for n in doc.nodes if n.component_type == ComponentType.MODEL and n.name == "local-chat"
    )
    hinted = {
        edge.source
        for edge in doc.edges
        if edge.target == model.id
        and edge.derivation == "hint"
        and edge.relationship_type == RelationshipType.USES
    }
    agents = [n for n in doc.nodes if n.component_type == ComponentType.AGENT]
    assert {n.name for n in agents if n.id in hinted} == {"Assistant"}


@pytest.mark.parametrize("names", [("chat", "local-chat"), ("gpt-4o", "gpt-4o-mini")])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("suffix", ["properties", "yaml"])
def test_config_merge_preserves_distinct_java_models_on_the_same_line(
    tmp_path: Path,
    names: tuple[str, str],
    reverse: bool,
    suffix: str,
) -> None:
    first, second = reversed(names) if reverse else names
    java = """package demo;
import dev.langchain4j.model.openai.OpenAiChatModel;
public class Worker {
  Object answer(String message) {
    var a = OpenAiChatModel.builder().modelName("FIRST").build(); var b = OpenAiChatModel.builder().modelName("SECOND").build();
    return a.generate(message);
  }
}
""".replace("FIRST", first).replace("SECOND", second)
    source = "src/main/java/demo/Worker.java"
    config_path = f"src/main/resources/application.{suffix}"
    sep = "=" if suffix == "properties" else ": "
    _write(tmp_path, source, java)
    _write(
        tmp_path, config_path, "langchain4j.open-ai.chat-model.model-name" + sep + names[1] + "\n"
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    models = {n.name: n for n in doc.nodes if n.component_type == ComponentType.MODEL}
    assert set(models) == set(names)
    assert all(not is_soft_rejected(n) for n in models.values())
    for name in names:
        assert any(
            e.kind.startswith("ast") and e.location and e.location.path == source
            for e in models[name].evidence
        )
        assert any(
            edge.target == models[name].id and edge.derivation == "hint" for edge in doc.edges
        )
    assert {e.location.path for e in models[names[1]].evidence if e.location} >= {
        source,
        config_path,
    }


@pytest.mark.parametrize("kinds", [("regex",), ("ast",), ("ast", "regex")])
@pytest.mark.parametrize("same_line", [False, True])
def test_config_absorbs_regex_only_truncations_not_structured_detections(
    kinds: tuple[str, ...],
    same_line: bool,
) -> None:
    extractor = AiSbomExtractor()
    node_map: dict[tuple[ComponentType, str], _NodeAccumulator] = {}
    config = java_config.extract_java_config_models(
        "langchain4j.open-ai.chat-model.model-name=gpt-4o-mini\n",
        "application.properties",
    )[0]
    extractor._merge_detection(node_map, config)
    for name, evidence_kinds, line in [
        ("gpt-4o-mini", ("ast",), 5),
        ("gpt-4o", kinds, 5 if same_line else 4),
    ]:
        for kind in evidence_kinds:
            extractor._merge_detection(
                node_map,
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name="model:" + name,
                    display_name=name,
                    adapter_name="fixture",
                    priority=20,
                    confidence=0.9,
                    metadata={},
                    file_path="Worker.java",
                    line=line,
                    snippet=name,
                    evidence_kind=kind,
                ),
            )
    _dedup_exact_config_models(node_map)
    _dedup_by_location(node_map)
    _dedup_by_name_prefix(node_map)
    expected = {"gpt-4o-mini"} if kinds == ("regex",) else {"gpt-4o", "gpt-4o-mini"}
    assert {acc.display_name for acc in node_map.values()} == expected
    winner = node_map[(ComponentType.MODEL, config.canonical_name)]
    if kinds == ("regex",):
        assert len(winner.evidence) == 3
    else:
        assert len(winner.evidence) == 2
