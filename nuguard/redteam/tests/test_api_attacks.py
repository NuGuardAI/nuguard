"""Tests for direct HTTP API attack scenario builders and generator integration."""
from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from nuguard.models.exploit_chain import HTTP_2XX_SENTINEL, GoalType, ScenarioType
from nuguard.redteam.executor.executor import StepResult
from nuguard.redteam.scenarios.api_attacks import (
    _DB_ERROR_SIGNATURES,
    _TRAVERSAL_SUCCESS_SIGNATURES,
    _replace_first_id_param,
    _replace_path_param,
    build_auth_bypass,
    build_idor,
    build_injection_probe,
    build_mass_assignment,
    build_open_redirect_probe,
    build_path_traversal_probe,
    build_reflected_xss_probe,
)
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.sbom.models import (
    AiSbomDocument,
    Edge,
    Node,
    NodeMetadata,
    NodeType,
    ScanSummary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sbom(nodes: list[Node], edges: list[Edge] | None = None) -> AiSbomDocument:
    return AiSbomDocument(
        generated_at=datetime.now(UTC),
        target="test-app",
        nodes=nodes,
        edges=edges or [],
        summary=ScanSummary(),
    )


def _api_node(
    node_id: str,
    name: str,
    path: str = "/api/resource",
    method: str = "GET",
    auth_required: bool = False,
    idor_surface: bool = False,
    path_params: list[str] | None = None,
) -> Node:
    return Node(
        id=_uuid.uuid5(_uuid.NAMESPACE_URL, node_id),
        name=name,
        component_type=NodeType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(
            endpoint=path,
            method=method,
            auth_required=auth_required,
            idor_surface=idor_surface,
            path_params=path_params or [],
        ),
    )


# ---------------------------------------------------------------------------
# _replace_first_id_param
# ---------------------------------------------------------------------------

def test_replace_curly_brace_param():
    result = _replace_first_id_param("/users/{user_id}/profile", ["user_id"])
    assert result == "/users/99999/profile"


def test_replace_colon_style_param():
    result = _replace_first_id_param("/users/:id/posts", ["id"])
    assert result == "/users/99999/posts"


def test_no_id_param_returns_none():
    result = _replace_first_id_param("/users/{name}/profile", ["name"])
    assert result is None


def test_first_id_param_replaced_only():
    result = _replace_first_id_param("/orgs/{org_id}/users/{user_id}", ["org_id", "user_id"])
    assert result == "/orgs/99999/users/{user_id}"


# ---------------------------------------------------------------------------
# build_auth_bypass
# ---------------------------------------------------------------------------

def test_auth_bypass_scenario_structure():
    s = build_auth_bypass("ep1", "Get User", "/api/users", method="GET")
    assert s.goal_type == GoalType.API_ATTACK
    assert s.scenario_type == ScenarioType.AUTH_BYPASS
    assert s.chain is not None
    step = s.chain.steps[0]
    assert step.target_path == "/api/users"
    assert step.http_method == "GET"
    assert step.success_signal == HTTP_2XX_SENTINEL
    assert step.on_failure == "abort"
    assert step.payload == ""


def test_auth_bypass_impact_score_elevated():
    s = build_auth_bypass("ep1", "Get User", "/api/users")
    # has_unauth_entry modifier: base 8.0 + 0.5 = 8.5
    assert s.impact_score == 8.5


def test_auth_bypass_title_contains_endpoint_name():
    s = build_auth_bypass("ep1", "Admin Panel", "/admin")
    assert "Admin Panel" in s.title


# ---------------------------------------------------------------------------
# build_mass_assignment
# ---------------------------------------------------------------------------

def test_mass_assignment_scenario_structure():
    s = build_mass_assignment("ep2", "Create User", "/api/users", method="POST")
    assert s.goal_type == GoalType.API_ATTACK
    assert s.scenario_type == ScenarioType.MASS_ASSIGNMENT
    step = s.chain.steps[0]
    assert step.target_path == "/api/users"
    assert step.http_method == "POST"
    assert step.http_body is not None
    assert step.http_body.get("role") == "admin"
    assert step.http_body.get("is_superuser") is True
    # OR-matched: the literal escalation value plus JSON-shaped echoes of the
    # smuggled fields (see build_mass_assignment docstring).
    assert step.success_signal.startswith("superuser|")
    assert '"role": "admin"' in step.success_signal
    assert '"is_superuser": true' in step.success_signal
    # Must require 2xx to avoid false positives when frameworks echo the request
    # body in a 422 validation error response.
    assert step.success_requires_2xx is True


def test_mass_assignment_no_false_positive_on_422_echo():
    """FastAPI echoes request body in 422 validation errors — must not be flagged."""
    from nuguard.models.exploit_chain import ExploitStep
    # Simulate FastAPI 422: {"detail":[{"input":{"privilege_level":"superuser",...}}]}
    response_422 = (
        '{"detail":[{"type":"missing","loc":["body","email"],"msg":"Field required",'
        '"input":{"role":"admin","is_admin":true,"privilege_level":"superuser",'
        '"is_superuser":true,"permissions":["admin","write","delete"]}}]}'
    )
    step = ExploitStep(
        step_id="s1",
        step_type="INVOKE",
        description="Send extra privilege fields",
        payload="",
        success_signal="superuser",
        success_requires_2xx=True,
    )
    result = StepResult(step=step, response=response_422, tool_calls=[], http_status_code=422)
    assert result.success_signal_found is False, (
        "keyword 'superuser' in a 422 body is an echo of rejected input, not a success"
    )


def test_mass_assignment_true_positive_on_200():
    """A 200 response that reflects the privileged field IS a mass assignment success."""
    from nuguard.models.exploit_chain import ExploitStep
    response_200 = '{"id": 42, "role": "admin", "privilege_level": "superuser", "created": true}'
    step = ExploitStep(
        step_id="s1",
        step_type="INVOKE",
        description="Send extra privilege fields",
        payload="",
        success_signal="superuser",
        success_requires_2xx=True,
    )
    result = StepResult(step=step, response=response_200, tool_calls=[], http_status_code=200)
    assert result.success_signal_found is True


def test_mass_assignment_impact_score():
    s = build_mass_assignment("ep2", "Create User", "/api/users")
    assert s.impact_score == 8.0


# ---------------------------------------------------------------------------
# build_idor
# ---------------------------------------------------------------------------

def test_idor_scenario_structure():
    s = build_idor("ep3", "Get Record", "/records/{id}", ["id"])
    assert s is not None
    assert s.goal_type == GoalType.API_ATTACK
    assert s.scenario_type == ScenarioType.IDOR
    step = s.chain.steps[0]
    assert step.target_path == "/records/99999"
    assert step.http_method == "GET"
    assert step.success_signal == HTTP_2XX_SENTINEL


def test_idor_scenario_has_low_id_fallback_probe():
    """A miss on the 99999 sentinel falls through to a low, likely-allocated ID.

    A huge out-of-range ID can never surface a real cross-tenant leak on apps
    with small sequential-integer primary keys (e.g. Juice Shop's basket IDs)
    since no row exists there at all — probing "1" as a second, cheap step
    catches the case where a real neighbouring row does exist.
    """
    s = build_idor("ep3", "Get Record", "/records/{id}", ["id"])
    assert s is not None
    steps = s.chain.steps
    assert len(steps) == 2
    assert steps[0].target_path == "/records/99999"
    assert steps[0].on_failure == "skip"
    assert steps[1].target_path == "/records/1"
    assert steps[1].on_failure == "abort"
    assert all(step.use_llm_eval for step in steps)


def test_idor_returns_none_when_no_id_param():
    result = build_idor("ep3", "Get Resource", "/resources/{name}", ["name"])
    assert result is None


def test_idor_returns_none_when_no_path_params():
    result = build_idor("ep3", "List All", "/resources", [])
    assert result is None


# ---------------------------------------------------------------------------
# _replace_path_param
# ---------------------------------------------------------------------------

def test_replace_path_param_curly_brace():
    result = _replace_path_param("/search/{query}", "query", "' OR '1'='1")
    assert result == "/search/' OR '1'='1"


def test_replace_path_param_non_id_name():
    """Unlike _replace_first_id_param, any param name is a valid target."""
    result = _replace_path_param("/products/{category}", "category", "payload")
    assert result == "/products/payload"


def test_replace_path_param_absent_returns_none():
    result = _replace_path_param("/users/{id}", "name", "payload")
    assert result is None


# ---------------------------------------------------------------------------
# build_injection_probe
# ---------------------------------------------------------------------------

def test_injection_probe_path_param_candidates():
    s = build_injection_probe("ep5", "Search", "/search/{query}", path_params=["query"])
    assert s is not None
    assert s.goal_type == GoalType.API_ATTACK
    assert s.scenario_type == ScenarioType.SQL_INJECTION
    assert s.chain is not None
    assert len(s.chain.steps) == 2  # one per _INJECTION_PAYLOADS entry
    for step in s.chain.steps:
        assert step.target_path is not None
        assert step.target_path.startswith("/search/")
        assert step.success_signal == "|".join(_DB_ERROR_SIGNATURES)
        assert step.success_requires_2xx is False
        assert step.use_llm_eval is True
    assert s.chain.steps[0].on_failure == "skip"
    assert s.chain.steps[-1].on_failure == "abort"


def test_injection_probe_body_field_candidates_include_sqli_and_nosqli():
    s = build_injection_probe(
        "ep6", "Login", "/login", method="POST",
        request_body_schema={"email": "str", "password": "str"},
    )
    assert s is not None
    assert s.chain is not None
    bodies = [step.http_body for step in s.chain.steps]
    assert any(b.get("email") == "' OR '1'='1" for b in bodies)
    assert any(b.get("email") == {"$ne": None} for b in bodies)
    assert any(b.get("password") == "' OR '1'='1" for b in bodies)


def test_injection_probe_returns_none_with_no_candidates():
    result = build_injection_probe("ep7", "List All", "/resources")
    assert result is None


def test_injection_probe_impact_score_matches_sql_injection_override():
    s = build_injection_probe("ep5", "Search", "/search/{query}", path_params=["query"])
    assert s is not None
    assert s.impact_score == 8.5


# ---------------------------------------------------------------------------
# build_path_traversal_probe
# ---------------------------------------------------------------------------

def test_path_traversal_probe_path_param_candidates():
    s = build_path_traversal_probe("ep8", "Download", "/files/{filename}", path_params=["filename"])
    assert s is not None
    assert s.goal_type == GoalType.API_ATTACK
    assert s.scenario_type == ScenarioType.PATH_TRAVERSAL
    assert s.chain is not None
    assert len(s.chain.steps) == 2  # one per _TRAVERSAL_PAYLOADS entry
    for step in s.chain.steps:
        assert step.target_path is not None
        assert "etc%2fpasswd" in step.target_path or "etc/passwd" in step.target_path
        assert step.success_signal == "|".join(_TRAVERSAL_SUCCESS_SIGNATURES)
        assert step.success_requires_2xx is True
    assert s.chain.steps[-1].on_failure == "abort"


def test_path_traversal_probe_ignores_non_file_param_names():
    """A path param whose name doesn't suggest a file is not probed."""
    result = build_path_traversal_probe("ep8", "Get User", "/users/{id}", path_params=["id"])
    assert result is None


def test_path_traversal_probe_body_field_candidate():
    s = build_path_traversal_probe(
        "ep9", "Preview", "/preview", method="POST",
        request_body_schema={"template": "str", "title": "str"},
    )
    assert s is not None
    assert s.chain is not None
    bodies = [step.http_body for step in s.chain.steps]
    assert any(b.get("template") == "../../../../etc/passwd" for b in bodies)
    assert all("title" not in b or b["title"] != "../../../../etc/passwd" for b in bodies)


def test_path_traversal_probe_returns_none_with_no_candidates():
    result = build_path_traversal_probe("ep10", "List All", "/resources")
    assert result is None


# ---------------------------------------------------------------------------
# build_open_redirect_probe
# ---------------------------------------------------------------------------

def test_open_redirect_probe_path_param_candidate():
    s = build_open_redirect_probe("ep11", "Login", "/login/{next}", path_params=["next"])
    assert s is not None
    assert s.goal_type == GoalType.API_ATTACK
    assert s.scenario_type == ScenarioType.OPEN_REDIRECT
    assert s.chain is not None
    step = s.chain.steps[0]
    assert step.target_path is not None
    assert ".invalid" in step.target_path
    assert step.success_signal == step.target_path.rsplit("/login/", 1)[-1]
    assert step.success_requires_2xx is False


def test_open_redirect_probe_ignores_non_redirect_param_names():
    result = build_open_redirect_probe("ep11", "Get User", "/users/{id}", path_params=["id"])
    assert result is None


def test_open_redirect_probe_body_field_candidate():
    s = build_open_redirect_probe(
        "ep12", "Login", "/login", method="POST",
        request_body_schema={"return_to": "str", "email": "str"},
    )
    assert s is not None
    assert s.chain is not None
    body = s.chain.steps[0].http_body
    assert body is not None
    assert ".invalid" in body["return_to"]


def test_open_redirect_probe_marker_is_unique_per_call():
    s1 = build_open_redirect_probe("ep11", "Login", "/login/{next}", path_params=["next"])
    s2 = build_open_redirect_probe("ep11", "Login", "/login/{next}", path_params=["next"])
    assert s1 is not None and s2 is not None
    assert s1.chain.steps[0].success_signal != s2.chain.steps[0].success_signal


def test_open_redirect_probe_returns_none_with_no_candidates():
    result = build_open_redirect_probe("ep13", "List All", "/resources")
    assert result is None


# ---------------------------------------------------------------------------
# build_reflected_xss_probe
# ---------------------------------------------------------------------------

def test_reflected_xss_probe_path_param_candidate():
    s = build_reflected_xss_probe("ep14", "Search", "/search/{query}", path_params=["query"])
    assert s is not None
    assert s.goal_type == GoalType.API_ATTACK
    assert s.scenario_type == ScenarioType.REFLECTED_XSS
    assert s.chain is not None
    step = s.chain.steps[0]
    assert step.target_path is not None
    assert "<script>" in step.target_path
    assert "nuguardxss" in step.target_path
    assert step.success_signal == "<script>" + step.target_path.split("<script>", 1)[1]
    assert step.success_requires_2xx is False


def test_reflected_xss_probe_body_field_candidate():
    s = build_reflected_xss_probe(
        "ep15", "Comment", "/comments", method="POST",
        request_body_schema={"body": "str", "author": "str"},
    )
    assert s is not None
    assert s.chain is not None
    bodies = [step.http_body for step in s.chain.steps]
    assert any("<script>" in (b.get("body") or "") for b in bodies)
    assert any("<script>" in (b.get("author") or "") for b in bodies)


def test_reflected_xss_probe_marker_is_unique_per_call():
    s1 = build_reflected_xss_probe("ep14", "Search", "/search/{query}", path_params=["query"])
    s2 = build_reflected_xss_probe("ep14", "Search", "/search/{query}", path_params=["query"])
    assert s1 is not None and s2 is not None
    assert s1.chain.steps[0].success_signal != s2.chain.steps[0].success_signal


def test_reflected_xss_probe_returns_none_with_no_candidates():
    result = build_reflected_xss_probe("ep16", "List All", "/resources")
    assert result is None


# ---------------------------------------------------------------------------
# TargetAppClient failed-redirect URL surfacing (open-redirect detection)
# ---------------------------------------------------------------------------

def test_invoke_endpoint_appends_failed_request_url_on_connect_error():
    """A DNS/connect failure to the redirect target must surface that URL
    in the returned response text — this is the sole signal
    build_open_redirect_probe relies on."""
    import asyncio

    import httpx

    from nuguard.redteam.target.client import TargetAppClient

    async def _run():
        client = TargetAppClient(base_url="http://example.invalid")

        async def _raise(*args, **kwargs):
            request = httpx.Request("GET", "https://nuguard-oob-deadbeef.invalid/redirect-canary")
            raise httpx.ConnectError("Name or service not known", request=request)

        client._client.request = _raise  # type: ignore[method-assign]
        status_code, response, _ = await client.invoke_endpoint(path="/redir", method="GET")
        assert status_code == 0
        assert "nuguard-oob-deadbeef.invalid" in response

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# ScenarioGenerator._api_attack_scenarios integration
# ---------------------------------------------------------------------------

def test_generator_produces_auth_bypass_for_protected_endpoint():
    node = _api_node("ep1", "List Users", path="/api/users", method="GET", auth_required=True)
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    auth_bypass = [s for s in scenarios if s.scenario_type == ScenarioType.AUTH_BYPASS]
    assert len(auth_bypass) == 1
    assert auth_bypass[0].target_node_ids == [str(_uuid.uuid5(_uuid.NAMESPACE_URL, "ep1"))]


def test_generator_produces_mass_assignment_for_post_endpoint():
    node = _api_node("ep2", "Create User", path="/api/users", method="POST")
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    mass = [s for s in scenarios if s.scenario_type == ScenarioType.MASS_ASSIGNMENT]
    assert len(mass) == 1


def test_generator_produces_idor_for_id_param_endpoint():
    node = _api_node(
        "ep3", "Get User", path="/api/users/{user_id}",
        method="GET", idor_surface=True, path_params=["user_id"]
    )
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    idor = [s for s in scenarios if s.scenario_type == ScenarioType.IDOR]
    assert len(idor) == 1
    assert "/99999" in idor[0].chain.steps[0].target_path


def test_generator_skips_idor_when_no_id_params():
    node = _api_node("ep4", "Get Resource", path="/api/resources/{name}", path_params=["name"])
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    idor = [s for s in scenarios if s.scenario_type == ScenarioType.IDOR]
    assert len(idor) == 0


def test_generator_produces_injection_probe_for_endpoint_with_path_params():
    node = _api_node(
        "ep5", "Search", path="/search/{query}",
        method="GET", path_params=["query"],
    )
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    injection = [s for s in scenarios if s.scenario_type == ScenarioType.SQL_INJECTION]
    assert len(injection) == 1


def test_generator_skips_injection_probe_when_no_params():
    node = _api_node("ep6", "List All", path="/resources", path_params=[])
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    injection = [s for s in scenarios if s.scenario_type == ScenarioType.SQL_INJECTION]
    assert len(injection) == 0


def test_generator_produces_path_traversal_probe_for_file_param_endpoint():
    node = _api_node(
        "ep8", "Download", path="/files/{filename}",
        method="GET", path_params=["filename"],
    )
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    traversal = [s for s in scenarios if s.scenario_type == ScenarioType.PATH_TRAVERSAL]
    assert len(traversal) == 1


def test_generator_skips_path_traversal_probe_when_no_file_param():
    node = _api_node("ep9", "Get User", path="/users/{id}", path_params=["id"])
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    traversal = [s for s in scenarios if s.scenario_type == ScenarioType.PATH_TRAVERSAL]
    assert len(traversal) == 0


def test_generator_produces_open_redirect_probe_for_redirect_param_endpoint():
    node = _api_node(
        "ep10", "Login", path="/login/{next}",
        method="GET", path_params=["next"],
    )
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    redirect = [s for s in scenarios if s.scenario_type == ScenarioType.OPEN_REDIRECT]
    assert len(redirect) == 1


def test_generator_skips_open_redirect_probe_when_no_redirect_param():
    node = _api_node("ep11", "Get User", path="/users/{id}", path_params=["id"])
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    redirect = [s for s in scenarios if s.scenario_type == ScenarioType.OPEN_REDIRECT]
    assert len(redirect) == 0


def test_generator_produces_reflected_xss_probe_for_param_endpoint():
    node = _api_node(
        "ep14", "Search", path="/search/{query}",
        method="GET", path_params=["query"],
    )
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    xss = [s for s in scenarios if s.scenario_type == ScenarioType.REFLECTED_XSS]
    assert len(xss) == 1


def test_generator_skips_reflected_xss_probe_when_no_params():
    node = _api_node("ep15", "List All", path="/resources", path_params=[])
    sbom = _make_sbom([node])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    xss = [s for s in scenarios if s.scenario_type == ScenarioType.REFLECTED_XSS]
    assert len(xss) == 0


def test_generator_no_api_scenarios_without_api_endpoint_nodes():
    # SBOM with only AGENT nodes — no API_ENDPOINT nodes
    agent = Node(
        id=_uuid.uuid5(_uuid.NAMESPACE_URL, "a1"),
        name="ChatAgent",
        component_type=NodeType.AGENT,
        confidence=0.9,
        metadata=NodeMetadata(),
    )
    sbom = _make_sbom([agent])
    gen = ScenarioGenerator(sbom)
    scenarios = gen.generate()
    api_scenarios = [s for s in scenarios if s.goal_type == GoalType.API_ATTACK]
    assert len(api_scenarios) == 0


# ---------------------------------------------------------------------------
# StepResult HTTP_2XX_SENTINEL logic
# ---------------------------------------------------------------------------

def test_step_result_http_2xx_sentinel_success():
    from nuguard.models.exploit_chain import ExploitStep
    step = ExploitStep(
        step_id="s1",
        step_type="INVOKE",
        description="test",
        payload="",
        success_signal=HTTP_2XX_SENTINEL,
    )
    result = StepResult(step=step, response="some response", tool_calls=[], http_status_code=200)
    assert result.success_signal_found is True


def test_step_result_http_2xx_sentinel_failure_on_401():
    from nuguard.models.exploit_chain import ExploitStep
    step = ExploitStep(
        step_id="s1",
        step_type="INVOKE",
        description="test",
        payload="",
        success_signal=HTTP_2XX_SENTINEL,
    )
    result = StepResult(step=step, response="Unauthorized", tool_calls=[], http_status_code=401)
    assert result.success_signal_found is False


def test_step_result_http_2xx_sentinel_failure_on_none_status():
    from nuguard.models.exploit_chain import ExploitStep
    step = ExploitStep(
        step_id="s1",
        step_type="INVOKE",
        description="test",
        payload="",
        success_signal=HTTP_2XX_SENTINEL,
    )
    result = StepResult(step=step, response="error", tool_calls=[], http_status_code=None)
    assert result.success_signal_found is False


# ---------------------------------------------------------------------------
# StepResult DB-error-signature keyword matching (build_injection_probe)
# ---------------------------------------------------------------------------

def test_step_result_db_error_signature_match_on_500():
    from nuguard.models.exploit_chain import ExploitStep
    step = ExploitStep(
        step_id="s1",
        step_type="INVOKE",
        description="test",
        payload="",
        success_signal="|".join(_DB_ERROR_SIGNATURES),
        success_requires_2xx=False,
    )
    response = "Internal Server Error: SQLSTATE[42000]: Syntax error near..."
    result = StepResult(step=step, response=response, tool_calls=[], http_status_code=500)
    assert result.success_signal_found is True


def test_step_result_db_error_signature_no_match_on_clean_response():
    from nuguard.models.exploit_chain import ExploitStep
    step = ExploitStep(
        step_id="s1",
        step_type="INVOKE",
        description="test",
        payload="",
        success_signal="|".join(_DB_ERROR_SIGNATURES),
        success_requires_2xx=False,
    )
    result = StepResult(
        step=step, response='{"results": []}', tool_calls=[], http_status_code=200
    )
    assert result.success_signal_found is False
