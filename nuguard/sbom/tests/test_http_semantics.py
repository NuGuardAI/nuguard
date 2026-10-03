"""Unit tests for the language-agnostic HTTP semantics pass (pentest-proposal Part A)."""
# Tests assert each optional sub-model is present before reading it; the asserts are the guard.
# mypy: disable-error-code="union-attr"

from __future__ import annotations

import pytest

from nuguard.sbom.http_semantics import (
    HANDLER_SIGNALS_KEY,
    apply_http_semantics,
    infer_identity_role,
    normalize_name,
)
from nuguard.sbom.models import (
    AiSbomDocument,
    HttpParameterMetadata,
    HttpRequestMetadata,
    Node,
    NodeMetadata,
)
from nuguard.sbom.types import ComponentType


def _endpoint(
    method: str,
    path: str,
    *,
    params: list[HttpParameterMetadata] | None = None,
    body: dict[str, str] | None = None,
    path_params: list[str] | None = None,
    signals: dict | None = None,
) -> Node:
    meta = NodeMetadata(
        method=method,
        endpoint=path,
        path_params=path_params,
        request_body_schema=body or {},
        http_request=(
            HttpRequestMetadata(methods=[method], parameters=params) if params is not None else None
        ),
    )
    if signals is not None:
        meta.extras[HANDLER_SIGNALS_KEY] = signals
    return Node(
        name=f"{method} {path}", component_type=ComponentType.API_ENDPOINT, confidence=1.0, metadata=meta
    )


def _doc(*nodes: Node) -> AiSbomDocument:
    doc = AiSbomDocument(target="t", nodes=list(nodes))
    apply_http_semantics(doc)
    return doc


# ── A1: identity roles ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "location", "expected"),
    [
        ("user_id", "json", "subject_id"),
        ("userId", "query", "subject_id"),
        ("X-User-Id", "header", "subject_id"),
        ("account_id", "path", "subject_id"),
        ("customerID", "json", "subject_id"),
        ("from_account_id", "json", "subject_id"),
        ("source_user_id", "query", "subject_id"),
        ("sub", "json", "subject_id"),
        ("tenant_id", "json", "tenant_id"),
        ("X-Tenant-Id", "header", "tenant_id"),
        ("orgId", "query", "tenant_id"),
        ("workspace_id", "path", "tenant_id"),
        ("role", "json", "role_hint"),
        ("is_admin", "json", "role_hint"),
        ("permissions", "json", "role_hint"),
    ],
)
def test_identity_role_name_heuristics(name: str, location: str, expected: str) -> None:
    assert infer_identity_role(name, location) == (expected, "name_match")


@pytest.mark.parametrize(
    "name",
    ["username", "login", "message", "amount", "page", "id", "order_id", "to_account_id", "target_user_id"],
)
def test_non_identity_names_are_not_tagged(name: str) -> None:
    assert infer_identity_role(name, "json") is None


def test_bare_id_takes_meaning_from_the_preceding_segment() -> None:
    assert infer_identity_role("id", "path", "/api/accounts/{id}") == ("subject_id", "path_context")
    assert infer_identity_role("id", "path", "/orgs/:id/projects") == ("tenant_id", "path_context")
    assert infer_identity_role("id", "path", "/api/things/<int:id>") is None


def test_normalize_name_collapses_case_styles() -> None:
    assert {normalize_name(n) for n in ("userId", "user_id", "User-Id", "X-User-Id")} == {"user_id"}


def test_body_schema_identity_fields_become_tagged_json_parameters() -> None:
    """FastAPI/Go report body fields only via request_body_schema — they must still be tagged."""
    doc = _doc(_endpoint("POST", "/api/transfers", body={"account_id": "str", "amount": "float", "role": "str"}))
    meta = doc.nodes[0].metadata
    tagged = {p.name: p for p in meta.http_request.parameters if p.identity_role}
    assert set(tagged) == {"account_id", "role"}
    assert tagged["account_id"].location == "json"
    assert tagged["role"].mass_assignment_risk is True  # client-supplied role on a write method
    assert tagged["account_id"].mass_assignment_risk is None
    assert meta.identity_binding == "client_supplied"


def test_role_hint_on_a_read_is_not_a_mass_assignment_risk() -> None:
    param = HttpParameterMetadata(name="role", location="query")
    doc = _doc(_endpoint("GET", "/api/users", params=[param]))
    assert param.identity_role == "role_hint"
    assert param.mass_assignment_risk is None


def test_existing_identity_role_is_never_overwritten() -> None:
    param = HttpParameterMetadata(name="user_id", location="query", identity_role="tenant_id")
    _doc(_endpoint("GET", "/x", params=[param]))
    assert param.identity_role == "tenant_id"


def test_path_params_without_http_request_get_a_minimal_one() -> None:
    """Express/Go emit no http_request; identity path params still surface for the trust matrix."""
    node = _endpoint("GET", "/api/users/:user_id", path_params=["user_id"])
    meta = _doc(node).nodes[0].metadata
    assert meta.http_request is not None
    assert meta.http_request.methods == ["GET"]
    assert [(p.name, p.location, p.identity_role) for p in meta.http_request.parameters] == [
        ("user_id", "path", "subject_id")
    ]


def test_no_identity_signal_leaves_http_request_untouched() -> None:
    meta = _doc(_endpoint("GET", "/api/health")).nodes[0].metadata
    assert meta.http_request is None
    assert meta.identity_binding is None


def test_identity_binding_credential_bound_and_mixed() -> None:
    injected = _endpoint("GET", "/api/me", signals={"identity_injection": ["Depends(get_current_user)"]})
    mixed = _endpoint(
        "GET", "/api/accounts/{account_id}", path_params=["account_id"],
        signals={"identity_injection": ["current_user"]},
    )
    doc = _doc(injected, mixed)
    assert doc.nodes[0].metadata.identity_binding == "credential_bound"
    assert doc.nodes[1].metadata.identity_binding == "mixed"


# ── A3: mutation semantics ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "kind"),
    [
        ("GET", "/api/accounts", "none"),
        ("HEAD", "/api/accounts/{id}", "none"),
        ("POST", "/api/search", "none"),
        ("POST", "/api/items", "create"),
        ("PUT", "/api/items/{id}", "update"),
        ("PATCH", "/api/items/{id}", "update"),
        ("DELETE", "/api/items/{id}", "delete"),
        ("DELETE", "/api/orders/{id}/cancel", "delete"),
        ("POST", "/api/transfers", "payment"),
        ("POST", "/api/accounts/{id}/transfer", "payment"),
        ("POST", "/api/transfers/{id}/confirm", "payment"),
        ("POST", "/auth/login", "auth_state"),
        ("POST", "/auth/refresh", "auth_state"),
        ("POST", "/api/notifications/read-all", "create"),  # marks read: a write, not a read
        ("POST", "/users/{id}/password", "auth_state"),
        ("POST", "/accounts/{id}/freeze", "update"),
        ("POST", "/users/register", "create"),
        ("GET", "/api/transfer", "unknown"),  # state-changing GET is never called "none"
        ("GET", "/api/delete", "unknown"),
    ],
)
def test_mutation_kind(method: str, path: str, kind: str) -> None:
    sem = _doc(_endpoint(method, path)).nodes[0].metadata.mutation_semantics
    assert sem is not None
    assert sem.mutation_kind == kind


def test_websocket_routes_have_no_mutation_semantics() -> None:
    assert _doc(_endpoint("WEBSOCKET", "/ws")).nodes[0].metadata.mutation_semantics is None


def test_idempotency_follows_http_semantics_and_idempotency_keys() -> None:
    plain = _endpoint("POST", "/api/items", params=[])
    keyed = _endpoint(
        "POST", "/api/orders",
        params=[HttpParameterMetadata(name="Idempotency-Key", location="header")],
    )
    put = _endpoint("PUT", "/api/items/{id}")
    doc = _doc(plain, keyed, put)
    assert [n.metadata.mutation_semantics.idempotency for n in doc.nodes] == [
        "non_idempotent", "idempotent", "idempotent",
    ]


def test_confirmation_param_vs_absence_signal() -> None:
    confirmed = _endpoint(
        "POST", "/api/payouts", params=[HttpParameterMetadata(name="confirm_token", location="json")]
    )
    bare = _endpoint("POST", "/api/transfers", params=[])
    unresolved = Node(
        name="u",
        confidence=1.0,
        component_type=ComponentType.API_ENDPOINT,
        metadata=NodeMetadata(
            method="POST", endpoint="/api/wire",
            http_request=HttpRequestMetadata(methods=["POST"], has_unresolved_inputs=True),
        ),
    )
    doc = _doc(confirmed, bare, unresolved)
    assert doc.nodes[0].metadata.mutation_semantics.confirmation_required is True
    # The *absence* of a confirmation step on a payment is the finding signal.
    assert doc.nodes[1].metadata.mutation_semantics.confirmation_required is False
    # …but only when every input was resolved; otherwise it stays unknown.
    assert doc.nodes[2].metadata.mutation_semantics.confirmation_required is None


def test_two_phase_confirm_route_marks_the_initiating_mutation() -> None:
    initiate = _endpoint("POST", "/api/transfers", params=[])
    confirm = _endpoint("POST", "/api/transfers/{id}/confirm", params=[])
    doc = _doc(initiate, confirm)
    sem = doc.nodes[0].metadata.mutation_semantics
    assert sem.confirmation_required is True
    assert "two_phase_route" in sem.evidence


def test_audit_logged_comes_only_from_handler_signals() -> None:
    audited = _endpoint("POST", "/api/items", signals={"audit_logged": True})
    unaudited = _endpoint("POST", "/api/other", signals={"audit_logged": False})
    unanalyzed = _endpoint("POST", "/api/third")
    doc = _doc(audited, unaudited, unanalyzed)
    assert [n.metadata.mutation_semantics.audit_logged for n in doc.nodes] == [True, False, None]


def test_verb_pairs_become_compensating_actions_in_both_directions() -> None:
    freeze = _endpoint("POST", "/api/accounts/{id}/freeze")
    unfreeze = _endpoint("POST", "/api/accounts/{id}/unfreeze")
    doc = _doc(freeze, unfreeze)
    a, b = (n.metadata.mutation_semantics for n in doc.nodes)
    assert (a.reversible, a.compensating_action.method, a.compensating_action.path) == (
        True, "POST", "/api/accounts/{id}/unfreeze",
    )
    assert b.compensating_action.path == "/api/accounts/{id}/freeze"


def test_create_pairs_with_delete_on_the_same_resource_family() -> None:
    create = _endpoint("POST", "/api/notes")
    delete = _endpoint("DELETE", "/api/notes/{note_id}")
    doc = _doc(create, delete)
    sem = doc.nodes[0].metadata.mutation_semantics
    assert sem.reversible is True
    assert (sem.compensating_action.method, sem.compensating_action.path) == ("DELETE", "/api/notes/{note_id}")
    # The irreversible half is not claimed reversible.
    assert doc.nodes[1].metadata.mutation_semantics.reversible is None


def test_payments_and_unrelated_families_never_get_a_compensating_action() -> None:
    doc = _doc(
        _endpoint("POST", "/api/transfers"),
        _endpoint("DELETE", "/api/transfers/{id}"),
        _endpoint("POST", "/api/notes"),
        _endpoint("DELETE", "/api/other/{id}"),
    )
    assert all(n.metadata.mutation_semantics.compensating_action is None for n in doc.nodes)


# ── A2: object-ID semantics ──────────────────────────────────────────────────


def test_integer_path_id_is_sequential_and_highly_enumerable() -> None:
    param = HttpParameterMetadata(name="account_id", location="path", type_hint="int", required=True)
    node = _endpoint("GET", "/api/accounts/{account_id}", params=[param], path_params=["account_id"])
    oid = _doc(node).nodes[0].metadata.object_id_semantics
    assert (oid.param_name, oid.id_format, oid.sequential, oid.enumerability) == (
        "account_id", "int", True, "high",
    )


def test_uuid_id_is_not_enumerable() -> None:
    node = _endpoint("GET", "/api/docs/{doc_uuid}", path_params=["doc_uuid"])
    oid = _doc(node).nodes[0].metadata.object_id_semantics
    assert (oid.id_format, oid.sequential, oid.enumerability) == ("uuid", False, "low")


def test_string_id_stays_unknown_rather_than_guessed() -> None:
    node = _endpoint("GET", "/api/accounts/{account_id}", path_params=["account_id"])
    oid = _doc(node).nodes[0].metadata.object_id_semantics
    assert (oid.id_format, oid.sequential, oid.enumerability) == (None, None, "unknown")


def test_unconventional_path_param_name_still_addresses_an_object() -> None:
    node = _endpoint("DELETE", "/api/payments/<pid>", path_params=["pid"])
    assert _doc(node).nodes[0].metadata.object_id_semantics.param_name == "pid"


def test_body_supplied_subject_id_is_the_object_id_when_no_path_param() -> None:
    node = _endpoint("POST", "/api/balance", body={"account_id": "str"})
    assert _doc(node).nodes[0].metadata.object_id_semantics.param_name == "account_id"


def test_sibling_read_paths_list_other_reads_of_the_family() -> None:
    doc = _doc(
        _endpoint("GET", "/api/accounts", params=[]),
        _endpoint("GET", "/api/accounts/{account_id}", path_params=["account_id"]),
        _endpoint("GET", "/api/accounts/{account_id}/transactions", path_params=["account_id"]),
        _endpoint("GET", "/api/users/{user_id}", path_params=["user_id"]),
    )
    oid = doc.nodes[1].metadata.object_id_semantics
    assert oid.sibling_read_paths == [
        "/api/accounts",
        "/api/accounts/{account_id}/transactions",
    ]


# ── A6: session cookie semantics ─────────────────────────────────────────────


def test_session_cookie_semantics_reflects_app_wide_handler_analysis() -> None:
    cookie = _endpoint("POST", "/login", signals={"sets_cookie": True})
    plain = _endpoint("GET", "/me", signals={"sets_cookie": False})
    unanalyzed = _endpoint("GET", "/other")
    doc = _doc(cookie, plain, unanalyzed)
    assert doc.nodes[0].metadata.session_cookie_semantics.set_cookie_expected is True
    # One handler sets a cookie, so the app is cookie-based for every analyzed endpoint.
    assert doc.nodes[1].metadata.session_cookie_semantics.set_cookie_expected is True
    assert doc.nodes[2].metadata.session_cookie_semantics is None


def test_stateless_app_reports_no_cookies_expected() -> None:
    doc = _doc(_endpoint("POST", "/a", signals={"sets_cookie": False}))
    assert doc.nodes[0].metadata.session_cookie_semantics.set_cookie_expected is False


# ── pass-level guarantees ────────────────────────────────────────────────────


def test_pass_is_idempotent_and_strips_internal_signals() -> None:
    doc = _doc(_endpoint("POST", "/api/items", body={"user_id": "str"}, signals={"audit_logged": True}))
    first = doc.nodes[0].metadata.model_dump(mode="json")
    apply_http_semantics(doc)
    assert doc.nodes[0].metadata.model_dump(mode="json") == first
    assert HANDLER_SIGNALS_KEY not in first["extras"]


def test_serialized_document_round_trips() -> None:
    doc = _doc(_endpoint("POST", "/api/transfers", body={"account_id": "str", "role": "str"}))
    restored = AiSbomDocument.model_validate(doc.model_dump(mode="json"))
    assert restored.nodes[0].metadata.mutation_semantics.mutation_kind == "payment"
    assert restored.nodes[0].metadata.http_request.parameters[0].identity_role == "subject_id"


def test_non_endpoint_nodes_are_ignored() -> None:
    tool = Node(name="t", component_type=ComponentType.TOOL, confidence=1.0, metadata=NodeMetadata())
    doc = _doc(tool)
    assert doc.nodes[0].metadata.mutation_semantics is None
