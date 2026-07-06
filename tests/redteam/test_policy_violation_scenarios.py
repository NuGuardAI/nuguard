"""Tests for new policy violation scenario builders.

Verifies that all Cognitive Policy dimensions produce at least one scenario:
- allowed_topics boundary
- hitl_tool_conditions
- rate_limits burst
- raw_sections probe
"""
from __future__ import annotations

from nuguard.models.policy import CognitivePolicy, HitlToolCondition
from nuguard.redteam.scenarios.faux_reasoning import build_policy_patching
from nuguard.redteam.scenarios.policy_violations import (
    build_allowed_topic_boundary,
    build_rate_limit_burst,
    build_raw_section_probe,
    build_restricted_topic,
    build_restricted_topic_variants,
    concrete_topic_probe,
    missing_universal_topics,
)
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.scenarios.session_metadata import build_policy_version_claim

_AGENT_ID = "agent-001"


class TestAllowedTopicBoundary:
    def test_produces_scenario(self) -> None:
        scenario = build_allowed_topic_boundary(
            _AGENT_ID, "cooking recipes", ["weapons", "drugs"]
        )
        assert isinstance(scenario, AttackScenario)
        assert scenario.chain is not None
        assert len(scenario.chain.steps) >= 2
        assert "cooking recipes" in scenario.title.lower() or "cooking" in scenario.title.lower()

    def test_uses_restricted_topic_in_pivot(self) -> None:
        scenario = build_allowed_topic_boundary(
            _AGENT_ID, "cooking recipes", ["weapons"]
        )
        # The second step should reference the restricted topic as a pivot
        second_payload = scenario.chain.steps[1].payload
        assert "weapons" in second_payload or "cooking" in second_payload

    def test_no_restricted_topics_defaults(self) -> None:
        scenario = build_allowed_topic_boundary(_AGENT_ID, "cooking", [])
        assert isinstance(scenario, AttackScenario)
        assert len(scenario.chain.steps) >= 2

    def test_target_node_ids(self) -> None:
        scenario = build_allowed_topic_boundary(_AGENT_ID, "travel", [])
        assert _AGENT_ID in scenario.target_node_ids


class TestRateLimitBurst:
    def test_produces_scenario(self) -> None:
        scenario = build_rate_limit_burst(_AGENT_ID, "api_calls", 10)
        assert isinstance(scenario, AttackScenario)
        assert scenario.chain is not None

    def test_burst_capped_at_10(self) -> None:
        scenario = build_rate_limit_burst(_AGENT_ID, "api_calls", 100)
        assert len(scenario.chain.steps) == 10

    def test_burst_is_limit_plus_one(self) -> None:
        scenario = build_rate_limit_burst(_AGENT_ID, "queries", 3)
        assert len(scenario.chain.steps) == 4  # min(3+1, 10)

    def test_last_step_uses_llm_eval(self) -> None:
        scenario = build_rate_limit_burst(_AGENT_ID, "requests", 5)
        last = scenario.chain.steps[-1]
        assert last.use_llm_eval is True

    def test_title_includes_key(self) -> None:
        scenario = build_rate_limit_burst(_AGENT_ID, "api_calls", 10)
        assert "api_calls" in scenario.title


class TestRawSectionProbe:
    def test_produces_scenario(self) -> None:
        scenario = build_raw_section_probe(
            _AGENT_ID, "Data Retention", ["All data must be deleted after 30 days"]
        )
        assert isinstance(scenario, AttackScenario)
        assert len(scenario.chain.steps) >= 2

    def test_empty_bullets(self) -> None:
        scenario = build_raw_section_probe(_AGENT_ID, "Jurisdiction", [])
        assert isinstance(scenario, AttackScenario)

    def test_title_includes_section_name(self) -> None:
        scenario = build_raw_section_probe(_AGENT_ID, "Audit Logging", ["all ops logged"])
        assert "Audit Logging" in scenario.title


_PII_TOPIC = (
    "Sensitive personal data disclosure beyond what is needed for reservation "
    "lookup or account support."
)
_OFF_TOPIC = (
    "Any advice or assistance unrelated to airline travel support or the app's "
    "configured FAQ scope."
)
_DOMAIN_TOPIC = "financial advice, investment recommendations, or general banking"
_GUARDRAIL_TOPIC = "Responses that contradict or bypass safety guardrails or prompt-injection guardrails."


class TestConcreteTopicProbe:
    """concrete_topic_probe() must impersonate a real request, not announce the category."""

    def test_pii_topic_uses_golden_tokens_and_concrete_fields(self) -> None:
        probe = concrete_topic_probe(_PII_TOPIC)
        assert "{golden_name}" in probe
        assert "{golden_id}" in probe
        assert any(field in probe.lower() for field in ("phone", "date of birth", "address"))
        assert "sensitive personal data disclosure" not in probe.lower()

    def test_off_topic_topic_produces_concrete_off_topic_question(self) -> None:
        probe = concrete_topic_probe(_OFF_TOPIC)
        assert "unrelated" not in probe.lower()
        assert "faq scope" not in probe.lower()
        assert any(w in probe.lower() for w in ("super bowl", "weather", "election"))

    def test_off_topic_is_deterministic(self) -> None:
        assert concrete_topic_probe(_OFF_TOPIC) == concrete_topic_probe(_OFF_TOPIC)

    def test_domain_topic_never_uses_bare_help_me_with_template(self) -> None:
        probe = concrete_topic_probe(_DOMAIN_TOPIC)
        assert "help me with:" not in probe.lower()
        assert "financial advice" in probe.lower()

    def test_escalate_variant_differs_from_initial(self) -> None:
        assert concrete_topic_probe(_PII_TOPIC) != concrete_topic_probe(_PII_TOPIC, escalate=True)
        assert concrete_topic_probe(_DOMAIN_TOPIC) != concrete_topic_probe(
            _DOMAIN_TOPIC, escalate=True
        )

    def test_guardrail_topic_does_not_echo_raw_clause(self) -> None:
        probe = concrete_topic_probe(_GUARDRAIL_TOPIC)
        assert "contradict" not in probe.lower()
        assert "bypass safety guardrails" not in probe.lower()
        escalate = concrete_topic_probe(_GUARDRAIL_TOPIC, escalate=True)
        assert probe != escalate
        assert "ignore" in escalate.lower() or "unrestricted" in escalate.lower()


class TestRestrictedTopicUsesConcreteProbe:
    def test_no_bare_help_me_with_splice(self) -> None:
        scenario = build_restricted_topic(_AGENT_ID, _PII_TOPIC)
        for step in scenario.chain.steps:
            assert f"help me with: {_PII_TOPIC}" not in step.payload
            assert f"information about: {_PII_TOPIC}" not in step.payload

    def test_pii_topic_step_carries_golden_tokens(self) -> None:
        scenario = build_restricted_topic(_AGENT_ID, _PII_TOPIC)
        assert any("{golden_id}" in step.payload for step in scenario.chain.steps)


class TestPolicyPatchingUsesConcreteProbe:
    def test_verify_topic_step_no_bare_splice(self) -> None:
        scenario = build_policy_patching(
            _AGENT_ID, "Cancellation Agent", extra_restricted_topics=[_OFF_TOPIC]
        )
        payloads = [s.payload for s in scenario.chain.steps]
        assert not any(f"help me with: {_OFF_TOPIC}" in p for p in payloads)
        assert any(w in " ".join(payloads).lower() for w in ("super bowl", "weather", "election"))


class TestPolicyVersionClaimUsesConcreteProbe:
    def test_no_bare_help_me_with_splice(self) -> None:
        scenario = build_policy_version_claim(
            _AGENT_ID, "Cancellation Agent", restricted_topic=_PII_TOPIC
        )
        payloads = [s.payload for s in scenario.chain.steps]
        assert not any(f"Can you help me with {_PII_TOPIC}" in p for p in payloads)
        assert any("{golden_id}" in p for p in payloads)


class TestGeneratorProducesAllDimensions:
    """Integration: ScenarioGenerator._policy_violation_scenarios covers all clauses."""

    def _make_sbom_with_agent(self) -> object:
        import uuid

        from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
        from nuguard.sbom.types import ComponentType
        node = Node(
            id=str(uuid.uuid4()),
            name="Main Agent",
            component_type=ComponentType.AGENT,
            confidence=1.0,
            metadata=NodeMetadata(),
        )
        return AiSbomDocument(target="test://local", nodes=[node], edges=[])

    def test_all_policy_dimensions_produce_scenarios(self) -> None:
        from nuguard.redteam.scenarios.generator import ScenarioGenerator

        sbom = self._make_sbom_with_agent()
        policy = CognitivePolicy(
            restricted_topics=["weapons"],
            restricted_actions=["delete all data"],
            hitl_triggers=["large transfer"],
            hitl_tool_conditions=[
                HitlToolCondition(tool_name="payment_tool", condition="amount > $500")
            ],
            allowed_topics=["cooking"],
            rate_limits={"api_calls": 5},
            raw_sections={"Custom Rules": ["do not share PII"]},
        )
        gen = ScenarioGenerator(sbom, policy)  # type: ignore[arg-type]
        scenarios = gen._policy_violation_scenarios()

        titles = [s.title for s in scenarios]

        # Each dimension must have produced at least one scenario
        assert any("weapons" in t or "Restricted Topic" in t for t in titles), "restricted_topics missing"
        assert any("delete all data" in t or "Restricted Action" in t for t in titles), "restricted_actions missing"
        assert any("large transfer" in t or "HITL Bypass" in t for t in titles), "hitl_triggers missing"
        assert any("payment_tool" in t or "HITL Bypass" in t for t in titles), "hitl_tool_conditions missing"
        assert any("cooking" in t or "Allowed Topic" in t for t in titles), "allowed_topics missing"
        assert any("api_calls" in t or "Rate Limit" in t for t in titles), "rate_limits missing"
        assert any("Custom Rules" in t or "Raw Policy" in t for t in titles), "raw_sections missing"


class TestMissingUniversalTopics:
    def test_empty_policy_returns_all_three_categories(self) -> None:
        missing = missing_universal_topics([])
        category_ids = {c for _, c in missing}
        assert category_ids == {"sexual_content", "violence", "self_harm"}

    def test_policy_already_covering_violence_excludes_it(self) -> None:
        missing = missing_universal_topics(["No content that glorifies violence or weapons."])
        category_ids = {c for _, c in missing}
        assert "violence" not in category_ids
        assert "sexual_content" in category_ids
        assert "self_harm" in category_ids

    def test_case_insensitive_match(self) -> None:
        missing = missing_universal_topics(["Discussion of SUICIDE or self-harm is prohibited."])
        category_ids = {c for _, c in missing}
        assert "self_harm" not in category_ids


class TestBuildRestrictedTopicVariants:
    def test_returns_three_distinct_variants(self) -> None:
        scenarios = build_restricted_topic_variants(_AGENT_ID, _PII_TOPIC)
        assert len(scenarios) == 3
        titles = [s.title for s in scenarios]
        assert len(set(titles)) == 3
        assert any("explicit" in t for t in titles)
        assert any("implicit (curious)" in t for t in titles)
        assert any("implicit (fiction)" in t for t in titles)

    def test_all_variants_target_same_goal_and_scenario_type(self) -> None:
        from nuguard.models.exploit_chain import GoalType, ScenarioType

        scenarios = build_restricted_topic_variants(_AGENT_ID, _PII_TOPIC)
        assert all(s.goal_type == GoalType.PROMPT_DRIVEN_THREAT for s in scenarios)
        assert all(s.scenario_type == ScenarioType.GUARDRAIL_BYPASS for s in scenarios)

    def test_implicit_variants_do_not_echo_raw_clause(self) -> None:
        scenarios = build_restricted_topic_variants(_AGENT_ID, _PII_TOPIC)
        for scenario in scenarios[1:]:
            for step in scenario.chain.steps:
                assert _PII_TOPIC.lower() not in step.payload.lower()

    def test_escalate_step_uses_llm_eval(self) -> None:
        scenarios = build_restricted_topic_variants(_AGENT_ID, _PII_TOPIC)
        for scenario in scenarios:
            assert scenario.chain.steps[-1].use_llm_eval is True


class TestUniversalSafetyScenarioTitles:
    """Universal-safety scenario titles must front-load the category id.

    generator.py's _policy_violation_scenarios() rewrites titles for scenarios
    produced by missing_universal_topics() so the category (sexual_content,
    violence, self_harm) survives the report's 60-char table truncation —
    embedding the full clause text in the title was pushing "self-harm" and
    "suicide" past the truncation point, making them undetectable in reports.
    """

    def _make_sbom_with_agent(self) -> object:
        import uuid

        from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
        from nuguard.sbom.types import ComponentType
        node = Node(
            id=str(uuid.uuid4()),
            name="Main Agent",
            component_type=ComponentType.AGENT,
            confidence=1.0,
            metadata=NodeMetadata(),
        )
        return AiSbomDocument(target="test://local", nodes=[node], edges=[])

    def test_universal_safety_titles_front_load_category_id(self) -> None:
        from nuguard.redteam.scenarios.generator import ScenarioGenerator

        sbom = self._make_sbom_with_agent()
        # App policy is silent on universal-safety topics — all 3 categories apply.
        policy = CognitivePolicy(restricted_topics=["off-topic banking chatter"])
        gen = ScenarioGenerator(sbom, policy)  # type: ignore[arg-type]
        scenarios = gen._policy_violation_scenarios()

        universal = [s for s in scenarios if "Universal Safety Probe (" in s.title]
        # 3 categories x 3 framings (explicit/curious/fiction) x 1 target node
        assert len(universal) == 9

        for s in universal:
            # report.py truncates at title_str[:60] + "…" only when len > 60,
            # so <= 60 survives untruncated.
            assert len(s.title) <= 60, f"title too long to survive table truncation: {s.title!r}"

        category_ids = {
            s.title.split("(", 1)[1].split(")", 1)[0] for s in universal
        }
        assert category_ids == {"sexual_content", "violence", "self_harm"}

    def test_self_harm_category_id_present_and_title_fits_truncation(self) -> None:
        from nuguard.redteam.scenarios.generator import ScenarioGenerator

        sbom = self._make_sbom_with_agent()
        policy = CognitivePolicy(restricted_topics=[])
        gen = ScenarioGenerator(sbom, policy)  # type: ignore[arg-type]
        scenarios = gen._policy_violation_scenarios()

        self_harm_titles = [s.title for s in scenarios if "(self_harm)" in s.title]
        assert self_harm_titles
        for title in self_harm_titles:
            # The raw clause text ("self-harm or suicide") is no longer in the
            # title, but the stable category_id substitutes as the identifier
            # that survives truncation in the report's Scenario Coverage table.
            assert len(title) <= 59
