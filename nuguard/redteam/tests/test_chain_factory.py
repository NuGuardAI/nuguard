"""Unit tests for nuguard.redteam.scenarios._chain_factory's shared primitives
introduced for issue #561: is_credentialed_mutation_step and
apply_secondary_credential.
"""
from __future__ import annotations

from nuguard.models.exploit_chain import ExploitChain, ExploitStep, GoalType, ScenarioType
from nuguard.redteam.scenarios._chain_factory import (
    apply_secondary_credential,
    is_credentialed_mutation_step,
)
from nuguard.redteam.scenarios.scenario_types import AttackScenario


def _step(**overrides) -> ExploitStep:
    defaults = dict(
        step_id="c1_s1", step_type="INVOKE", description="", payload="",
    )
    return ExploitStep(**{**defaults, **overrides})


def _scenario(steps: list[ExploitStep]) -> AttackScenario:
    chain = ExploitChain(
        chain_id="c1", goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.MASS_ASSIGNMENT, steps=steps,
    )
    return AttackScenario(
        scenario_id="s1", goal_type=GoalType.API_ATTACK,
        scenario_type=ScenarioType.MASS_ASSIGNMENT,
        title="Mass Assignment — TestEndpoint", description="test", chain=chain,
    )


# ---------------------------------------------------------------------------
# is_credentialed_mutation_step
# ---------------------------------------------------------------------------

def test_no_target_path_is_not_credentialed_mutation():
    """A chat-mediated step (no target_path) is never a direct-HTTP mutation,
    regardless of its (unused-in-that-case) http_method default."""
    step = _step(target_path=None, http_method="POST", strip_auth=False)
    assert not is_credentialed_mutation_step(step)


def test_get_method_is_not_credentialed_mutation():
    step = _step(target_path="/api/users/1", http_method="GET", strip_auth=False)
    assert not is_credentialed_mutation_step(step)


def test_strip_auth_write_is_not_credentialed_mutation():
    step = _step(target_path="/api/users", http_method="POST", strip_auth=True)
    assert not is_credentialed_mutation_step(step)


def test_post_credentialed_write_is_mutation():
    step = _step(target_path="/api/users", http_method="POST", strip_auth=False)
    assert is_credentialed_mutation_step(step)


def test_put_patch_delete_credentialed_writes_are_mutations():
    for method in ("PUT", "PATCH", "DELETE"):
        step = _step(target_path="/api/users/1", http_method=method, strip_auth=False)
        assert is_credentialed_mutation_step(step), f"{method} should be a write method"


def test_lowercase_method_is_still_recognized():
    """http_method is normalized with .upper() before the WRITE_METHODS check."""
    step = _step(target_path="/api/users", http_method="post", strip_auth=False)
    assert is_credentialed_mutation_step(step)


# ---------------------------------------------------------------------------
# apply_secondary_credential
# ---------------------------------------------------------------------------

def test_apply_secondary_credential_none_scenario_returns_none():
    assert apply_secondary_credential(None, {"Authorization": "Bearer x"}) is None


def test_apply_secondary_credential_guided_conversation_scenario_unchanged():
    """A scenario with no static chain (guided_conversation instead) can't be
    swapped — returned unchanged rather than raising on scenario.chain.steps."""
    scenario = AttackScenario(
        scenario_id="s1", goal_type=GoalType.PROMPT_DRIVEN_THREAT,
        scenario_type=ScenarioType.MANY_SHOT_JAILBREAK, title="t", description="d", chain=None,
    )
    result = apply_secondary_credential(scenario, {"Authorization": "Bearer x"})
    assert result is scenario
    assert result.chain is None


def test_apply_secondary_credential_empty_headers_dict_leaves_scenario_unchanged():
    """An empty (falsy) headers dict takes the same no-op path as None —
    documents current behavior at the `not secondary_auth_headers` branch."""
    step = _step(target_path="/api/users", http_method="POST")
    scenario = _scenario([step])
    result = apply_secondary_credential(scenario, {})
    assert result.chain.steps[0].strip_auth is False
    assert result.chain.steps[0].extra_headers == {}


def test_apply_secondary_credential_swaps_only_credentialed_steps_in_mixed_chain():
    """A chain with a credentialed write, a GET, and an already-strip_auth step
    — only the credentialed write should be touched."""
    write_step = _step(step_id="c1_s1", target_path="/api/users", http_method="POST")
    get_step = _step(step_id="c1_s2", target_path="/api/users/1", http_method="GET")
    stripped_step = _step(
        step_id="c1_s3", target_path="/api/users", http_method="POST", strip_auth=True,
        extra_headers={"Authorization": "Bearer forged"},
    )
    scenario = _scenario([write_step, get_step, stripped_step])

    result = apply_secondary_credential(scenario, {"Authorization": "Bearer canary-tok"})

    swapped, unchanged_get, unchanged_stripped = result.chain.steps
    assert swapped.strip_auth is True
    assert swapped.extra_headers == {"Authorization": "Bearer canary-tok"}
    assert unchanged_get.strip_auth is False
    assert unchanged_get.extra_headers == {}
    assert unchanged_stripped.strip_auth is True
    assert unchanged_stripped.extra_headers == {"Authorization": "Bearer forged"}


def test_apply_secondary_credential_preserves_steps_own_extra_headers_on_collision():
    """A credentialed step that already carries its own extra_headers keeps
    that value on key collision — the step's own header wins over the canary
    one merged in underneath it (dict unpacking order in apply_secondary_credential)."""
    step = _step(
        target_path="/api/users", http_method="POST",
        extra_headers={"Authorization": "Bearer step-specific", "X-Trace": "abc"},
    )
    scenario = _scenario([step])

    result = apply_secondary_credential(scenario, {"Authorization": "Bearer canary-tok"})

    swapped = result.chain.steps[0]
    assert swapped.strip_auth is True
    assert swapped.extra_headers == {"Authorization": "Bearer step-specific", "X-Trace": "abc"}


def test_apply_secondary_credential_empty_steps_list_is_a_noop():
    scenario = _scenario([])
    result = apply_secondary_credential(scenario, {"Authorization": "Bearer x"})
    assert result.chain.steps == []
