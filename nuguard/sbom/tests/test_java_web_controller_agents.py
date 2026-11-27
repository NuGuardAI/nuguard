"""A Spring controller is an agent only when it touches AI.

The web adapter emits every controller as a candidate agent so that it can be linked to the AI
agents it is injected with (NuGuardAI/nuguard#643). A controller with no AI usage and no link to
an AI component is not an agent (#641); keeping it turned every endpoint class of a plain web
app into a "critical" agent finding.
"""

from __future__ import annotations

from pathlib import Path

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.types import ComponentType

_PLAIN_CONTROLLER = """package demo.web;
import org.springframework.web.bind.annotation.*;
@RestController
class OrderController {
    private OrderService service;
    @GetMapping("/orders/{id}")
    String order(@PathVariable String id) { return service.find(id); }
}
"""
_PLAIN_SERVICE = """package demo.web;
class OrderService {
    String find(String id) { return id; }
}
"""
_AI_CONTROLLER = """package demo.web;
import dev.langchain4j.model.openai.OpenAiChatModel;
import org.springframework.web.bind.annotation.*;
@RestController
class ChatController {
    @PostMapping("/chat")
    String chat(@RequestBody String message) {
        return OpenAiChatModel.builder().modelName("gpt-4o-mini").build().generate(message);
    }
}
"""


def _extract(tmp_path: Path, files: dict[str, str]):
    source = tmp_path / "src/main/java/demo/web"
    source.mkdir(parents=True)
    for name, content in files.items():
        (source / name).write_text(content, encoding="utf-8")
    return AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))


def _names(doc, kind: ComponentType) -> set[str]:
    return {node.name for node in doc.nodes if node.component_type == kind}


def test_a_controller_without_ai_is_not_an_agent_but_keeps_its_endpoint(tmp_path: Path) -> None:
    doc = _extract(tmp_path, {"OrderController.java": _PLAIN_CONTROLLER, "OrderService.java": _PLAIN_SERVICE})
    assert _names(doc, ComponentType.AGENT) == set()
    assert any("/orders/" in name for name in _names(doc, ComponentType.API_ENDPOINT))
    node_ids = {node.id for node in doc.nodes}
    assert all(edge.source in node_ids and edge.target in node_ids for edge in doc.edges)


def test_a_controller_that_calls_a_model_is_still_an_agent(tmp_path: Path) -> None:
    doc = _extract(tmp_path, {"ChatController.java": _AI_CONTROLLER})
    assert "chatcontroller" in {name.lower() for name in _names(doc, ComponentType.AGENT)}


def test_ai_in_any_controller_method_keeps_the_agent(tmp_path: Path) -> None:
    controller = """package demo.web;
import org.springframework.web.bind.annotation.*;
@RestController
class MixedController {
    @GetMapping("/health")
    String health() { return "ok"; }
    @PostMapping("/ask")
    String ask(@RequestBody String message) {
        return client.chat().completions().create(message);
    }
}
"""
    doc = _extract(tmp_path, {"MixedController.java": controller})
    assert "mixedcontroller" in {name.lower() for name in _names(doc, ComponentType.AGENT)}
