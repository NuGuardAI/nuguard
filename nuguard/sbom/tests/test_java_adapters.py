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
    literal annotation names. The ``value = LevelConstants.LEVEL_1`` bare
    constant reference (not a string literal) is resolved to ``LEVEL_1`` via
    the identifier-name fallback — confirmed live against the real deployed
    app: ``GET /VulnerableApp/BlindSQLInjectionVulnerability/LEVEL_1`` -> 200.
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
    endpoints = {
        item.metadata["api_endpoint"]: item
        for item in detections
        if item.component_type == ComponentType.API_ENDPOINT
    }

    assert set(endpoints) == {
        "/BlindSQLInjectionVulnerability/LEVEL_1",
        "/BlindSQLInjectionVulnerability/LEVEL_2",
    }
    assert len({item.canonical_name for item in endpoints.values()}) == 2


def test_unresolvable_route_value_still_yields_distinct_endpoints() -> None:
    """A path value that isn't a string literal or a bare identifier.

    Nothing in ``value = 1`` can be turned into a plausible path segment, so
    the class-level path is used as-is — but distinct handler methods must
    still not collapse into one SBOM node.
    """
    source = """package demo;

@VulnerableAppRestController(value = "SomeVulnerability")
public class SomeVulnerability {

    @VulnerableAppRequestMapping(value = 1)
    public String stepOne() { return "ok"; }

    @VulnerableAppRequestMapping(value = 1)
    public String stepTwo() { return "ok"; }
}
"""
    parsed = parse_java(source, "SomeVulnerability.java")
    detections = JavaWebAdapter().extract(source, "SomeVulnerability.java", parsed)
    endpoints = [
        item for item in detections if item.component_type == ComponentType.API_ENDPOINT
    ]

    assert len(endpoints) == 2
    assert len({item.canonical_name for item in endpoints}) == 2
    for endpoint in endpoints:
        assert endpoint.metadata["api_endpoint"] == "/SomeVulnerability"


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


def test_route_value_falls_back_to_bare_constant_identifier() -> None:
    adapter = JavaWebAdapter()
    assert (
        adapter._route_value('@VulnerableAppRequestMapping(value = LevelConstants.LEVEL_1)')
        == "LEVEL_1"
    )
    # A string literal still wins outright — no fallback needed.
    assert adapter._route_value('@RequestMapping("/api")') == "/api"
    # Nothing plausible to extract.
    assert adapter._route_value('@VulnerableAppRequestMapping(value = 1)') == ""


def _http_request_by_route(source: str, file_path: str) -> dict[str, dict]:
    parsed = parse_java(source, file_path)
    return {
        item.display_name: item.metadata["http_request"]
        for item in JavaWebAdapter().extract(source, file_path, parsed)
        if item.component_type == ComponentType.API_ENDPOINT
    }


def _params(http_request: dict) -> dict[tuple[str, str], dict]:
    return {(p["name"], p["location"]): p for p in http_request["parameters"]}


def test_http_request_maps_spring_parameter_annotations() -> None:
    source = """package demo;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class ItemController {
    private static final String URL_PARAM_KEY = "url";

    @GetMapping("/items/{id}")
    public String item(@PathVariable("id") String itemId,
                       @RequestParam(value = "q", required = false) String query,
                       @RequestParam(name = "page", defaultValue = "1") Integer page,
                       @RequestHeader("X-Trace") String trace,
                       @CookieValue("session") String session,
                       @RequestParam Map<String, String> queryParams,
                       HttpServletRequest request) {
        return queryParams.get("sort") + queryParams.get(URL_PARAM_KEY);
    }

    @PostMapping(value = "/upload", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public String upload(@RequestPart("file") MultipartFile file) {
        return "ok";
    }

    @RequestMapping(value = "/any")
    public String any(String keyword) {
        return keyword;
    }
}
"""
    routes = _http_request_by_route(source, "ItemController.java")

    item = routes["GET /api/items/{id}"]
    assert item["methods"] == ["GET"]
    assert item["has_unresolved_inputs"] is False
    params = _params(item)
    assert params[("id", "path")]["required"] is True
    assert params[("q", "query")]["required"] is False
    assert params[("page", "query")] == {
        "name": "page",
        "location": "query",
        "type_hint": "int",
        "required": False,
    }
    assert ("X-Trace", "header") in params
    assert ("session", "cookie") in params
    # Map keys resolved from literal and same-file constant lookups.
    assert ("sort", "query") in params
    assert ("url", "query") in params
    assert not any(name == "request" for name, _ in params)

    upload = routes["POST /api/upload"]
    assert _params(upload)[("file", "multipart")]["required"] is True
    assert upload["content_types"] == ["multipart/form-data"]

    any_route = routes["ANY /api/any"]
    assert any_route["methods"] == ["UNKNOWN"]
    assert ("keyword", "query") in _params(any_route)


def test_http_request_marks_unresolvable_inputs_instead_of_guessing() -> None:
    source = """package demo;

import org.springframework.web.bind.annotation.*;

@RestController
public class SearchController {
    @GetMapping("/search")
    public String search(@RequestParam Map<String, String> queryParams) {
        return queryParams.get(Constants.ID);
    }

    @PostMapping("/orders")
    public String create(@RequestBody OrderDto order) {
        return "ok";
    }
}
"""
    routes = _http_request_by_route(source, "SearchController.java")

    search = routes["GET /search"]
    assert search["parameters"] == []
    assert search["has_unresolved_inputs"] is True

    create = routes["POST /orders"]
    assert create["parameters"] == []
    assert create["has_unresolved_inputs"] is True
    assert create["content_types"] == ["application/json"]


def test_http_request_maps_jax_rs_parameter_annotations() -> None:
    source = """package demo;

import jakarta.ws.rs.*;

@Path("/users")
public class UserResource {
    @GET
    @Path("/{id}")
    public String get(@PathParam("id") long id, @QueryParam("expand") String expand) {
        return "ok";
    }

    @POST
    public String create(@FormParam("name") String name) {
        return "ok";
    }
}
"""
    routes = _http_request_by_route(source, "UserResource.java")

    get = _params(routes["GET /users/{id}"])
    assert get[("id", "path")]["type_hint"] == "int"
    assert ("expand", "query") in get
    create = routes["POST /users"]
    assert ("name", "form") in _params(create)
    assert create["content_types"] == ["application/x-www-form-urlencoded"]
