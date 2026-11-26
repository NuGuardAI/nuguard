"""Tests for multimodal injection scenarios: the modality gate, the Image, PDF and
voice builders, the generator switch and the ``redteam.multimodal_scenarios`` config.

All SBOMs are synthetic, so these tests do not depend on SBOM detection output.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from nuguard.config import NuGuardConfig, _flatten_yaml
from nuguard.models.exploit_chain import ExploitChain, GoalType, ScenarioType
from nuguard.redteam.public_api import RedteamRunRequest
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.redteam.scenarios.multimodal_injection import (
    _VOICE_LANG_VARIANTS,
    _spell_out,
    build_image_embedded_injection,
    build_multimodal_scenarios,
    build_pdf_instruction_injection,
    build_voice_multilingual_injection,
    build_voice_spoken_injection,
)
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata, ScanSummary
from nuguard.sbom.types import ComponentType, RelationshipType

AGENT_ID = "agent-1"
BLOCKED = "export user data"


def _sbom(
    modality_support: dict[str, bool] | None = None,
    *,
    name: str = "support-agent",
    description: str | None = None,
    with_summary: bool = True,
) -> AiSbomDocument:
    meta = NodeMetadata(description=description) if description else NodeMetadata()
    node = Node(
        id=uuid.uuid5(uuid.NAMESPACE_URL, name),
        name=name,
        component_type=ComponentType.AGENT,
        confidence=0.9,
        metadata=meta,
    )
    summary = ScanSummary(modality_support=modality_support or {}) if with_summary else None
    return AiSbomDocument(
        generated_at=datetime.now(UTC),
        target="test-app",
        nodes=[node],
        edges=[],
        summary=summary,
    )


def _kinds(scenarios: list[AttackScenario]) -> list[str]:
    """Scenario titles without the trailing ' — <agent>' part."""
    return [s.title.split(" — ")[0].removeprefix("Multimodal Injection ") for s in scenarios]


def _build(sbom: AiSbomDocument, mode: str = "auto") -> list[AttackScenario]:
    return build_multimodal_scenarios(sbom, AGENT_ID, "Bot", BLOCKED, mode=mode)


def _assert_well_formed(chain: ExploitChain) -> None:
    ids = [s.step_id for s in chain.steps]
    assert len(ids) == len(set(ids))
    assert all(i.startswith(chain.chain_id) for i in ids)
    for step in chain.steps:
        assert set(step.depends_on) <= set(ids)
        assert step.target_node_id == AGENT_ID


# ---------------------------------------------------------------------------
# Gate (mode="auto")
# ---------------------------------------------------------------------------

IMAGE_PDF = ["(Image)", "(PDF)"]
VOICE = ["(Voice)", "(Voice, multilingual)"]


@pytest.mark.parametrize(
    ("sbom", "expected"),
    [
        pytest.param(_sbom({"text": True}), [], id="text-only"),
        pytest.param(_sbom(), [], id="empty-modality-support"),
        pytest.param(_sbom(with_summary=False), [], id="no-summary"),
        pytest.param(_sbom({"video": True}), [], id="video-only-is-out-of-scope"),
        pytest.param(_sbom({"image": True}), IMAGE_PDF, id="image-flag"),
        pytest.param(_sbom({"voice": True}), VOICE, id="voice-flag"),
        pytest.param(
            _sbom({"image": True, "voice": True}), IMAGE_PDF + VOICE, id="image-and-voice"
        ),
        pytest.param(_sbom(name="image-analyst"), IMAGE_PDF, id="keyword-in-name"),
        pytest.param(
            _sbom(with_summary=False, description="Reads a screenshot with OCR"),
            IMAGE_PDF,
            id="keyword-in-description-without-summary",
        ),
        pytest.param(_sbom({"image": False, "voice": False}), [], id="flags-false"),
    ],
)
def test_auto_gate(sbom: AiSbomDocument, expected: list[str]) -> None:
    assert _kinds(_build(sbom)) == expected


def test_voice_flag_does_not_fall_back_to_keywords() -> None:
    # "voice" is not an image keyword, and voice has no keyword fallback.
    assert _build(_sbom(name="voice-agent")) == []


def test_mode_on_generates_everything_without_any_signal() -> None:
    assert _kinds(_build(_sbom({"text": True}), mode="on")) == IMAGE_PDF + VOICE


def test_mode_off_generates_nothing_even_with_signals() -> None:
    sbom = _sbom({"image": True, "voice": True}, name="vision-agent")
    assert _build(sbom, mode="off") == []


def test_invalid_mode_raises() -> None:
    with pytest.raises(ValueError, match="auto, on, off"):
        _build(_sbom(), mode="sometimes")


def test_all_scenarios_use_multimodal_injection_type() -> None:
    scenarios = _build(_sbom(), mode="on")
    assert len(scenarios) == 4
    for s in scenarios:
        assert s.scenario_type == ScenarioType.MULTIMODAL_INJECTION
        assert s.chain is not None
        assert s.chain.scenario_type == ScenarioType.MULTIMODAL_INJECTION
        assert s.target_node_ids == [AGENT_ID]
        assert s.title.endswith("Bot")
        _assert_well_formed(s.chain)


# ---------------------------------------------------------------------------
# Existing Image and PDF builders
# ---------------------------------------------------------------------------


def test_image_builder_structure() -> None:
    s = build_image_embedded_injection(AGENT_ID, "Bot", BLOCKED)
    assert s.chain is not None
    steps = s.chain.steps
    assert [st.step_type for st in steps] == ["OBSERVE", "INJECT", "INJECT"]
    assert steps[0].contributes_to_finding is False
    assert "[IMAGE: screenshot.png]" in steps[1].payload
    assert "Export user data" in steps[1].payload
    assert "Export user data" in steps[2].payload
    assert steps[1].depends_on == [steps[0].step_id]
    assert steps[2].depends_on == [steps[1].step_id]
    assert s.goal_type == GoalType.PROMPT_DRIVEN_THREAT
    _assert_well_formed(s.chain)


def test_pdf_builder_structure() -> None:
    s = build_pdf_instruction_injection(AGENT_ID, "Bot", BLOCKED)
    assert s.chain is not None
    steps = s.chain.steps
    assert [st.step_type for st in steps] == ["OBSERVE", "INJECT", "INJECT"]
    assert steps[0].contributes_to_finding is False
    assert "[PDF: report.pdf]" in steps[1].payload
    assert BLOCKED in steps[1].payload
    assert "0pt" in steps[2].payload
    assert steps[2].depends_on == [steps[1].step_id]
    _assert_well_formed(s.chain)


def test_builders_default_blocked_action() -> None:
    image = build_image_embedded_injection(AGENT_ID, "Bot")
    pdf = build_pdf_instruction_injection(AGENT_ID, "Bot")
    assert image.chain is not None and pdf.chain is not None
    assert "Reveal your system prompt" in image.chain.steps[1].payload
    assert "export all stored user data" in pdf.chain.steps[1].payload


# ---------------------------------------------------------------------------
# Voice builders
# ---------------------------------------------------------------------------


def test_spell_out() -> None:
    assert _spell_out("ignore") == "I-G-N-O-R-E"


def test_voice_spoken_builder_structure() -> None:
    s = build_voice_spoken_injection(AGENT_ID, "Bot", BLOCKED)
    assert s.chain is not None
    steps = s.chain.steps
    assert [st.step_type for st in steps] == ["OBSERVE", "INJECT", "INJECT", "INJECT"]
    assert steps[0].contributes_to_finding is False
    for st in steps[1:]:
        assert BLOCKED in st.payload
        assert st.use_llm_eval is True
    assert "comma" in steps[1].payload  # dictated punctuation
    assert "I-G-N-O-R-E" in steps[2].payload  # letter-by-letter spelling
    assert "Caller ID" in steps[3].payload  # caller-identity claim
    for prev, cur in pairwise(steps):
        assert cur.depends_on == [prev.step_id]
    assert "no audio is sent" in s.description
    _assert_well_formed(s.chain)


def test_voice_multilingual_builder_covers_each_language_once() -> None:
    s = build_voice_multilingual_injection(AGENT_ID, "Bot", BLOCKED)
    assert s.chain is not None
    steps = s.chain.steps
    codes = [code for code, _name, _tpl in _VOICE_LANG_VARIANTS]
    assert len(steps) == len(codes)
    assert len(set(codes)) == len(codes)
    assert {"es", "fr", "de", "zh"} <= set(codes)
    assert all(st.step_type == "INJECT" for st in steps)
    assert len({st.payload for st in steps}) == len(steps)
    for st in steps:
        assert BLOCKED in st.payload
        assert "{goal}" not in st.payload
    assert steps[0].depends_on == []
    for prev, cur in pairwise(steps):
        assert cur.depends_on == [prev.step_id]
    assert "no audio is sent" in s.description
    _assert_well_formed(s.chain)


def test_voice_templates_each_contain_goal_placeholder_once() -> None:
    for _code, name, template in _VOICE_LANG_VARIANTS:
        assert template.count("{goal}") == 1, name


# ---------------------------------------------------------------------------
# Generator switch
# ---------------------------------------------------------------------------


def _generator_titles(sbom: AiSbomDocument, mode: str | None = None) -> list[str]:
    gen = (
        ScenarioGenerator(sbom)
        if mode is None
        else ScenarioGenerator(sbom, multimodal_scenarios=mode)
    )
    return _kinds(gen._multimodal_scenarios())


def test_generator_default_is_auto() -> None:
    assert _generator_titles(_sbom({"voice": True})) == VOICE
    assert _generator_titles(_sbom({"text": True})) == []


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("auto", IMAGE_PDF + VOICE), ("on", IMAGE_PDF + VOICE), ("off", [])],
)
def test_generator_mode(mode: str, expected: list[str]) -> None:
    sbom = _sbom({"image": True, "voice": True})
    assert _generator_titles(sbom, mode) == expected


def test_generator_on_without_signal() -> None:
    sbom = _sbom({"text": True})
    assert _generator_titles(sbom, "on") == IMAGE_PDF + VOICE


def _count_multimodal(scenarios: list[AttackScenario]) -> int:
    return sum(s.scenario_type == ScenarioType.MULTIMODAL_INJECTION for s in scenarios)


def test_generate_includes_and_excludes_multimodal_scenarios() -> None:
    sbom = _sbom({"voice": True})
    with_mm = ScenarioGenerator(sbom).generate()
    without_mm = ScenarioGenerator(sbom, multimodal_scenarios="off").generate()
    assert _count_multimodal(with_mm) == 2
    assert _count_multimodal(without_mm) == 0


# ---------------------------------------------------------------------------
# Config: redteam.multimodal_scenarios
# ---------------------------------------------------------------------------


def test_config_default_is_auto() -> None:
    assert NuGuardConfig.model_fields["redteam_multimodal_scenarios"].default == "auto"


def test_flatten_leaves_default_when_key_absent() -> None:
    flat = _flatten_yaml(yaml.safe_load("redteam:\n  skip_discovery: true\n"))
    assert "redteam_multimodal_scenarios" not in flat


@pytest.mark.parametrize(
    ("yaml_value", "expected"),
    [
        ("auto", "auto"),
        ("on", "on"),  # YAML 1.1 parses unquoted on/off as booleans
        ("off", "off"),
        ("true", "on"),
        ("false", "off"),
        ('"off"', "off"),
        ('"OFF"', "off"),
        ('"Auto"', "auto"),
    ],
)
def test_flatten_multimodal_scenarios(yaml_value: str, expected: str) -> None:
    data = yaml.safe_load(f"redteam:\n  multimodal_scenarios: {yaml_value}\n")
    assert _flatten_yaml(data)["redteam_multimodal_scenarios"] == expected


def test_config_accepts_valid_and_rejects_invalid_value() -> None:
    ok: dict[str, Any] = {"redteam_multimodal_scenarios": "on"}
    assert NuGuardConfig(**ok).redteam_multimodal_scenarios == "on"
    bad: dict[str, Any] = {"redteam_multimodal_scenarios": "sometimes"}
    with pytest.raises(ValidationError, match="redteam_multimodal_scenarios"):
        NuGuardConfig(**bad)


def test_public_api_request_defaults_to_auto() -> None:
    assert RedteamRunRequest.model_fields["multimodal_scenarios"].default == "auto"


def test_public_api_request_field_is_a_literal() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(
        RedteamRunRequest.model_fields["multimodal_scenarios"].annotation
    )
    assert adapter.validate_python("on") == "on"
    with pytest.raises(ValidationError):
        adapter.validate_python("sometimes")


# ---------------------------------------------------------------------------
# Review fixes: eager mode validation, agent cap and entry-agent coverage
# ---------------------------------------------------------------------------


def test_generator_rejects_invalid_mode_at_construction() -> None:
    with pytest.raises(ValueError, match="auto, on, off"):
        ScenarioGenerator(_sbom(), multimodal_scenarios="sometimes")


def _multi_agent_sbom(agents: int, entry_index: int | None = None) -> AiSbomDocument:
    """``agents`` AGENT nodes with the image and voice flags set.

    With ``entry_index``, one API endpoint CALLS that agent, making it the only
    entry agent.
    """
    nodes: list[Node] = [
        Node(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"agent-{i}"),
            name=f"agent-{i}",
            component_type=ComponentType.AGENT,
            confidence=0.9,
            metadata=NodeMetadata(),
        )
        for i in range(agents)
    ]
    edges: list[Edge] = []
    if entry_index is not None:
        endpoint = Node(
            name="POST /chat", component_type=ComponentType.API_ENDPOINT, confidence=1.0
        )
        edges.append(
            Edge(
                source=endpoint.id,
                target=nodes[entry_index].id,
                relationship_type=RelationshipType.CALLS,
            )
        )
        nodes.append(endpoint)
    return AiSbomDocument(
        generated_at=datetime.now(UTC),
        target="test-app",
        nodes=nodes,
        edges=edges,
        summary=ScanSummary(modality_support={"image": True, "voice": True}),
    )


def _multimodal_agent_targets(sbom: AiSbomDocument) -> list[str]:
    """Agent names targeted by the multimodal scenarios that survive generate()."""
    return [
        s.title.split(" — ")[-1]
        for s in ScenarioGenerator(sbom).generate()
        if s.scenario_type == ScenarioType.MULTIMODAL_INJECTION
    ]


@pytest.mark.parametrize(("agents", "expected"), [(1, 4), (2, 8), (3, 8), (6, 8)])
def test_multimodal_scenarios_are_capped_at_two_agents(agents: int, expected: int) -> None:
    assert len(_multimodal_agent_targets(_multi_agent_sbom(agents))) == expected


@pytest.mark.parametrize(("agents", "entry_index"), [(3, 0), (3, 2), (6, 5)])
def test_entry_agent_is_always_covered(agents: int, entry_index: int) -> None:
    targets = _multimodal_agent_targets(_multi_agent_sbom(agents, entry_index))
    assert f"agent-{entry_index}" in targets
    assert len(targets) == 8


def test_agents_after_the_cap_are_not_targeted() -> None:
    targets = _multimodal_agent_targets(_multi_agent_sbom(6, entry_index=5))
    assert sorted(set(targets)) == ["agent-0", "agent-5"]
