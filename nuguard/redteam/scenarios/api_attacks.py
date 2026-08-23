"""Direct HTTP API attack scenario builders (GoalType.API_ATTACK).

These scenarios probe REST endpoints discovered in the SBOM by sending raw HTTP
requests — completely bypassing the agent chat interface.  Each step carries
``target_path`` so the executor routes to ``TargetAppClient.invoke_endpoint``
instead of the chat endpoint.
"""
from __future__ import annotations

import re
import uuid

from nuguard.common.response_extraction import build_minimal_payload as _build_realistic_body
from nuguard.models.exploit_chain import (
    HTTP_2XX_SENTINEL,
    ExploitChain,
    ExploitStep,
    GoalType,
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
    request_body_schema: dict[str, str] | None = None,
    sensitive_fields: list[str] | None = None,
) -> AttackScenario:
    """Build an authentication bypass scenario for a protected endpoint.

    Sends a request to *path* without any auth headers.  Success is defined as
    receiving a 2xx response — which means the endpoint failed to enforce auth.

    For POST/PUT/PATCH endpoints a realistic body is generated from
    *request_body_schema* so that the request passes schema validation and
    reaches the authentication check rather than being rejected with 422.

    *sensitive_fields* (from SBOM ``pii_fields``/``phi_fields``/``pfi_fields``
    metadata on the endpoint or its backing datastore) is used at execution
    time to check whether the bypassed response actually contains sensitive
    data, and here to boost the pre-execution impact score.
    """
    # Build a realistic body for write-method endpoints so the request
    # passes schema validation — auth bypass must be tested on a valid request.
    http_body: dict | None = None
    if method in ("POST", "PUT", "PATCH") and request_body_schema:
        http_body = _build_realistic_body(request_body_schema)

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
                http_body=http_body,
                target_node_id=endpoint_id,
                success_signal=HTTP_2XX_SENTINEL,
                on_failure="abort",
                use_llm_eval=True,
                strip_auth=True,
                sensitive_fields=sensitive_fields or [],
            )
        ],
    )
    chain.pre_score = pre_score(
        chain, has_unauth_entry=True, pii_in_path=bool(sensitive_fields)
    )
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
    request_body_schema: dict[str, str] | None = None,
) -> AttackScenario:
    """Build a mass-assignment scenario for a write-capable endpoint.

    Sends extra privilege-escalating fields (role, is_admin, etc.) in the
    request body.  Success is detection of an echoed privilege value in the
    response, indicating the server accepted and reflected the extra fields.

    When *request_body_schema* is provided (from the SBOM), plausible values are
    generated for all required fields so the request passes schema validation.
    The privilege-escalation fields are then merged on top: the combined body
    looks like a legitimate request that also smuggles in the escalation fields.
    """
    # Start with plausible values for all known schema fields so the request
    # passes validation, then overlay the privilege-escalation fields.
    base_body = _build_realistic_body(request_body_schema) if request_body_schema else {}
    body = {**base_body, **_MASS_ASSIGN_BODY}

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
                http_body=body,
                target_node_id=endpoint_id,
                # OR-matched keywords: the literal escalation value ("superuser")
                # or a JSON-shaped echo of role/is_admin/is_superuser being
                # accepted — catches servers that echo the field name+value back
                # without necessarily using the word "superuser" itself.
                success_signal=(
                    "superuser"
                    '|"role": "admin"|"role":"admin"'
                    '|"is_admin": true|"is_admin":true'
                    '|"is_superuser": true|"is_superuser":true'
                ),
                # Require 2xx so that frameworks (e.g. FastAPI) echoing the request
                # body verbatim inside a 422 validation error do not produce a false
                # positive — the escalation keywords appearing in a rejection is
                # not a success.
                success_requires_2xx=True,
                on_failure="abort",
                use_llm_eval=True,
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
            f"{method} {path}.  A 2xx response echoing back an accepted "
            f"escalation field (role=admin, is_admin, is_superuser, or "
            f"privilege_level=superuser) confirms mass assignment."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )


def build_auth_scope_bypass(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    method: str = "GET",
    auth_scope: str | None = None,
    auth_detail: str | None = None,
    request_body_schema: dict[str, str] | None = None,
) -> AttackScenario:
    """BFLA/RBAC probe — access an endpoint with a lower-privileged or mismatched scope."""
    scope_label = auth_scope or auth_detail or "elevated scope"
    body: dict = _build_realistic_body(request_body_schema or {})
    chain_id = str(uuid.uuid4())
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.AUTH_BYPASS,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI03 – Identity and Privilege Abuse",
        steps=[
            ExploitStep(
                step_id=f"{chain_id}_s1",
                step_type="INVOKE",
                description=f"Access {endpoint_name} with downgraded scope (BFLA probe)",
                payload="",
                target_path=path,
                http_method=method,
                http_body=body if body else None,
                target_node_id=endpoint_id,
                success_signal=HTTP_2XX_SENTINEL,
                on_failure="abort",
                use_llm_eval=True,
            )
        ],
    )
    chain.pre_score = pre_score(chain)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.AUTH_BYPASS,
        title=f"BFLA/Scope Bypass — {endpoint_name}",
        description=(
            f"Access {method} {path} without required scope '{scope_label}'. "
            "A 2xx response indicates broken function-level authorization."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )


def build_rate_limit_probe(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    method: str = "GET",
    request_body_schema: dict[str, str] | None = None,
    burst_count: int = 5,
) -> AttackScenario:
    """Safe rate-limit probe — send a bounded burst and check for 429 enforcement."""
    body: dict = _build_realistic_body(request_body_schema or {})
    chain_id = str(uuid.uuid4())
    steps = [
        ExploitStep(
            step_id=f"{chain_id}_s{i + 1}",
            step_type="INVOKE",
            description=f"Rate-limit burst {i + 1}/{burst_count} to {endpoint_name}",
            payload="",
            target_path=path,
            http_method=method,
            http_body=body if body else None,
            target_node_id=endpoint_id,
            depends_on=[f"{chain_id}_s{i}"] if i > 0 else [],
            on_failure="skip",
        )
        for i in range(burst_count)
    ]
    # Last step probes for 429 enforcement — missing 429 = rate-limit bypass
    steps[-1].success_signal = HTTP_2XX_SENTINEL
    steps[-1].on_failure = "abort"
    steps[-1].use_llm_eval = True
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.AUTH_BYPASS,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI05",
        steps=steps,
    )
    chain.pre_score = pre_score(chain)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.AUTH_BYPASS,
        title=f"Rate-Limit Probe — {endpoint_name} ({burst_count} requests)",
        description=(
            f"Send {burst_count} rapid {method} {path} requests. "
            "A 2xx on the final request without a 429 earlier indicates missing rate-limit enforcement."
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
    sensitive_fields: list[str] | None = None,
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
                use_llm_eval=True,
                sensitive_fields=sensitive_fields or [],
            )
        ],
    )
    chain.pre_score = pre_score(chain, pii_in_path=bool(sensitive_fields))
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.IDOR,
        title=f"IDOR — {endpoint_name}",
        description=(
            f"Access GET {probe_path} using a different object ID.  "
            f"A 2xx response containing another record's data (not just any "
            f"2xx) indicates IDOR."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )


def build_open_data_exposure(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    method: str = "GET",
    sensitive_fields: list[str] | None = None,
) -> AttackScenario:
    """Probe an already-open (no-auth-required) endpoint for data exposure.

    Unlike :func:`build_auth_bypass`, this endpoint requires no
    authentication by design — there is no auth check to bypass.  The
    question is simply whether it returns sensitive data (PII/PHI/PFI
    declared on its backing datastore) to anyone who calls it.  Success
    requires HTTP 2xx *and* evidence the body actually contains that data —
    see ``response_data_check.check_response_for_data_exposure``.
    """
    chain_id = str(uuid.uuid4())
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI03 – Identity and Privilege Abuse",
        owasp_llm_ref="LLM06 – Sensitive Information Disclosure",
        steps=[
            ExploitStep(
                step_id=f"{chain_id}_s1",
                step_type="INVOKE",
                description=f"Probe open endpoint {endpoint_name} for sensitive data exposure",
                payload="",
                target_path=path,
                http_method=method,
                target_node_id=endpoint_id,
                success_signal=HTTP_2XX_SENTINEL,
                on_failure="abort",
                use_llm_eval=True,
                sensitive_fields=sensitive_fields or [],
            )
        ],
    )
    chain.pre_score = pre_score(chain, has_unauth_entry=True, pii_in_path=True)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.DIRECT_PII_EXTRACTION,
        title=f"Open Endpoint Data Exposure — {endpoint_name}",
        description=(
            f"Call {method} {path}, an endpoint that requires no authentication "
            f"by design and is declared to return sensitive data.  A response "
            f"containing PII/PHI/PFI values or field names confirms "
            f"unrestricted data exposure."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )
