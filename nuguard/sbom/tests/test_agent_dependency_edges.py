"""Regression coverage for unsupported agent dependency edges (issue #662)."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.adapters.base import RelationshipHint
from nuguard.sbom.extractor.core import _NodeAccumulator
from nuguard.sbom.models import AiSbomDocument, Edge, Evidence, Node, NodeMetadata, SourceLocation
from nuguard.sbom.normalization import canonicalize_text
from nuguard.sbom.types import ComponentType, RelationshipType


def _node(name: str, kind: ComponentType, path: str | None, *, controller: bool = False) -> Node:
    return Node(
        name=name,
        component_type=kind,
        confidence=0.9,
        metadata=NodeMetadata(
            framework="fixture",
            extras={
                "canonical_name": canonicalize_text(f"{kind.value}:{name}"),
                "web_controller": controller,
            },
        ),
        evidence=[
            Evidence(
                kind="ast",
                confidence=0.9,
                detail="fixture",
                location=SourceLocation(path=path, line=1),
            )
        ]
        if path is not None
        else [],
    )


def _hint(source: Node, target: Node, relation: RelationshipType) -> RelationshipHint:
    # Raw colon-form hints must still resolve against normalized node names.
    return RelationshipHint(
        source_canonical=f"{source.component_type.value}:{source.name}",
        source_type=source.component_type,
        target_canonical=f"{target.component_type.value}:{target.name}",
        target_type=target.component_type,
        relationship_type=relation.value,
        access_type="read" if relation == RelationshipType.ACCESSES else None,
    )


def _resolve(items: list[Node], hints: tuple[RelationshipHint, ...] = ()) -> AiSbomDocument:
    accumulators: dict[tuple[ComponentType, str], _NodeAccumulator] = {}
    for node in items:
        canonical = str(node.metadata.extras["canonical_name"])
        accumulators[(node.component_type, canonical)] = _NodeAccumulator(
            component_type=node.component_type,
            canonical_name=canonical,
            display_name=node.name,
            adapter_name="fixture",
            priority=10,
            confidence=node.confidence,
        )
    next(iter(accumulators.values())).relationships.extend(hints)
    doc = AiSbomDocument(target="fixture", nodes=items)
    AiSbomExtractor()._resolve_edges(doc, accumulators)
    return doc


def _edge_keys(doc: AiSbomDocument) -> set[tuple[UUID, UUID, RelationshipType]]:
    return {(edge.source, edge.target, edge.relationship_type) for edge in doc.edges}


@pytest.mark.parametrize(
    "path",
    [
        "src/main/java/demo/Agent.java",
        "app/agent.py",
        "src/agent.ts",
        "cmd/agent.go",
        "App/Agent.cs",
        None,
    ],
)
@pytest.mark.parametrize("controller", [False, True])
def test_coexistence_does_not_establish_agent_dependencies(
    path: str | None,
    controller: bool,
) -> None:
    agent = _node("Agent", ComponentType.AGENT, path, controller=controller)
    # Shared file, framework and even a single agent are not relationship evidence.
    tools = [_node(f"Tool{i}", ComponentType.TOOL, path) for i in range(7)]
    models = [_node(f"Model{i}", ComponentType.MODEL, path) for i in range(4)]
    items = [agent, *tools, *models]
    doc = _resolve(items)
    assert doc.edges == []
    assert doc.nodes == items


@pytest.mark.parametrize(
    "target_kind,relation",
    [
        (ComponentType.TOOL, RelationshipType.CALLS),
        (ComponentType.MODEL, RelationshipType.USES),
    ],
)
@pytest.mark.parametrize("controller", [False, True])
def test_supported_edge_does_not_invent_the_other_dependency_kind(
    target_kind: ComponentType,
    relation: RelationshipType,
    controller: bool,
) -> None:
    agent = _node("Agent", ComponentType.AGENT, "module-a/agent.py", controller=controller)
    tool = _node("Tool", ComponentType.TOOL, "module-b/tool.py")
    model = _node("Model", ComponentType.MODEL, "module-c/model.py")
    target = tool if target_kind == ComponentType.TOOL else model
    hint = _hint(agent, target, relation)
    doc = _resolve([agent, tool, model], (hint, hint))
    assert _edge_keys(doc) == {(agent.id, target.id, relation)}
    assert len(doc.edges) == 1
    assert doc.edges[0].derivation == "hint"
    assert doc.edges[0].confidence is None
    assert AiSbomDocument.model_validate(doc.model_dump(mode="json")) == doc


@pytest.mark.parametrize("missing_source", [False, True])
def test_unresolved_hint_does_not_trigger_replacement_edges(missing_source: bool) -> None:
    agent = _node("Agent", ComponentType.AGENT, "agent.py")
    tool = _node("Tool", ComponentType.TOOL, "tool.py")
    missing = _node("Missing", ComponentType.AGENT if missing_source else ComponentType.TOOL, None)
    hint = (
        _hint(missing, tool, RelationshipType.CALLS)
        if missing_source
        else _hint(
            agent,
            missing,
            RelationshipType.CALLS,
        )
    )
    assert _resolve([agent, tool], (hint,)).edges == []


def test_controller_keeps_delegation_without_inheriting_worker_dependencies() -> None:
    endpoint = _node("Endpoint", ComponentType.API_ENDPOINT, "web/Controller.java")
    router = _node("Router", ComponentType.AGENT, "web/Controller.java", controller=True)
    worker = _node("Worker", ComponentType.AGENT, "service/Worker.java")
    tool = _node("Tool", ComponentType.TOOL, "tools/Tool.java")
    model = _node("Model", ComponentType.MODEL, "models/Model.java")
    datastore = _node("Database", ComponentType.DATASTORE, "data/Store.java")
    hints = (
        _hint(endpoint, router, RelationshipType.CALLS),
        _hint(router, worker, RelationshipType.CALLS),
        _hint(worker, tool, RelationshipType.CALLS),
        _hint(worker, model, RelationshipType.USES),
        _hint(tool, datastore, RelationshipType.ACCESSES),
    )
    doc = _resolve([endpoint, router, worker, tool, model, datastore], hints)
    assert _edge_keys(doc) == {
        (endpoint.id, router.id, RelationshipType.CALLS),
        (router.id, worker.id, RelationshipType.CALLS),
        (worker.id, tool.id, RelationshipType.CALLS),
        (worker.id, model.id, RelationshipType.USES),
        (tool.id, datastore.id, RelationshipType.ACCESSES),
        (worker.id, datastore.id, RelationshipType.ACCESSES),
    }
    direct = [edge for edge in doc.edges if edge.source == tool.id]
    assert len(direct) == 1 and direct[0].access_type == "read"
    assert len(doc.edges) == 6


@pytest.mark.parametrize("reverse", [False, True])
def test_unrelated_names_confidence_and_order_cannot_change_dependencies(reverse: bool) -> None:
    agent = _node("Agent", ComponentType.AGENT, "app/agent.py")
    unrelated_agent = _node("OtherAgent", ComponentType.AGENT, "other/agent.py")
    tool = _node("UsedTool", ComponentType.TOOL, "tools/used.py")
    model = _node("UsedModel", ComponentType.MODEL, "models/used.py")
    hints = (_hint(agent, tool, RelationshipType.CALLS), _hint(agent, model, RelationshipType.USES))
    items = [agent, unrelated_agent, tool, model]
    expected = _edge_keys(_resolve(items, hints))
    extras = [
        _node(f"Unused{i}", kind, "other/catalog.py")
        for kind in (ComponentType.TOOL, ComponentType.MODEL)
        for i in range(7)
    ]
    for prefix, confidence in (("AAA", 1.0), ("ZZZ", 0.1)):
        for index, extra in enumerate(extras):
            extra.name = f"{prefix}{index}"
            extra.confidence = confidence
        expanded = [*items, *extras]
        if reverse:
            expanded.reverse()
        assert _edge_keys(_resolve(expanded, hints)) == expected


@pytest.mark.parametrize(
    "target_kind,relation",
    [
        (ComponentType.TOOL, RelationshipType.CALLS),
        (ComponentType.MODEL, RelationshipType.USES),
    ],
)
def test_preexisting_edges_are_preserved_without_adding_guesses(
    target_kind: ComponentType,
    relation: RelationshipType,
) -> None:
    agent = _node("Agent", ComponentType.AGENT, "app/agent.py")
    tool = _node("Tool", ComponentType.TOOL, "tools/tool.py")
    model = _node("Model", ComponentType.MODEL, "models/model.py")
    target = tool if target_kind == ComponentType.TOOL else model
    edge = Edge(source=agent.id, target=target.id, relationship_type=relation, derivation="hint")
    doc = AiSbomDocument(target="fixture", nodes=[agent, tool, model], edges=[edge])
    AiSbomExtractor()._resolve_edges(doc, {})
    assert doc.edges == [edge]


def test_extraction_preserves_model_binding_without_guessing_unrelated_tools(
    tmp_path: Path,
) -> None:
    (tmp_path / "agents.py").write_text(
        """from agents import Agent, function_tool

@function_tool
def lookup(query: str) -> str:
    return query

worker = Agent(name="Worker", model="gpt-4o-mini", instructions="Answer questions.")
router = Agent(name="Router", instructions="Route requests without calling tools.")
""",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(
        tmp_path,
        AiSbomConfig(enable_llm=False, include_extensions={".py"}),
    )
    agents = {node.name: node for node in doc.nodes if node.component_type == ComponentType.AGENT}
    tool_ids = {node.id for node in doc.nodes if node.component_type == ComponentType.TOOL}
    model_ids = {node.id for node in doc.nodes if node.component_type == ComponentType.MODEL}
    assert {"Worker", "Router"} <= agents.keys()
    assert tool_ids and model_ids
    worker_edges = [
        edge
        for edge in doc.edges
        if edge.source == agents["Worker"].id and edge.target in tool_ids | model_ids
    ]
    assert not any(edge.target in tool_ids for edge in worker_edges)
    assert any(
        edge.relationship_type == RelationshipType.USES and edge.target in model_ids
        for edge in worker_edges
    )
    assert all(edge.derivation == "hint" for edge in worker_edges)
    assert not any(
        edge.source == agents["Router"].id and edge.target in tool_ids | model_ids
        for edge in doc.edges
    )


def test_java_binding_survives_without_linking_a_routing_controller(tmp_path: Path) -> None:
    source = tmp_path / "src/main/java/demo"
    source.mkdir(parents=True)
    (source / "Worker.java").write_text(
        """package demo;
import dev.langchain4j.agent.tool.Tool;
import dev.langchain4j.model.openai.OpenAiChatModel;
import dev.langchain4j.service.AiServices;
class Worker {
    interface Assistant { String chat(String message); }
    Assistant assistant = AiServices.builder(Assistant.class)
        .chatLanguageModel(OpenAiChatModel.builder().modelName("gpt-4o-mini").build())
        .tools(this).build();
    String answer(String message) { return assistant.chat(message); }
    @Tool String lookup(String query) { return query; }
}
""",
        encoding="utf-8",
    )
    (source / "Router.java").write_text(
        """package demo;
import org.springframework.web.bind.annotation.*;
@RestController
class Router {
    private Worker worker;
    @GetMapping("/chat")
    String route(@RequestParam String message) { return worker.answer(message); }
}
""",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    agents = {node.name: node for node in doc.nodes if node.component_type == ComponentType.AGENT}
    targets = {
        node.id: node
        for node in doc.nodes
        if node.component_type in {ComponentType.TOOL, ComponentType.MODEL}
    }
    assert {"Worker", "Router"} <= agents.keys()
    assert agents["Router"].metadata.extras["web_controller"] is True
    edges = [
        edge for edge in doc.edges if edge.source == agents["Worker"].id and edge.target in targets
    ]
    assert {(targets[edge.target].component_type, edge.relationship_type) for edge in edges} == {
        (ComponentType.TOOL, RelationshipType.CALLS),
        (ComponentType.MODEL, RelationshipType.USES),
    }
    assert all(edge.derivation == "hint" for edge in edges)
    assert not any(
        edge.source == agents["Router"].id and edge.target in targets for edge in doc.edges
    )
    endpoints = {node.id for node in doc.nodes if node.component_type == ComponentType.API_ENDPOINT}
    assert any(
        edge.source in endpoints
        and edge.target == agents["Router"].id
        and edge.relationship_type == RelationshipType.CALLS
        for edge in doc.edges
    )
