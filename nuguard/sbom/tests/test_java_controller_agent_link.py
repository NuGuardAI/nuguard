"""A controller is linked to the agents it is injected with (NuGuardAI/nuguard#643).

The extractor resolves a relationship hint by the canonical name of its target, which is built from
the target's file path. The web adapter derives that path from the controller's package, its
imports and its own location, so the hint matches the agent emitted by the AI adapter.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from nuguard.sbom.adapters.java import JavaAIAdapter, JavaWebAdapter
from nuguard.sbom.core.java_parser import parse_java
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.models import AiSbomDocument, Evidence, Node, SourceLocation
from nuguard.sbom.types import ComponentType

_AGENT = """package {package};

import dev.langchain4j.service.MemoryId;
import dev.langchain4j.service.UserMessage;
import dev.langchain4j.service.spring.AiService;

@AiService
public interface SupportAgent {{
    String answer(@MemoryId String id, @UserMessage String message);
}}
"""


def _agent_canonicals(source: str, path: str) -> set[str]:
    detections = JavaAIAdapter().extract(source, path, parse_java(source, path))
    return {
        item.canonical_name for item in detections if item.component_type == ComponentType.AGENT
    }


def _controller_targets(source: str, path: str) -> set[str]:
    detections = JavaWebAdapter().extract(source, path, parse_java(source, path))
    controller = next(item for item in detections if item.component_type == ComponentType.AGENT)
    assert all(rel.relationship_type == "CALLS" for rel in controller.relationships)
    return {canonical for rel in controller.relationships for _, canonical in rel.target_files}


def test_a_controller_is_linked_to_the_ai_service_in_its_package() -> None:
    controller = """package demo.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SupportController {
    private final SupportAgent agent;

    public SupportController(SupportAgent agent) { this.agent = agent; }

    @GetMapping("/support")
    public String support(String message) { return agent.answer("1", message); }
}
"""
    agent = _agent_canonicals(
        _AGENT.format(package="demo.web"), "src/main/java/demo/web/SupportAgent.java"
    )
    targets = _controller_targets(controller, "src/main/java/demo/web/SupportController.java")
    assert agent and agent <= targets


def test_a_controller_is_linked_to_an_ai_service_imported_from_another_package() -> None:
    controller = """package demo.web;

import demo.agents.SupportAgent;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SupportController {
    private final SupportAgent agent;

    public SupportController(final SupportAgent agent) { this.agent = agent; }

    @GetMapping("/support")
    public String support(String message) { return agent.answer("1", message); }
}
"""
    agent = _agent_canonicals(
        _AGENT.format(package="demo.agents"), "src/main/java/demo/agents/SupportAgent.java"
    )
    targets = _controller_targets(controller, "src/main/java/demo/web/SupportController.java")
    assert agent and agent <= targets


def test_a_wildcard_import_and_field_injection_are_resolved() -> None:
    controller = """package demo.web;

import demo.agents.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SupportController {
    @Autowired
    private SupportAgent agent;

    @GetMapping("/support")
    public String support(String message) { return agent.answer("1", message); }
}
"""
    agent = _agent_canonicals(
        _AGENT.format(package="demo.agents"), "src/main/java/demo/agents/SupportAgent.java"
    )
    targets = _controller_targets(controller, "src/main/java/demo/web/SupportController.java")
    assert agent and agent <= targets


def test_a_qualified_type_and_a_qualifier_annotation_are_resolved() -> None:
    controller = """package demo.web;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SupportController {
    private final demo.agents.SupportAgent agent;

    public SupportController(@Qualifier("support") demo.agents.SupportAgent agent) { this.agent = agent; }

    @GetMapping("/support")
    public String support(String message) { return agent.answer("1", message); }
}
"""
    agent = _agent_canonicals(
        _AGENT.format(package="demo.agents"), "src/main/java/demo/agents/SupportAgent.java"
    )
    targets = _controller_targets(controller, "src/main/java/demo/web/SupportController.java")
    assert agent and agent <= targets


def test_the_package_root_is_found_in_a_nested_project() -> None:
    controller = """package demo.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SupportController {
    public SupportController(SupportAgent agent) { }

    @GetMapping("/support")
    public String support(String message) { return message; }
}
"""
    prefix = "tests/apps/demo/src/main/java/demo/web/"
    agent = _agent_canonicals(_AGENT.format(package="demo.web"), prefix + "SupportAgent.java")
    targets = _controller_targets(controller, prefix + "SupportController.java")
    assert agent and agent <= targets


def test_a_controller_injected_with_a_plain_service_gets_no_agent_edge() -> None:
    controller = """package demo.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SupportController {
    private final SupportService service;

    public SupportController(SupportService service) { this.service = service; }

    @GetMapping("/support")
    public String support(String message) { return service.handle(message); }
}
"""
    service = "package demo.web;\n\npublic class SupportService {\n    String handle(String m) { return m; }\n}\n"
    service_agents = _agent_canonicals(service, "src/main/java/demo/web/SupportService.java")
    targets = _controller_targets(controller, "src/main/java/demo/web/SupportController.java")
    assert not (service_agents & targets)


def test_a_controller_with_no_injected_types_has_no_hints() -> None:
    controller = """package demo.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class PingController {
    @GetMapping("/ping")
    public String ping() { return "pong"; }
}
"""
    assert _controller_targets(controller, "src/main/java/demo/web/PingController.java") == set()


def test_a_controller_never_links_to_itself() -> None:
    controller = """package demo.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SupportController {
    private SupportController other;

    @GetMapping("/support")
    public String support() { return "x"; }
}
"""
    path = "src/main/java/demo/web/SupportController.java"
    detections = JavaWebAdapter().extract(controller, path, parse_java(controller, path))
    agent = next(item for item in detections if item.component_type == ComponentType.AGENT)
    assert agent.canonical_name not in {rel.target_canonical for rel in agent.relationships}


_CONTROLLER = """package demo.web;
{imports}
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SupportController {{
{body}
    @GetMapping("/support")
    public String support(String message) {{ return "x"; }}
}}
"""
_WEB = "src/main/java/demo/web/"
_AGENTS = "src/main/java/demo/agents/"
_FIELD = "    private final SupportAgent agent;\n"


def _node(canonical: str, path: str) -> Node:
    node = Node(
        name=canonical,
        component_type=ComponentType.AGENT,
        confidence=0.9,
        evidence=[
            Evidence(
                kind="ast",
                confidence=0.9,
                detail="fixture",
                location=SourceLocation(path=path, line=1),
            )
        ],
    )
    node.metadata.extras["canonical_name"] = canonical
    return node


def _linked_agents(
    controller: str,
    agents: dict[str, str],
    plain: tuple[str, ...] = (),
    scanned: bool = True,
) -> set[str]:
    """Run the web adapter, resolve its hints, and return the agent files it links to."""
    path = _WEB + "SupportController.java"
    detections = JavaWebAdapter().extract(controller, path, parse_java(controller, path))
    source = next(item for item in detections if item.component_type == ComponentType.AGENT)
    controller_node = _node(source.canonical_name, path)
    agent_nodes = {
        agent_path: _node(canonical, agent_path)
        for agent_path, text in agents.items()
        for canonical in _agent_canonicals(text, agent_path)
    }
    doc = AiSbomDocument(target="fixture", nodes=[controller_node, *agent_nodes.values()])
    node_map = cast("Any", {"controller": SimpleNamespace(relationships=source.relationships)})
    files = {path, *agents, *plain} if scanned else None
    AiSbomExtractor()._resolve_edges(doc, node_map, scanned_files=files)
    path_by_id = {node.id: agent_path for agent_path, node in agent_nodes.items()}
    return {
        path_by_id[edge.target]
        for edge in doc.edges
        if edge.source == controller_node.id
        and edge.derivation == "hint"
        and edge.target in path_by_id
    }


def test_a_field_with_a_qualified_type_keeps_its_package() -> None:
    imports = "import org.springframework.beans.factory.annotation.Autowired;"
    body = "    @Autowired\n    private demo.agents.SupportAgent agent;\n"
    controller = _CONTROLLER.format(imports=imports, body=body)
    targets = _controller_targets(controller, _WEB + "SupportController.java")
    real = _agent_canonicals(_AGENT.format(package="demo.agents"), _AGENTS + "SupportAgent.java")
    same_package = _agent_canonicals(_AGENT.format(package="demo.web"), _WEB + "SupportAgent.java")
    assert real and real <= targets
    assert not (same_package & targets)


def test_commented_out_and_quoted_declarations_are_not_injection() -> None:
    body = (
        "    // private SupportAgent agent;\n"
        "    /* private SupportAgent other; */\n"
        '    private String example = "private SupportAgent text;";\n'
    )
    controller = _CONTROLLER.format(imports="", body=body)
    assert _controller_targets(controller, _WEB + "SupportController.java") == set()


def test_a_same_package_agent_hides_a_wildcard_imported_one() -> None:
    controller = _CONTROLLER.format(imports="import demo.agents.*;", body=_FIELD)
    agents = {
        _WEB + "SupportAgent.java": _AGENT.format(package="demo.web"),
        _AGENTS + "SupportAgent.java": _AGENT.format(package="demo.agents"),
    }
    assert _linked_agents(controller, agents) == {_WEB + "SupportAgent.java"}


def test_a_same_package_plain_class_hides_a_wildcard_imported_agent() -> None:
    controller = _CONTROLLER.format(imports="import demo.agents.*;", body=_FIELD)
    agents = {_AGENTS + "SupportAgent.java": _AGENT.format(package="demo.agents")}
    assert _linked_agents(controller, agents, plain=(_WEB + "SupportAgent.java",)) == set()


def test_a_wildcard_imported_agent_is_linked_when_the_package_has_no_such_type() -> None:
    controller = _CONTROLLER.format(imports="import demo.agents.*;", body=_FIELD)
    agents = {_AGENTS + "SupportAgent.java": _AGENT.format(package="demo.agents")}
    assert _linked_agents(controller, agents) == {_AGENTS + "SupportAgent.java"}
    assert _linked_agents(controller, agents, scanned=False) == {_AGENTS + "SupportAgent.java"}


def test_an_explicit_import_beats_a_same_package_agent() -> None:
    controller = _CONTROLLER.format(imports="import demo.agents.SupportAgent;", body=_FIELD)
    agents = {
        _WEB + "SupportAgent.java": _AGENT.format(package="demo.web"),
        _AGENTS + "SupportAgent.java": _AGENT.format(package="demo.agents"),
    }
    assert _linked_agents(controller, agents) == {_AGENTS + "SupportAgent.java"}


def test_the_agent_is_found_after_many_ordinary_dependencies() -> None:
    imports = "import demo.a.*;\nimport demo.b.*;\nimport demo.agents.*;"
    services = "".join(f"    private final Service{n} service{n};\n" for n in range(1, 7))
    controller = _CONTROLLER.format(imports=imports, body=services + _FIELD)
    agents = {_AGENTS + "SupportAgent.java": _AGENT.format(package="demo.agents")}
    assert _linked_agents(controller, agents) == {_AGENTS + "SupportAgent.java"}
