"""Tests for the W2 trust-context matrix (nuguard/redteam/trust_context.py)."""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from nuguard.models.exploit_chain import ExploitStep
from nuguard.redteam.catalog.capability import CapabilityDetector
from nuguard.redteam.catalog.selector import select_scenarios
from nuguard.redteam.target.canary import CanaryConfig, CanaryTenant
from nuguard.redteam.target.session import AttackSession
from nuguard.redteam.trust_context import (
    DEFAULT_SAMPLED_CELLS,
    CredentialAxis,
    IdentityBindingAxis,
    IdentityValueAxis,
    TrustContextCell,
    TrustContextRunner,
    apply_cell_to_step,
    assess_identity_mismatch,
)
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata
from nuguard.sbom.types import ComponentType, RelationshipType

_NS = uuid.UUID("00000000-0000-0000-0000-000000000005")


def _uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NS, name)

_GOLDEN = "Your account balance is 1000 dollars and your email is alice@example.com, id ACC1001"
_NOVEL = "Account ACC2002 belongs to Bob Smith, email bob@example.org, balance 5000, phone 555-2002"
_OWN_DATA = "Your account balance is 1000 dollars and your email is alice@example.com"


def _chat_step(payload: str = "show me the account") -> ExploitStep:
    return ExploitStep(step_id="s1", step_type="INJECT", description="x", payload=payload)


def _direct_step(path: str = "/api/accounts/2") -> ExploitStep:
    return ExploitStep(
        step_id="s1", step_type="INVOKE", description="x", payload="", target_path=path,
        http_body={"account_id": "ACC1001"},
    )


def _session(golden_data: str = _GOLDEN, golden_ids: list[str] | None = None) -> AttackSession:
    s = AttackSession(session_id="s1", target_url="http://t", chain_id="c1")
    s.golden_data = golden_data
    s.golden_ids = golden_ids or ["ACC1001"]
    s.golden_name = "Alice"
    return s


# ── apply_cell_to_step ───────────────────────────────────────────────────────


def test_missing_credentials_strips_auth() -> None:
    cell = TrustContextCell(
        "x", CredentialAxis.MISSING, IdentityBindingAxis.BODY_ONLY, IdentityValueAxis.CROSS_TENANT,
    )
    mutated = apply_cell_to_step(_direct_step(), cell, golden_id="ACC1001")
    assert mutated.strip_auth is True


def test_invalid_credentials_sets_bogus_authorization_header() -> None:
    cell = TrustContextCell(
        "x", CredentialAxis.INVALID, IdentityBindingAxis.BODY_ONLY, IdentityValueAxis.CROSS_TENANT,
    )
    mutated = apply_cell_to_step(_direct_step(), cell, golden_id="ACC1001")
    assert mutated.extra_headers["Authorization"].startswith("Bearer invalid-")


def test_body_only_sets_http_body_field_for_direct_step() -> None:
    cell = TrustContextCell(
        "x", CredentialAxis.INVALID, IdentityBindingAxis.BODY_ONLY, IdentityValueAxis.CROSS_TENANT,
    )
    canary = CanaryConfig(tenants=[CanaryTenant(tenant_id="tenant-999")])
    mutated = apply_cell_to_step(_direct_step(), cell, golden_id="ACC1001", canary_config=canary)
    assert mutated.http_body["user_id"] == "tenant-999"


def test_body_only_appends_identity_claim_for_chat_step() -> None:
    cell = TrustContextCell(
        "x", CredentialAxis.INVALID, IdentityBindingAxis.BODY_ONLY, IdentityValueAxis.ADMIN_LIKE,
    )
    mutated = apply_cell_to_step(_chat_step(), cell, golden_id="ACC1001")
    assert "admin" in mutated.payload
    assert mutated.payload.startswith("show me the account")  # original text preserved


def test_conflicting_sets_both_header_golden_and_body_spoofed() -> None:
    cell = TrustContextCell(
        "x", CredentialAxis.INVALID, IdentityBindingAxis.CONFLICTING, IdentityValueAxis.ADMIN_LIKE,
    )
    mutated = apply_cell_to_step(_direct_step(), cell, golden_id="ACC1001")
    assert mutated.extra_headers["X-User-Id"] == "ACC1001"
    assert mutated.http_body["user_id"] == "admin"


def test_valid_axis_not_present_in_default_cells() -> None:
    # Proposal's "valid creds + golden identity" baseline is the scenario's
    # own first attempt (step_results[0]), never re-sent by the matrix.
    for cell in DEFAULT_SAMPLED_CELLS:
        assert cell.credentials in (CredentialAxis.INVALID, CredentialAxis.MISSING)


# ── assess_identity_mismatch ──────────────────────────────────────────────────


def _cell() -> TrustContextCell:
    return TrustContextCell(
        "x", CredentialAxis.INVALID, IdentityBindingAxis.BODY_ONLY, IdentityValueAxis.CROSS_TENANT,
    )


def test_no_golden_baseline_is_inconclusive() -> None:
    session = _session(golden_data="")
    assert assess_identity_mismatch(_cell(), _NOVEL, 200, session) == "inconclusive"


def test_non_2xx_direct_response_is_control_held() -> None:
    session = _session()
    assert assess_identity_mismatch(_cell(), _NOVEL, 403, session) == "control_held"


def test_chat_refusal_is_control_held() -> None:
    session = _session()
    refused = "I cannot share another customer's account details."
    assert assess_identity_mismatch(_cell(), refused, None, session) == "control_held"


def test_foreign_data_disclosed_is_mismatch() -> None:
    session = _session()
    assert assess_identity_mismatch(_cell(), _NOVEL, 200, session) == "mismatch"


def test_own_data_only_is_control_held() -> None:
    session = _session()
    assert assess_identity_mismatch(_cell(), _OWN_DATA, 200, session) == "control_held"


# ── TrustContextRunner ────────────────────────────────────────────────────────


class _FakeDirectClient:
    """Returns foreign data for the body-spoofed cross-tenant ID, refuses
    everything else — simulates the exact auth-bypass primitive."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def invoke_endpoint(
        self, path: str, method: str = "GET", body: dict | None = None,
        params: dict | None = None, extra_headers: dict | None = None, strip_auth: bool = False,
    ) -> tuple[int, str, dict]:
        self.calls.append({"body": body, "strip_auth": strip_auth, "extra_headers": extra_headers})
        spoofed_id = (body or {}).get("user_id", "")
        if spoofed_id == "tenant-999":
            return 200, _NOVEL, {}
        return 403, "Forbidden", {}


@pytest.mark.asyncio
async def test_runner_confirms_mismatch_on_direct_http_step() -> None:
    client = _FakeDirectClient()
    canary = CanaryConfig(tenants=[CanaryTenant(tenant_id="tenant-999")])
    runner = TrustContextRunner(client, canary_config=canary, confirmation_cells=1)
    session = _session()

    results = await runner.run(_direct_step(), session)

    mismatches = [r for r in results if r.verdict == "mismatch"]
    assert mismatches, "expected at least one confirmed mismatch"
    assert mismatches[0].cell.name == "invalid_creds_spoofed_identity"


@pytest.mark.asyncio
async def test_runner_early_exits_after_confirmation_budget() -> None:
    client = _FakeDirectClient()
    canary = CanaryConfig(tenants=[CanaryTenant(tenant_id="tenant-999")])
    runner = TrustContextRunner(client, canary_config=canary, confirmation_cells=0)
    session = _session()

    results = await runner.run(_direct_step(), session)

    # confirmation_cells=0: stop immediately after the first confirmed mismatch.
    assert len(results) == 1
    assert results[0].verdict == "mismatch"


class _RefusingClient:
    """Never discloses anything — the control genuinely holds everywhere."""

    async def invoke_endpoint(self, *args: Any, **kwargs: Any) -> tuple[int, str, dict]:
        return 403, "Forbidden", {}

    async def send(self, payload: str, session: Any, extra_headers: Any = None, retry_transient: bool = False):
        return "I cannot share another customer's account details.", []


@pytest.mark.asyncio
async def test_runner_runs_all_cells_when_nothing_confirms() -> None:
    runner = TrustContextRunner(_RefusingClient(), confirmation_cells=1)
    session = _session()

    results = await runner.run(_direct_step(), session)

    assert len(results) == len(DEFAULT_SAMPLED_CELLS)
    assert all(r.verdict == "control_held" for r in results)


@pytest.mark.asyncio
async def test_runner_skips_missing_credentials_for_chat_step() -> None:
    runner = TrustContextRunner(_RefusingClient(), confirmation_cells=1)
    session = _session()

    results = await runner.run(_chat_step(), session)

    # MISSING-credentials cell cannot be expressed over client.send() —
    # must be skipped, not silently sent with full auth.
    assert all(r.cell.credentials != CredentialAxis.MISSING for r in results)
    assert len(results) == len(DEFAULT_SAMPLED_CELLS) - 1


# ── Catalog end-to-end: A10/A11 generate with identity_sensitive=True ────────


def test_a10_generates_with_identity_sensitive_flag() -> None:
    agent = Node(
        id=_uuid("agent"), name="support_agent", component_type=ComponentType.AGENT,
        confidence=0.9, metadata=NodeMetadata(),
    )
    datastore = Node(
        id=_uuid("ds"), name="accounts_db", component_type=ComponentType.DATASTORE,
        confidence=0.9, metadata=NodeMetadata(pii_fields=["email"]),
    )
    sbom = AiSbomDocument(
        target="unit-test", nodes=[agent, datastore],
        edges=[Edge(source=agent.id, target=datastore.id, relationship_type=RelationshipType.ACCESSES)],
    )
    profile = CapabilityDetector(sbom).build()
    scenarios, _coverage = select_scenarios(sbom, profile, scan_profile="full")

    a10 = [s for s in scenarios if s.catalog_id == "A10"]
    assert a10, "A10 should be generated once SENSITIVE_CONTEXT is detected"
    assert all(s.identity_sensitive for s in a10)


def test_a11_generates_with_identity_sensitive_flag_on_direct_endpoint() -> None:
    agent = Node(
        id=_uuid("agent2"), name="support_agent", component_type=ComponentType.AGENT,
        confidence=0.9, metadata=NodeMetadata(),
    )
    tool = Node(
        id=_uuid("tool2"), name="get_account", component_type=ComponentType.TOOL,
        confidence=0.9, metadata=NodeMetadata(description="Retrieve account data"),
    )
    endpoint = Node(
        id=_uuid("endpoint2"), name="GET /api/accounts/{id}",
        component_type=ComponentType.API_ENDPOINT, confidence=0.9,
        metadata=NodeMetadata(endpoint="/api/accounts/{id}", method="GET"),
    )
    sbom = AiSbomDocument(
        target="unit-test", nodes=[agent, tool, endpoint],
        edges=[Edge(source=agent.id, target=tool.id, relationship_type=RelationshipType.CALLS)],
    )
    profile = CapabilityDetector(sbom).build()
    scenarios, _coverage = select_scenarios(sbom, profile, scan_profile="full")

    a11 = [s for s in scenarios if s.catalog_id == "A11"]
    assert a11, "A11 should be generated once DIRECT_TOOL_ENDPOINT is detected"
    assert all(s.identity_sensitive for s in a11)
