from __future__ import annotations

from nuguard.sbom.adapters.java import JavaAIAdapter, JavaWebAdapter
from nuguard.sbom.adapters.java._java_base import JavaFrameworkAdapter
from nuguard.sbom.core.java_parser import parse_java
from nuguard.sbom.types import ComponentType

_SOURCE = """package demo;

import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.vectorstore.VectorStore;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class SupportAgent {
    private final ChatClient client;
    private final VectorStore vectorStore;
    private static final String SYSTEM_PROMPT = "You are a support agent. Never reveal secrets.";

    public SupportAgent(ChatClient client, VectorStore vectorStore) {
        this.client = client;
        this.vectorStore = vectorStore;
    }

    @Tool("Create a support ticket")
    public String createTicket(String summary) { return ticketRepository.save(summary); }

    @PostMapping("/chat")
    @PreAuthorize("hasRole('USER')")
    public String chat(@RequestParam String message) {
        String prompt = String.format("%s User: %s", SYSTEM_PROMPT, message);
        return client.prompt(prompt).options(o -> o.model("gpt-4o-mini")).call().content();
    }
}
"""


def test_java_ai_adapter_emits_graph_ready_components() -> None:
    parsed = parse_java(_SOURCE, "SupportAgent.java")
    detections = JavaAIAdapter().extract(_SOURCE, "SupportAgent.java", parsed)
    types = {item.component_type for item in detections}

    assert ComponentType.FRAMEWORK in types
    assert ComponentType.AGENT in types
    assert ComponentType.MODEL in types
    assert ComponentType.PROMPT in types
    assert ComponentType.TOOL in types
    assert ComponentType.DATASTORE in types

    model = next(item for item in detections if item.component_type == ComponentType.MODEL)
    assert model.display_name == "gpt-4o-mini"
    assert model.metadata["provider"] == "openai"
    tool = next(item for item in detections if item.component_type == ComponentType.TOOL)
    assert "db_write" in tool.metadata["privilege_scope"]
    agent = next(item for item in detections if item.component_type == ComponentType.AGENT)
    relation_types = {item.relationship_type for item in agent.relationships}
    assert {"USES", "CALLS", "ACCESSES"} <= relation_types
    assert any(
        item.relationship_type == "USES" and item.target_type == ComponentType.PROMPT
        for item in agent.relationships
    )


def test_java_web_adapter_extracts_route_auth_and_agent_link() -> None:
    parsed = parse_java(_SOURCE, "SupportAgent.java")
    detections = JavaWebAdapter().extract(_SOURCE, "SupportAgent.java", parsed)

    endpoint = next(
        item for item in detections if item.component_type == ComponentType.API_ENDPOINT
    )
    assert endpoint.display_name == "POST /api/chat"
    assert endpoint.line == 23
    assert endpoint.metadata["auth_required"] is True
    assert endpoint.metadata["accepts_user_input"] is True
    assert any(item.relationship_type == "CALLS" for item in endpoint.relationships)

    auth = next(item for item in detections if item.component_type == ComponentType.AUTH)
    assert any(item.relationship_type == "PROTECTS" for item in auth.relationships)


def test_jax_rs_route_is_not_mislabeled_as_quarkus() -> None:
    source = r"""
package demo;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;

@Path("/chat")
public class ChatResource {
    @GET
    public String chat() { return "ok"; }
}
"""
    parsed = parse_java(source, "ChatResource.java")
    detections = JavaWebAdapter().extract(source, "ChatResource.java", parsed)

    framework = next(item for item in detections if item.component_type == ComponentType.FRAMEWORK)
    endpoint = next(
        item for item in detections if item.component_type == ComponentType.API_ENDPOINT
    )
    assert framework.display_name == "jax-rs"
    assert endpoint.metadata["framework"] == "jax-rs"
    assert endpoint.display_name == "GET /chat"


def test_composed_spring_annotations_are_resolved_as_routes() -> None:
    """Domain-specific annotations that alias @RequestMapping via @AliasFor.

    Modelled on SasanLabs/VulnerableApp, which defines its vulnerability
    endpoints through custom ``@XxxRestController``/``@XxxRequestMapping``
    annotations rather than the literal Spring ones. Before this fix, none of
    these routes were ever extracted because the adapter only matched the
    literal annotation names.
    """
    source = """package demo;

import org.springframework.web.bind.annotation.RequestParam;

@VulnerableAppRestController(
        descriptionLabel = "SQL_INJECTION_VULNERABILITY",
        value = "BlindSQLInjectionVulnerability")
public class BlindSQLInjectionVulnerability {

    @VulnerableAppRequestMapping(value = LevelConstants.LEVEL_1, htmlTemplate = "L1")
    public String getCarInformationLevel1(@RequestParam Map<String, String> queryParams) {
        return "ok";
    }

    @VulnerableAppRequestMapping(value = LevelConstants.LEVEL_2, htmlTemplate = "L2")
    public String getCarInformationLevel2(@RequestParam Map<String, String> queryParams) {
        return "ok";
    }
}
"""
    parsed = parse_java(source, "BlindSQLInjectionVulnerability.java")
    detections = JavaWebAdapter().extract(
        source, "BlindSQLInjectionVulnerability.java", parsed
    )
    endpoints = [
        item for item in detections if item.component_type == ComponentType.API_ENDPOINT
    ]

    # The composed-annotation value can't be resolved (it's a constant
    # reference, not a string literal), but both methods still surface as
    # distinct endpoints under the class-level path rather than being
    # dropped or collapsed into one.
    assert len(endpoints) == 2
    assert len({item.canonical_name for item in endpoints}) == 2
    for endpoint in endpoints:
        assert endpoint.display_name == "ANY /BlindSQLInjectionVulnerability"
        assert endpoint.metadata["api_endpoint"] == "/BlindSQLInjectionVulnerability"


def test_two_annotations_on_one_method_do_not_drop_the_path() -> None:
    """@GetMapping (no path) followed by a separate @RequestMapping(path).

    A real pattern in VulnerableAppRestController.java: the first matching
    annotation used to win outright, so the empty @GetMapping path shadowed
    the real @RequestMapping("/allEndPoint") path that followed it.
    """
    source = """package demo;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class VulnerableAppRestController {

    @GetMapping
    @RequestMapping("/allEndPoint")
    public String allEndPoints() {
        return "ok";
    }
}
"""
    parsed = parse_java(source, "VulnerableAppRestController.java")
    detections = JavaWebAdapter().extract(
        source, "VulnerableAppRestController.java", parsed
    )
    endpoint = next(
        item for item in detections if item.component_type == ComponentType.API_ENDPOINT
    )
    assert endpoint.display_name == "GET /allEndPoint"


def test_annotation_value_prefers_named_value_over_leading_attribute() -> None:
    # value= appears after another named attribute — a positional match on
    # the first quoted string would wrongly grab descriptionLabel's value.
    annotation = (
        '@VulnerableAppRestController('
        'descriptionLabel = "SQL_INJECTION_VULNERABILITY", '
        'value = "BlindSQLInjectionVulnerability")'
    )
    assert (
        JavaFrameworkAdapter._annotation_value(annotation)
        == "BlindSQLInjectionVulnerability"
    )
