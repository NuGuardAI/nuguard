"""A controller is linked to the agents it is injected with (NuGuardAI/nuguard#643).

The extractor resolves a relationship hint by the canonical name of its target, which is built from
the target's file path. The web adapter derives that path from the controller's package, its
imports and its own location, so the hint matches the agent emitted by the AI adapter.
"""

from __future__ import annotations

from nuguard.sbom.adapters.java import JavaAIAdapter, JavaWebAdapter
from nuguard.sbom.core.java_parser import parse_java
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
    return {rel.target_canonical for rel in controller.relationships}


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
