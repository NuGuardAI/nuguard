from __future__ import annotations

from pathlib import Path

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.types import ComponentType


def test_java_source_and_maven_manifest_flow_through_extractor(tmp_path: Path) -> None:
    source_path = tmp_path / "src/main/java/demo/ChatController.java"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        """package demo;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.web.bind.annotation.*;
@RestController
class ChatController {
  @PostMapping("/chat")
  String chat(@RequestParam String message) {
    return client.prompt(String.format("User: %s", message))
      .options(o -> o.model("gpt-4o-mini")).call().content();
  }
}
""",
        encoding="utf-8",
    )
    (tmp_path / "pom.xml").write_text(
        """<project><dependencies><dependency>
<groupId>org.springframework.ai</groupId>
<artifactId>spring-ai-openai-spring-boot-starter</artifactId>
<version>1.0.1</version>
</dependency></dependencies></project>""",
        encoding="utf-8",
    )

    document = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    node_types = {item.component_type for item in document.nodes}
    assert ComponentType.MODEL in node_types
    assert ComponentType.AGENT in node_types
    assert ComponentType.API_ENDPOINT in node_types
    assert any(
        dep.purl.endswith("spring-ai-openai-spring-boot-starter@1.0.1") for dep in document.deps
    )

    endpoint = next(
        item for item in document.nodes if item.component_type == ComponentType.API_ENDPOINT
    )
    assert endpoint.evidence[0].location.path.endswith("ChatController.java")
    assert endpoint.evidence[0].location.line == 6


def test_java_is_in_default_source_extensions() -> None:
    assert ".java" in AiSbomConfig().include_extensions


def test_java_http_request_metadata_is_promoted_to_typed_node_metadata(tmp_path: Path) -> None:
    source_path = tmp_path / "src/main/java/demo/ItemController.java"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        """package demo;
import org.springframework.web.bind.annotation.*;
@RestController
class ItemController {
  @GetMapping("/items/{id}")
  String item(@PathVariable("id") String id, @RequestParam(value = "q", required = false) String q) {
    return id + q;
  }
}
""",
        encoding="utf-8",
    )

    document = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    endpoint = next(
        node
        for node in document.nodes
        if node.component_type == ComponentType.API_ENDPOINT
        and node.metadata.endpoint == "/items/{id}"
    )

    http_request = endpoint.metadata.http_request
    assert http_request is not None
    assert http_request.methods == ["GET"]
    assert {(p.name, p.location) for p in http_request.parameters} == {
        ("id", "path"),
        ("q", "query"),
    }
    assert "http_request" not in endpoint.metadata.extras
