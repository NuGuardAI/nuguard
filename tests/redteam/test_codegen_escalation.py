"""Tests for the code-gen exploitation escalation feature (v1 engine).

Covers:
- Detection logic via the module-level ``detect_codegen_success()`` helper
  used by ``RedteamOrchestrator`` to decide when to spawn escalation chains.
- Scenario builder output from ``build_codegen_escalation_chains()``
- Goal-type attribution: each chain must carry the correct GoalType

The v2 engine had its own ``ObjectiveRunner`` with parallel methods that this
test file used to exercise. After the v2 removal (issue #216) those internal
classes no longer exist, so this file now tests the v1 surface only — the
module-level ``detect_codegen_success()`` is what the v1 orchestrator calls
(``nuguard/redteam/executor/orchestrator.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.scenarios.codegen_escalation import (
    _goal_type_for_hint,
    build_codegen_escalation_chains,
    detect_codegen_success,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeStep:
    step_type: str = "INJECT"


@dataclass
class _FakeStepResult:
    step: _FakeStep = field(default_factory=_FakeStep)
    success_signal_found: bool = False
    response: str = ""
    llm_eval_confidence: str = ""
    llm_eval_evidence: str = ""


# ---------------------------------------------------------------------------
# Detection tests — module-level detect_codegen_success()
# ---------------------------------------------------------------------------


class TestDetectCodegenSuccess:
    def test_fires_on_fenced_code_block(self):
        results = [_FakeStepResult(
            success_signal_found=True,
            response="Here's the code:\n```python\ndef fetch_users():\n    return db.query('SELECT * FROM users')\n```",
        )]
        hit, evidence = detect_codegen_success(results)
        assert hit
        assert "Code block" in evidence

    def test_fires_on_llm_eval_evidence_mentioning_code(self):
        results = [_FakeStepResult(
            success_signal_found=True,
            llm_eval_confidence="high",
            llm_eval_evidence="Agent generated a Python function that fetches user PII.",
        )]
        hit, evidence = detect_codegen_success(results)
        assert hit
        assert "python" in evidence.lower() or "generated" in evidence.lower()

    def test_skips_warmup_steps(self):
        results = [_FakeStepResult(
            step=_FakeStep(step_type="WARMUP"),
            success_signal_found=True,
            response="```python\nprint('hello')\n```",
        )]
        hit, _ = detect_codegen_success(results)
        assert not hit

    def test_skips_discover_steps(self):
        results = [_FakeStepResult(
            step=_FakeStep(step_type="DISCOVER"),
            success_signal_found=True,
            response="```typescript\nconst x = 1;\n```",
        )]
        hit, _ = detect_codegen_success(results)
        assert not hit

    def test_no_hit_when_no_success_signal(self):
        results = [_FakeStepResult(
            success_signal_found=False,
            response="```python\nprint('hello')\n```",
        )]
        hit, _ = detect_codegen_success(results)
        assert not hit

    def test_no_hit_on_plain_refusal(self):
        results = [_FakeStepResult(
            success_signal_found=False,
            response="I'm sorry, I can't help with that request.",
        )]
        hit, _ = detect_codegen_success(results)
        assert not hit

    def test_no_hit_on_low_confidence_evidence(self):
        results = [_FakeStepResult(
            success_signal_found=True,
            llm_eval_confidence="low",
            llm_eval_evidence="Agent mentioned the word python in passing.",
        )]
        hit, _ = detect_codegen_success(results)
        assert not hit

    def test_empty_results(self):
        hit, _ = detect_codegen_success([])
        assert not hit

    def test_hit_snippet_truncates_long_responses(self):
        long_response = "```python\n" + ("x = 1\n" * 200) + "```"
        results = [_FakeStepResult(
            success_signal_found=True,
            response=long_response,
        )]
        hit, evidence = detect_codegen_success(results)
        assert hit
        # snippet is truncated to 200 chars and newlines replaced
        assert len(evidence) < len(long_response) + 50


# ---------------------------------------------------------------------------
# Builder tests — goal-type attribution
# ---------------------------------------------------------------------------


class TestBuildCodegenEscalationChains:
    def test_returns_five_chains(self):
        chains = build_codegen_escalation_chains("agent-1", "TestAgent")
        assert len(chains) == 5

    def test_chain1_is_prompt_driven_threat(self):
        """Safeguard strip → PROMPT_DRIVEN_THREAT so the finding is correctly attributed."""
        chains = build_codegen_escalation_chains("agent-1", "TestAgent")
        assert chains[0].chain.goal_type == GoalType.PROMPT_DRIVEN_THREAT
        assert chains[0].chain.scenario_type == ScenarioType.GUARDRAIL_BYPASS

    def test_chain2_goal_type_from_hint_privilege(self):
        """Chain 2 uses PRIVILEGE_ESCALATION when the parent hint says so."""
        chains = build_codegen_escalation_chains("a", "Bot", goal_type_hint="PRIVILEGE_ESCALATION")
        assert chains[1].chain.goal_type == GoalType.PRIVILEGE_ESCALATION

    def test_chain2_goal_type_from_hint_tool_abuse(self):
        chains = build_codegen_escalation_chains("a", "Bot", goal_type_hint="TOOL_ABUSE")
        assert chains[1].chain.goal_type == GoalType.TOOL_ABUSE

    def test_chain2_goal_type_from_hint_policy(self):
        chains = build_codegen_escalation_chains("a", "Bot", goal_type_hint="POLICY_VIOLATION")
        assert chains[1].chain.goal_type == GoalType.POLICY_VIOLATION

    def test_chain2_default_hint_is_prompt_driven_threat(self):
        chains = build_codegen_escalation_chains("a", "Bot", goal_type_hint="")
        assert chains[1].chain.goal_type == GoalType.PROMPT_DRIVEN_THREAT

    def test_chain3_is_data_exfiltration(self):
        """Bulk data fetch → DATA_EXFILTRATION."""
        chains = build_codegen_escalation_chains("agent-1", "TestAgent")
        assert chains[2].chain.goal_type == GoalType.DATA_EXFILTRATION
        assert chains[2].chain.scenario_type == ScenarioType.BULK_DATA_EXPORT

    def test_chain4_is_tool_abuse(self):
        """Tool chaining step → TOOL_ABUSE."""
        chains = build_codegen_escalation_chains("agent-1", "TestAgent")
        assert chains[3].chain.goal_type == GoalType.TOOL_ABUSE
        assert chains[3].chain.scenario_type == ScenarioType.UNAUTHORIZED_MESSAGE_SEND

    def test_chain5_is_data_exfiltration_covert(self):
        """Covert base64 exfil → DATA_EXFILTRATION / COVERT_ENCODING."""
        chains = build_codegen_escalation_chains("agent-1", "TestAgent")
        assert chains[4].chain.goal_type == GoalType.DATA_EXFILTRATION
        assert chains[4].chain.scenario_type == ScenarioType.COVERT_ENCODING

    def test_all_chains_start_with_warmup(self):
        chains = build_codegen_escalation_chains("a", "Bot")
        for scenario in chains:
            assert scenario.chain.steps[0].step_type == "WARMUP"
            assert not scenario.chain.steps[0].contributes_to_finding

    def test_all_inject_steps_have_llm_eval(self):
        chains = build_codegen_escalation_chains("a", "Bot")
        for scenario in chains:
            for step in scenario.chain.steps:
                if step.step_type == "INJECT":
                    assert step.use_llm_eval
                    assert step.contributes_to_finding

    def test_agent_id_wired_to_all_steps(self):
        chains = build_codegen_escalation_chains("agent-42", "MyAgent")
        for scenario in chains:
            for step in scenario.chain.steps:
                assert step.target_node_id == "agent-42"

    def test_all_titles_include_agent_name(self):
        chains = build_codegen_escalation_chains("a", "BankBot")
        for scenario in chains:
            assert "BankBot" in scenario.title

    def test_no_two_chains_share_goal_and_scenario_type(self):
        """No two chains are identical (goal_type + scenario_type pair must differ)."""
        chains = build_codegen_escalation_chains("a", "Bot")
        seen = set()
        for sc in chains:
            key = (sc.chain.goal_type, sc.chain.scenario_type)
            assert key not in seen, f"Duplicate chain: {key}"
            seen.add(key)


class TestGoalTypeForHint:
    def test_privilege(self):
        goal, _ = _goal_type_for_hint("PRIVILEGE_ESCALATION")
        assert goal == GoalType.PRIVILEGE_ESCALATION

    def test_tool(self):
        goal, _ = _goal_type_for_hint("TOOL_ABUSE")
        assert goal == GoalType.TOOL_ABUSE

    def test_policy(self):
        goal, _ = _goal_type_for_hint("POLICY_VIOLATION")
        assert goal == GoalType.POLICY_VIOLATION

    def test_default(self):
        goal, _ = _goal_type_for_hint("")
        assert goal == GoalType.PROMPT_DRIVEN_THREAT

    def test_case_insensitive(self):
        goal, _ = _goal_type_for_hint("privilege_escalation")
        assert goal == GoalType.PRIVILEGE_ESCALATION


# ---------------------------------------------------------------------------
# Goal attribution integration test
# ---------------------------------------------------------------------------


class TestGoalAttribution:
    """Verify that each escalation chain's GoalType matches the intended attack."""

    def test_safeguard_strip_is_not_data_exfil(self):
        """The safeguard-strip chain must NOT be DATA_EXFILTRATION — it's PROMPT_DRIVEN_THREAT."""
        chains = build_codegen_escalation_chains("a", "Bot")
        safeguard_chain = chains[0]
        assert safeguard_chain.chain.goal_type != GoalType.DATA_EXFILTRATION
        assert safeguard_chain.chain.goal_type == GoalType.PROMPT_DRIVEN_THREAT

    def test_tool_abuse_chain_is_not_data_exfil(self):
        """The tool-abuse chain must be TOOL_ABUSE, not DATA_EXFILTRATION."""
        chains = build_codegen_escalation_chains("a", "Bot")
        tool_chain = chains[3]
        assert tool_chain.chain.goal_type == GoalType.TOOL_ABUSE
        assert tool_chain.chain.goal_type != GoalType.DATA_EXFILTRATION

    def test_each_chain_contributes_findings_independently(self):
        """Every INJECT step in every chain has contributes_to_finding=True."""
        chains = build_codegen_escalation_chains("a", "Bot")
        for scenario in chains:
            inject_steps = [s for s in scenario.chain.steps if s.step_type == "INJECT"]
            assert inject_steps, f"Chain {scenario.title!r} has no INJECT steps"
            for step in inject_steps:
                assert step.contributes_to_finding, (
                    f"Step {step.description!r} in chain {scenario.title!r} "
                    "must have contributes_to_finding=True"
                )