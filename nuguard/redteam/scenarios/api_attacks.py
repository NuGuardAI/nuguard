"""Direct HTTP API attack scenario builders (GoalType.API_ATTACK).

These scenarios probe REST endpoints discovered in the SBOM by sending raw HTTP
requests — completely bypassing the agent chat interface.  Each step carries
``target_path`` so the executor routes to ``TargetAppClient.invoke_endpoint``
instead of the chat endpoint.
"""
from __future__ import annotations

import re
import uuid

from nuguard.models.exploit_chain import (
    ExploitChain,
    ExploitStep,
    GoalType,
    HTTP_2XX_SENTINEL,
    ScenarioType,
)

from .pre_scorer import pre_score
from .scenario_types import AttackScenario

# Fields commonly accepted by web frameworks that grant elevated privilege when
# accepted blindly (mass-assignment / parameter pollution).
_MASS_ASSIGN_BODY: dict = {
    "role": "admin",
    "is_admin": True,
    "privilege_level": "superuser",
    "is_superuser": True,
    "permissions": ["admin", "write", "delete"],
}

_ID_PARAM_PATTERN = re.compile(
    r"\{(?P<param>[^}]+)\}|:(?P<bare>[A-Za-z_][A-Za-z0-9_]*)"
)
_ID_LIKE = {"id", "user_id", "tenant_id", "account_id", "customer_id", "org_id"}


def _replace_first_id_param(path: str, path_params: list[str]) -> str | None:
    """Return *path* with the first ID-like param replaced by a probe value.

    Returns ``None`` when no replaceable parameter is found.
    """
    for param in path_params:
        if param.lower() in _ID_LIKE:
            # Replace both {param} and :param styles
            replaced = re.sub(
                rf"\{{{re.escape(param)}\}}|:{re.escape(param)}\b",
                "99999",
                path,
            )
            if replaced != path:
                return replaced
    return None


def build_auth_bypass(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    method: str = "GET",
) -> AttackScenario:
    """Build an authentication bypass scenario for a protected endpoint.

    Sends a request to *path* without any auth headers.  Success is defined as
    receiving a 2xx response — which means the endpoint failed to enforce auth.
    """
    chain_id = str(uuid.uuid4())
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.AUTH_BYPASS,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI03 – Identity and Privilege Abuse",
        owasp_llm_ref="LLM05 – Improper Output Handling",
        steps=[
            ExploitStep(
                step_id=f"{chain_id}_s1",
                step_type="INVOKE",
                description=f"Access {endpoint_name} without authentication",
                payload="",
                target_path=path,
                http_method=method,
                target_node_id=endpoint_id,
                success_signal=HTTP_2XX_SENTINEL,
                on_failure="abort",
            )
        ],
    )
    chain.pre_score = pre_score(chain, has_unauth_entry=True)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.AUTH_BYPASS,
        title=f"Authentication Bypass — {endpoint_name}",
        description=(
            f"Probe {method} {path} without credentials.  "
            f"A 2xx response confirms authentication is not enforced."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )


def build_mass_assignment(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    method: str = "POST",
) -> AttackScenario:
    """Build a mass-assignment scenario for a write-capable endpoint.

    Sends extra privilege-escalating fields (role, is_admin, etc.) in the
    request body.  Success is detection of an echoed privilege value in the
    response, indicating the server accepted and reflected the extra fields.
    """
    chain_id = str(uuid.uuid4())
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.MASS_ASSIGNMENT,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI03 – Identity and Privilege Abuse",
        steps=[
            ExploitStep(
                step_id=f"{chain_id}_s1",
                step_type="INVOKE",
                description=f"Send extra privilege fields to {endpoint_name}",
                payload="",
                target_path=path,
                http_method=method,
                http_body=_MASS_ASSIGN_BODY,
                target_node_id=endpoint_id,
                success_signal="superuser",
                # Require 2xx so that frameworks (e.g. FastAPI) echoing the request
                # body verbatim inside a 422 validation error do not produce a false
                # positive — "superuser" appearing in a rejection is not a success.
                success_requires_2xx=True,
                on_failure="abort",
            )
        ],
    )
    chain.pre_score = pre_score(chain)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.MASS_ASSIGNMENT,
        title=f"Mass Assignment — {endpoint_name}",
        description=(
            f"POST privilege-escalating fields (role, is_admin, is_superuser) to "
            f"{method} {path}.  Response echoing 'superuser' confirms mass assignment."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )


def build_idor(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    path_params: list[str],
) -> AttackScenario | None:
    """Build an IDOR (Insecure Direct Object Reference) scenario.

    Substitutes the first ID-like path parameter with a probe value (99999)
    to test whether the server enforces object-level authorisation.

    Returns ``None`` when no substitutable ID parameter is found in *path*.
    """
    probe_path = _replace_first_id_param(path, path_params)
    if probe_path is None:
        return None

    chain_id = str(uuid.uuid4())
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.IDOR,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI03 – Identity and Privilege Abuse",
        steps=[
            ExploitStep(
                step_id=f"{chain_id}_s1",
                step_type="INVOKE",
                description=f"Access {endpoint_name} with a different object ID",
                payload="",
                target_path=probe_path,
                http_method="GET",
                target_node_id=endpoint_id,
                success_signal=HTTP_2XX_SENTINEL,
                on_failure="abort",
            )
        ],
    )
    chain.pre_score = pre_score(chain)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.IDOR,
        title=f"IDOR — {endpoint_name}",
        description=(
            f"Access GET {probe_path} using a different object ID.  "
            f"A 2xx response without explicit authorisation check indicates IDOR."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )
