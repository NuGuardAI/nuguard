"""Tests for _idor_chain_status_with_auth_caveat.

A direct-HTTP IDOR probe (nuguard/redteam/scenarios/api_attacks.py::build_idor)
sends its "different object ID" GET with no identity of its own — it just
inherits whatever ambient headers the target client already carries. When
target.auth is entirely unconfigured, that means the probe runs with no auth
at all, so a miss just proves the endpoint requires auth (a weaker, different
claim than "object-level authorization is enforced"). See the OWASP Juice
Shop /rest/basket/:id case this was traced from: the probe hit a bare 401
("No Authorization header was found") and was recorded as a clean miss,
indistinguishable from a real ownership check having been exercised.
"""

from __future__ import annotations

from types import SimpleNamespace

from nuguard.models.exploit_chain import ScenarioType
from nuguard.redteam.executor.orchestrator import _idor_chain_status_with_auth_caveat


def _scenario(scenario_type: ScenarioType, target_paths: list[str | None]) -> SimpleNamespace:
    steps = [SimpleNamespace(target_path=p) for p in target_paths]
    return SimpleNamespace(scenario_type=scenario_type, chain=SimpleNamespace(steps=steps))


def test_idor_miss_with_no_auth_becomes_inconclusive():
    scenario = _scenario(ScenarioType.IDOR, ["/rest/basket/99999"])
    status = _idor_chain_status_with_auth_caveat(
        scenario, had_finding=False, no_target_auth=True, chain_status="completed"
    )
    assert status == "inconclusive:no_auth_configured"


def test_idor_miss_with_auth_configured_is_left_alone():
    scenario = _scenario(ScenarioType.IDOR, ["/rest/basket/99999"])
    status = _idor_chain_status_with_auth_caveat(
        scenario, had_finding=False, no_target_auth=False, chain_status="completed"
    )
    assert status == "completed"


def test_idor_hit_is_never_downgraded_even_with_no_auth():
    scenario = _scenario(ScenarioType.IDOR, ["/rest/basket/99999"])
    status = _idor_chain_status_with_auth_caveat(
        scenario, had_finding=True, no_target_auth=True, chain_status="completed"
    )
    assert status == "completed"


def test_non_idor_scenario_type_is_unaffected():
    scenario = _scenario(ScenarioType.AUTH_BYPASS, ["/api/users"])
    status = _idor_chain_status_with_auth_caveat(
        scenario, had_finding=False, no_target_auth=True, chain_status="completed"
    )
    assert status == "completed"


def test_chat_routed_idor_scenario_is_unaffected():
    # A step with no target_path routes through chat, not invoke_endpoint —
    # not the direct-HTTP IDOR probe this caveat targets.
    scenario = _scenario(ScenarioType.IDOR, [None])
    status = _idor_chain_status_with_auth_caveat(
        scenario, had_finding=False, no_target_auth=True, chain_status="completed"
    )
    assert status == "completed"


def test_preexisting_abort_reason_is_preserved_when_not_overridden():
    scenario = _scenario(ScenarioType.IDOR, ["/rest/basket/99999"])
    status = _idor_chain_status_with_auth_caveat(
        scenario, had_finding=False, no_target_auth=False, chain_status="aborted:timeout"
    )
    assert status == "aborted:timeout"
