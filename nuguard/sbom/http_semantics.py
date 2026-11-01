"""Language- and framework-agnostic HTTP semantics for API_ENDPOINT nodes.

Derives the pentest-oriented facts from ``documentation/developer-specs/
pentest-proposal.md`` Part A that every framework adapter would otherwise have
to re-implement: identity-role tagging (A1), object-ID semantics (A2), mutation
semantics and compensating actions (A3), identity binding, and per-app session
cookie semantics (A6).

The inference runs once, after extraction, over the *normalized* node metadata
(route template, methods, ``http_request`` parameters, ``request_body_schema``)
so Flask, FastAPI, Express, NestJS, Spring, ASP.NET and Go endpoints all get the
same treatment. Adapters contribute only what the node metadata cannot express —
handler-body evidence — through ``metadata.extras["handler_signals"]``:

``identity_injection``  list[str] — the handler resolves the caller from the
                        credential (``Depends(get_current_user)``, ``@AuthenticationPrincipal``…)
``audit_logged``        bool — the handler writes an audit-log record
``sets_cookie``         bool — the handler sets a cookie / uses a server-side session

Every heuristic is name/shape based and conservative; ``unknown`` is the
default and never blocks a scan. Nothing here reads a live target or persists a
runtime observation (pentest-proposal A7).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .models import (
    CompensatingAction,
    HttpParameterMetadata,
    HttpRequestMetadata,
    IdentityRole,
    IdentityRoleEvidence,
    MutationSemantics,
    ObjectIdSemantics,
    SessionCookieSemantics,
)
from .types import ComponentType

if TYPE_CHECKING:
    from .models import AiSbomDocument, Node, NodeMetadata

HANDLER_SIGNALS_KEY = "handler_signals"

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_BODY_LOCATIONS = frozenset({"json", "form", "multipart"})

# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")
_TEMPLATE_PARAM_RE = re.compile(r"\{[^}]*\}|<[^>]*>|\[[^\]]*\]|:[A-Za-z_]\w*|\$\{[^}]*\}")
_TEMPLATE_PARAM_NAME_RE = re.compile(r"\{\s*(?:\*\*)?(\w+)[^}]*\}|<(?:[^:>]+:)?(\w+)>|:(\w+)|\[(\w+)\]")


def normalize_name(name: str) -> str:
    """``X-User-Id`` / ``userId`` / ``user_id`` all become ``user_id``."""
    snake = _NON_ALNUM_RE.sub("_", _CAMEL_RE.sub("_", name.strip())).strip("_").lower()
    return snake[2:] if snake.startswith("x_") and len(snake) > 2 else snake


def _tokens(text: str) -> list[str]:
    return [t for t in normalize_name(text).split("_") if t]


def _singular(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


# ---------------------------------------------------------------------------
# A1 — identity roles
# ---------------------------------------------------------------------------

# ``from_account_id`` / ``source_user_id`` name the acting party's own resource, so a
# client-supplied value is as spoofable as a bare ``account_id``. ``to_*`` / ``target_*``
# name a counterparty and are deliberately not subject identifiers.
_SUBJECT_RE = re.compile(
    r"^(?:(?:from|source|src|sender|payer)_)?"
    r"(?:user|account|acct|caller|subject|customer|profile|member|owner|actor|requester)"
    r"_?(?:id|uuid)$"
)
_SUBJECT_NAMES = frozenset({"sub", "current_user", "current_user_id", "uid"})
_TENANT_RE = re.compile(
    r"^(?:tenant|org|organization|organisation|workspace|team|company)_?(?:id|uuid|slug)$"
)
_TENANT_NAMES = frozenset({"tenant"})
_ROLE_NAMES = frozenset({
    "role", "roles", "user_role", "permission", "permissions", "scope", "scopes",
    "is_admin", "isadmin", "admin", "is_superuser", "is_staff", "access_level",
    "privilege", "privileges",
})
# A bare ``id`` path parameter takes its meaning from the preceding segment.
_BARE_ID_NAMES = frozenset({"id", "pk", "uuid"})
_SUBJECT_SEGMENTS = frozenset({"user", "account", "customer", "profile", "member"})
_TENANT_SEGMENTS = frozenset({"tenant", "org", "organization", "organisation", "workspace", "team", "company"})


def infer_identity_role(
    name: str, location: str, path: str | None = None
) -> tuple[IdentityRole, IdentityRoleEvidence] | None:
    """Conservatively classify a request parameter as an identity field.

    Returns ``(role, evidence)`` or ``None`` when the parameter is not
    identity-related. ``username``/``login`` are deliberately *not* subject
    identifiers — on a login route they are credentials, not a spoofable
    identity claim.
    """
    norm = normalize_name(name)
    if not norm:
        return None
    if _SUBJECT_RE.match(norm) or norm in _SUBJECT_NAMES:
        return "subject_id", "name_match"
    if _TENANT_RE.match(norm) or norm in _TENANT_NAMES:
        return "tenant_id", "name_match"
    if norm in _ROLE_NAMES:
        return "role_hint", "name_match"
    if location == "path" and norm in _BARE_ID_NAMES and path:
        segment = _segment_before_param(path, name)
        if segment in _SUBJECT_SEGMENTS:
            return "subject_id", "path_context"
        if segment in _TENANT_SEGMENTS:
            return "tenant_id", "path_context"
    return None


def _segment_before_param(path: str, param: str) -> str | None:
    """Singularized literal path segment immediately before *param* in *path*."""
    segments = [s for s in path.split("/") if s]
    for index, segment in enumerate(segments):
        names = {g for m in _TEMPLATE_PARAM_NAME_RE.finditer(segment) for g in m.groups() if g}
        if param in names and index > 0:
            previous = segments[index - 1]
            if _TEMPLATE_PARAM_RE.fullmatch(previous):
                return None
            tokens = _tokens(previous)
            return _singular(tokens[-1]) if tokens else None
    return None


# ---------------------------------------------------------------------------
# Endpoint helpers
# ---------------------------------------------------------------------------


def _endpoint_path(meta: NodeMetadata) -> str:
    return (meta.endpoint or str(meta.extras.get("api_endpoint", "") or "")).strip()


def _path_param_names(meta: NodeMetadata) -> list[str]:
    """Path parameter names in route order, from every source an adapter may have used.

    ``meta.path_params`` only understands ``{id}`` and ``:id`` templates; Flask's
    ``<int:id>`` and ``[id]`` routes (and adapters that emit only ``http_request``)
    are recovered from the template and the structured parameters.
    """
    names: list[str] = list(meta.path_params or [])
    for segment in (s for s in _endpoint_path(meta).split("/") if s):
        names.extend(g for m in _TEMPLATE_PARAM_NAME_RE.finditer(segment) for g in m.groups() if g)
    if meta.http_request:
        names.extend(p.name for p in meta.http_request.parameters if p.location == "path")
    return list(dict.fromkeys(names))


def _methods(meta: NodeMetadata) -> list[str]:
    """Every concrete HTTP method the route accepts (upper-case, no ``UNKNOWN``)."""
    raw = list(meta.http_request.methods) if meta.http_request and meta.http_request.methods else []
    if meta.method:
        raw.append(meta.method)
    return [m for m in dict.fromkeys(x.upper() for x in raw) if m not in ("UNKNOWN", "ANY", "*")]


def _effective_method(methods: list[str]) -> str | None:
    """The state-changing method if any, else the first safe one."""
    for preferred in ("DELETE", "POST", "PUT", "PATCH"):
        if preferred in methods:
            return preferred
    return methods[0] if methods else None


def _signals(meta: NodeMetadata) -> dict[str, Any]:
    raw = meta.extras.get(HANDLER_SIGNALS_KEY)
    return raw if isinstance(raw, dict) else {}


def _template(path: str) -> str:
    """Route template with every parameter collapsed to ``{}`` and no trailing slash."""
    return _TEMPLATE_PARAM_RE.sub("{}", path).rstrip("/") or "/"


def _is_api_endpoint(node: Node) -> bool:
    return node.component_type == ComponentType.API_ENDPOINT and node.metadata is not None


def _literal_segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s and not _TEMPLATE_PARAM_RE.fullmatch(s)]


# ---------------------------------------------------------------------------
# A1 — apply identity roles + identity binding
# ---------------------------------------------------------------------------


def apply_identity_roles(meta: NodeMetadata) -> None:
    """Tag identity parameters on *meta* and derive ``identity_binding``.

    Existing tags are never overwritten. Identity-shaped body fields that an
    adapter reported only through ``request_body_schema`` (FastAPI Pydantic
    bodies, Go structs) and path parameters of adapters that emit no
    ``http_request`` (Express, Go) are added as parameters so the trust-context
    matrix and authorization-replay see them in one place.
    """
    path = _endpoint_path(meta)
    methods = _methods(meta)
    is_write = bool(_WRITE_METHODS.intersection(methods))
    request = meta.http_request
    parameters: list[HttpParameterMetadata] = list(request.parameters) if request else []
    known = {(p.name, p.location) for p in parameters}
    added: list[HttpParameterMetadata] = []

    def add(name: str, location: Any, type_hint: str, required: bool) -> None:
        if (name, location) in known:
            return
        found = infer_identity_role(name, location, path)
        if found is None:
            return
        known.add((name, location))
        added.append(
            HttpParameterMetadata(
                name=name, location=location, type_hint=type_hint, required=required,
                identity_role=found[0], identity_role_evidence=found[1],
            )
        )

    for field, raw_type in (meta.request_body_schema or {}).items():
        add(str(field), "json", _type_hint(str(raw_type or "")), False)
    for field in meta.context_payload_fields or {}:
        add(str(field), "json", "string", False)
    for name in _path_param_names(meta):
        add(name, "path", "string", True)

    for param in parameters:
        if param.identity_role is None:
            found = infer_identity_role(param.name, param.location, path)
            if found is not None:
                param.identity_role, param.identity_role_evidence = found
        if (
            param.identity_role == "role_hint"
            and param.location in _BODY_LOCATIONS
            and is_write
            and param.mass_assignment_risk is None
        ):
            param.mass_assignment_risk = True

    if added:
        for param in added:
            if param.identity_role == "role_hint" and param.location in _BODY_LOCATIONS and is_write:
                param.mass_assignment_risk = True
        if request is None and "WEBSOCKET" not in methods:
            meta.http_request = HttpRequestMetadata(
                methods=methods or ["UNKNOWN"], parameters=added
            )
        elif request is not None:
            request.parameters = [*parameters, *added]

    client_supplied = any(
        p.identity_role in ("subject_id", "tenant_id")
        for p in (meta.http_request.parameters if meta.http_request else [])
    )
    injected = bool(_signals(meta).get("identity_injection"))
    if meta.identity_binding is None and (client_supplied or injected):
        meta.identity_binding = (
            "mixed" if client_supplied and injected
            else "client_supplied" if client_supplied
            else "credential_bound"
        )


def _type_hint(raw: str) -> str:
    lowered = raw.lower()
    for needle, hint in (("int", "int"), ("float", "float"), ("bool", "bool"), ("uuid", "uuid")):
        if needle in lowered:
            return hint
    return "string"


# ---------------------------------------------------------------------------
# A3 — mutation semantics
# ---------------------------------------------------------------------------

_PAYMENT_TOKENS = frozenset({
    "transfer", "pay", "payment", "payout", "withdraw", "withdrawal", "deposit", "refund",
    "charge", "checkout", "purchase", "wire", "remit", "remittance", "billpay", "transaction",
})
_AUTH_STATE_TOKENS = frozenset({
    "password", "passwd", "login", "logout", "signin", "signout", "refresh", "role",
    "permission", "mfa", "otp", "2fa", "token", "apikey", "impersonate", "revoke",
})
_DELETE_TOKENS = frozenset({
    "delete", "remove", "destroy", "cancel", "close", "terminate", "purge", "wipe", "drop", "erase",
})
_UPDATE_TOKENS = frozenset({
    "update", "edit", "modify", "set", "change", "freeze", "unfreeze", "block", "unblock",
    "lock", "unlock", "enable", "disable", "activate", "deactivate", "subscribe", "unsubscribe",
    "archive", "unarchive", "restore", "approve", "reject", "confirm", "toggle", "rename", "assign",
})
_CREATE_TOKENS = frozenset({
    "create", "add", "new", "register", "signup", "submit", "upload", "import", "invite",
})
_READ_TOKENS = frozenset({
    "search", "query", "list", "find", "lookup", "get", "fetch", "count", "validate",
    "verify", "check", "preview", "calculate", "estimate", "quote", "health", "ping",
})
_CONFIRM_TOKENS = frozenset({
    "confirm", "confirmation", "confirmed", "otp", "mfa", "challenge", "approval", "approved",
})
_IDEMPOTENCY_KEYS = frozenset({
    "idempotency_key", "idempotency_id", "dedupe_key", "deduplication_id", "client_request_id",
})
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
# Singular verb-like last segments that make even a GET suspicious (state-changing GET).
_UNSAFE_GET_TOKENS = _PAYMENT_TOKENS | _DELETE_TOKENS | frozenset({"freeze", "unfreeze", "block", "revoke"})


def _verb_kind(segments: list[str]) -> tuple[str, str] | None:
    """Mutation kind from the last two literal path segments, with the matched token.

    Kinds are checked in priority order across both segments, so the confirm
    leg of a transfer (``/transfers/{id}/confirm``) is still a payment.
    """
    tokens = [t for segment in reversed(segments[-2:]) for t in _tokens(segment)]
    for table, kind in (
        (_PAYMENT_TOKENS, "payment"),
        (_AUTH_STATE_TOKENS, "auth_state"),
        (_DELETE_TOKENS, "delete"),
        (_UPDATE_TOKENS, "update"),
        (_CREATE_TOKENS, "create"),
        (_READ_TOKENS, "none"),
    ):
        for token in tokens:
            if token in table or _singular(token) in table:
                return kind, token
    return None


def infer_mutation_semantics(meta: NodeMetadata) -> MutationSemantics | None:
    """Static state-effect classification of one endpoint (cross-endpoint links added later)."""
    methods = _methods(meta)
    method = _effective_method(methods)
    if method is None or method == "WEBSOCKET":
        return None
    segments = _literal_segments(_endpoint_path(meta))
    verb = _verb_kind(segments)
    evidence = [f"http_method:{method}"]

    kind: str
    if method in _SAFE_METHODS:
        unsafe_get = bool(segments) and any(t in _UNSAFE_GET_TOKENS for t in _tokens(segments[-1]))
        kind = "unknown" if unsafe_get else "none"
        if unsafe_get:
            evidence.append("state_changing_get")
    elif method == "DELETE":
        kind = "delete"  # the method wins over a path verb such as '/cancel'
    elif verb is not None:
        kind = verb[0]
        evidence.append(f"path_verb:{verb[1]}")
    elif method in ("PUT", "PATCH"):
        kind = "update"
    elif method == "POST":
        kind = "create"
    else:
        kind = "unknown"

    params = meta.http_request.parameters if meta.http_request else []
    param_tokens = {t for p in params for t in _tokens(p.name)}
    param_names = {normalize_name(p.name) for p in params}
    param_names.update(normalize_name(str(k)) for k in (meta.request_body_schema or {}))
    param_tokens.update(t for n in param_names for t in n.split("_") if t)
    has_key = bool(param_names & _IDEMPOTENCY_KEYS)

    idempotency: str
    if kind == "none":
        idempotency = "idempotent"
    elif has_key:
        idempotency = "idempotent"
        evidence.append("idempotency_key_param")
    elif method in _IDEMPOTENT_METHODS:
        idempotency = "idempotent"
    elif method in ("POST", "PATCH"):
        idempotency = "non_idempotent"
    else:
        idempotency = "unknown"

    confirmation: bool | None = None
    if kind != "none":
        if param_tokens & _CONFIRM_TOKENS:
            confirmation = True
            evidence.append("confirm_param")
        elif kind in ("payment", "delete") and meta.http_request is not None and (
            not meta.http_request.has_unresolved_inputs
        ):
            confirmation = False

    audit = _signals(meta).get("audit_logged")
    return MutationSemantics(
        mutation_kind=kind,  # type: ignore[arg-type]
        idempotency=idempotency,  # type: ignore[arg-type]
        confirmation_required=confirmation,
        audit_logged=audit if isinstance(audit, bool) else None,
        evidence=evidence,
    )


_INVERSE_VERBS: dict[str, set[str]] = {}
for _a, _b in (
    ("freeze", "unfreeze"), ("block", "unblock"), ("lock", "unlock"), ("enable", "disable"),
    ("activate", "deactivate"), ("subscribe", "unsubscribe"), ("archive", "unarchive"),
    ("archive", "restore"), ("follow", "unfollow"), ("mute", "unmute"), ("publish", "unpublish"),
    ("open", "close"), ("add", "remove"), ("hold", "release"), ("suspend", "resume"),
):
    _INVERSE_VERBS.setdefault(_a, set()).add(_b)
    _INVERSE_VERBS.setdefault(_b, set()).add(_a)


def _inverse_candidates(token: str) -> set[str]:
    found = set(_INVERSE_VERBS.get(token, ()))
    found.add("un" + token)
    if token.startswith("un") and len(token) > 3:
        found.add(token[2:])
    return found


def link_compensating_actions(endpoints: list[Node]) -> None:
    """Pair symmetric routes into rollback recipes (freeze↔unfreeze, create↔DELETE).

    Pure route-template diffing over the endpoint set — same resource family,
    opposite verb — with no app names. Both halves of a verb pair are linked,
    so either can be rolled back by the other.
    """
    entries: list[tuple[Node, str, str, str]] = []  # node, method, raw path, template
    for node in endpoints:
        meta = node.metadata
        method = _effective_method(_methods(meta))
        path = _endpoint_path(meta)
        if method and path.startswith("/") and meta.mutation_semantics is not None:
            entries.append((node, method, path, _template(path)))
    by_key = {(method, template): (node, path) for node, method, path, template in entries}

    def link(node: Node, method: str, path: str) -> None:
        sem = node.metadata.mutation_semantics
        if sem is None or sem.mutation_kind in ("none", "unknown") or sem.compensating_action:
            return
        sem.reversible = True
        sem.compensating_action = CompensatingAction(method=method, path=path)
        sem.evidence.append("compensating_route")

    for node, method, path, template in entries:
        sem = node.metadata.mutation_semantics
        assert sem is not None
        if sem.mutation_kind in ("none", "unknown"):
            continue
        prefix, _, last = template.rpartition("/")
        last_token = "_".join(_tokens(last)) if last and last != "{}" else ""
        if last_token:
            for inverse in _inverse_candidates(last_token):
                hit = by_key.get((method, f"{prefix}/{inverse.replace('_', '-')}")) or by_key.get(
                    (method, f"{prefix}/{inverse}")
                )
                if hit is not None and hit[0] is not node:
                    link(node, method, hit[1])
                    break
        if method == "POST" and sem.mutation_kind == "create" and last != "{}":
            hit = by_key.get(("DELETE", f"{template}/{{}}"))
            if hit is not None:
                link(node, "DELETE", hit[1])


def link_two_phase_confirmation(endpoints: list[Node]) -> None:
    """Mark a mutation ``confirmation_required`` when a sibling ``…/confirm`` route exists."""
    confirm_prefixes: set[str] = set()
    for node in endpoints:
        path = _endpoint_path(node.metadata)
        segments = [s for s in path.split("/") if s]
        if segments and any(t in _CONFIRM_TOKENS for t in _tokens(segments[-1])):
            confirm_prefixes.add(_template("/" + "/".join(segments[:-1])))
    if not confirm_prefixes:
        return
    for node in endpoints:
        sem = node.metadata.mutation_semantics
        if sem is None or sem.mutation_kind in ("none", "unknown"):
            continue
        template = _template(_endpoint_path(node.metadata))
        if any(p == template or p.startswith(template + "/") for p in confirm_prefixes) and (
            sem.confirmation_required is not True
        ):
            sem.confirmation_required = True
            sem.evidence.append("two_phase_route")


# ---------------------------------------------------------------------------
# A2 — object-ID semantics
# ---------------------------------------------------------------------------

_ID_NAME_RE = re.compile(r"(?:^|_)(?:id|uuid|ulid|guid|pk|number|no|slug)$")


def _id_format(name: str, type_hint: str) -> tuple[str | None, bool | None, str]:
    """``(id_format, sequential, enumerability)`` from the parameter's name and declared type."""
    norm, hint = normalize_name(name), (type_hint or "").lower()
    if "uuid" in norm or "guid" in norm or hint in ("uuid", "guid"):
        return "uuid", False, "low"
    if "ulid" in norm:
        return "ulid", False, "low"
    if hint in ("int", "integer", "long", "number"):
        return "int", True, "high"
    return None, None, "unknown"


def infer_object_id_semantics(meta: NodeMetadata) -> ObjectIdSemantics | None:
    """Object-ID facts for the parameter that addresses one object, when there is one."""
    path = _endpoint_path(meta)
    params: dict[tuple[str, str], HttpParameterMetadata] = {
        (p.name, p.location): p for p in (meta.http_request.parameters if meta.http_request else [])
    }
    target: tuple[str, str] | None = None
    path_params = _path_param_names(meta)
    for name in reversed(path_params):
        if _ID_NAME_RE.search(normalize_name(name)) or infer_identity_role(name, "path", path):
            target = (name, "path")
            break
    if target is None and path_params:
        # An unconventionally named parameter (``/payments/{pid}``) still addresses one object.
        target = (path_params[-1], "path")
    if target is None:
        for (name, location), candidate in params.items():
            if candidate.identity_role == "subject_id" and location != "header":
                target = (name, location)
                break
    if target is None:
        return None
    param = params.get(target)
    id_format, sequential, enumerability = _id_format(target[0], param.type_hint if param else "")
    return ObjectIdSemantics(
        param_name=target[0], id_format=id_format, sequential=sequential, enumerability=enumerability,  # type: ignore[arg-type]
    )


def link_sibling_read_paths(endpoints: list[Node]) -> None:
    """Fill ``sibling_read_paths`` — other reads of the same object family."""
    reads: list[tuple[str, str]] = []  # (template, raw path)
    for node in endpoints:
        meta = node.metadata
        sem = meta.mutation_semantics
        path = _endpoint_path(meta)
        if path.startswith("/") and sem is not None and sem.mutation_kind == "none":
            reads.append((_template(path), path))
    for node in endpoints:
        meta = node.metadata
        oid = meta.object_id_semantics
        path = _endpoint_path(meta)
        if oid is None or not path.startswith("/") or oid.sibling_read_paths:
            continue
        template = _template(path)
        if oid.param_name not in _path_param_names(meta):
            continue
        # Family = route up to and including the object parameter, e.g. /accounts/{}.
        head, _, _ = template.partition("{}")
        family = head.rstrip("/") + "/{}" if "{}" in template else template
        collection = family.rsplit("/", 1)[0] or "/"
        siblings = sorted({
            raw for tpl, raw in reads
            if raw != path and (tpl == collection or tpl == family or tpl.startswith(family + "/"))
        })
        oid.sibling_read_paths = siblings[:10]


# ---------------------------------------------------------------------------
# A6 — session cookie semantics
# ---------------------------------------------------------------------------


def apply_session_cookie_semantics(endpoints: list[Node]) -> None:
    """``set_cookie_expected`` per endpoint, from handler analysis across the whole app.

    An endpoint is *analyzed* when its adapter reported ``sets_cookie``. The app
    expects cookies when any analyzed handler sets one; endpoints no adapter
    analyzed stay ``None`` rather than being guessed.
    """
    analyzed = [n for n in endpoints if isinstance(_signals(n.metadata).get("sets_cookie"), bool)]
    if not analyzed:
        return
    app_uses_cookies = any(_signals(n.metadata)["sets_cookie"] for n in analyzed)
    for node in analyzed:
        if node.metadata.session_cookie_semantics is None:
            node.metadata.session_cookie_semantics = SessionCookieSemantics(
                set_cookie_expected=bool(_signals(node.metadata)["sets_cookie"]) or app_uses_cookies
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def apply_http_semantics(doc: AiSbomDocument) -> None:
    """Populate every HTTP-semantics field on the document's API_ENDPOINT nodes (idempotent)."""
    endpoints = [n for n in doc.nodes if _is_api_endpoint(n)]
    for node in endpoints:
        meta = node.metadata
        apply_identity_roles(meta)
        if meta.mutation_semantics is None:
            meta.mutation_semantics = infer_mutation_semantics(meta)
        if meta.object_id_semantics is None:
            meta.object_id_semantics = infer_object_id_semantics(meta)
    link_compensating_actions(endpoints)
    link_two_phase_confirmation(endpoints)
    link_sibling_read_paths(endpoints)
    apply_session_cookie_semantics(endpoints)
    for node in endpoints:
        node.metadata.extras.pop(HANDLER_SIGNALS_KEY, None)
