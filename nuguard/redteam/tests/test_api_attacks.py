"""Tests for direct HTTP API attack scenario builders and generator integration."""
from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from nuguard.models.exploit_chain import HTTP_2XX_SENTINEL, GoalType, ScenarioType
from nuguard.redteam.executor.executor import StepResult
from nuguard.redteam.scenarios.api_attacks import (
    _replace_first_id_param,
    build_auth_bypass,
    build_idor,
    build_mass_assignment,
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
    assert step.success_signal == "superuser"
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


def test_idor_returns_none_when_no_id_param():
    result = build_idor("ep3", "Get Resource", "/resources/{name}", ["name"])
    assert result is None


def test_idor_returns_none_when_no_path_params():
    result = build_idor("ep3", "List All", "/resources", [])
    assert result is None


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
