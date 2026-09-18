from __future__ import annotations

import asyncio
from copy import deepcopy
from itertools import permutations

import pytest

from nuguard.models.finding import Finding, Severity
from nuguard.remediation.backfill import backfill_finding_remediation
from nuguard.remediation.models import RemediationArtefact
from nuguard.remediation.synthesizer import (
    RemediationSynthesizer,
    _artefact_dedup_key,
    _merge_artefacts,
)
from nuguard.sbom.models import AiSbomDocument, Edge, Node, NodeMetadata, NodeType


def _finding(
    fid: str = "f1", severity: object = "high", scenario: str = "ENV_VAR_PROBE", **kwargs: object
) -> dict:
    return {
        "finding_id": fid,
        "severity": severity,
        "scenario_type": scenario,
        "title": "Source finding",
        "description": "The observed boundary was crossed.",
        "affected_component": "AgentA",
    } | kwargs


def _synthesize(
    findings: list[dict], asynchronous: bool = False, synth: RemediationSynthesizer | None = None
) -> list[RemediationArtefact]:
    synth = synth or RemediationSynthesizer()
    if asynchronous:
        return asyncio.run(synth.synthesize_findings_async(findings))
    return synth.synthesize_findings(findings)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("severity", list(Severity))
@pytest.mark.parametrize("scenario", ["AUTH_BYPASS", "ENV_VAR_PROBE", "SYSTEM_PROMPT_EXTRACTION"])
def test_priorities_never_exceed_their_source(
    asynchronous: bool, severity: Severity, scenario: str
) -> None:
    finding = _finding(severity=severity, scenario=scenario, title="Access to tool 'delete_record'")
    original = deepcopy(finding)
    actions = _synthesize([finding], asynchronous)
    assert actions
    rank = list(Severity)
    assert all(rank.index(Severity(a.priority)) >= rank.index(severity) for a in actions)
    assert all(a.finding_ids == ["f1"] for a in actions)
    assert finding == original
    for action in actions:
        assert RemediationArtefact.model_validate_json(action.model_dump_json()) == action


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_duplicate_advice_preserves_every_id_and_highest_priority(asynchronous: bool) -> None:
    findings = [_finding("f1", "low"), _finding("f2", "high")]
    for ordered in (findings, list(reversed(findings))):
        actions = _synthesize(ordered, asynchronous)
        assert len(actions) == 1
        action = actions[0]
        assert action.finding_ids == ["f1", "f2"]
        assert action.priority == "high"
        assert set(action.per_finding_rationale) == {"f1", "f2"}
        records = [
            Finding.model_validate({k: v for k, v in f.items() if k != "scenario_type"})
            for f in findings
        ]
        backfill_finding_remediation(records, actions)
        assert all(f.remediation == action.rationale for f in records)


def _patch(fid: str, priority: str, **changes: object) -> RemediationArtefact:
    return RemediationArtefact.model_validate(
        dict(
            finding_ids=[fid],
            component="AgentA",
            component_type="AGENT",
            artefact_type="system_prompt_patch",
            priority=priority,
            patch_section="Controls",
            patch_text=f"Instruction {fid}",
            rationale=f"Reason {fid}",
        )
        | changes
    )


def test_prompt_merge_keeps_highest_priority_rationale_and_security_context() -> None:
    low = _patch("f1", "low", requires_auth=True, patch_location="prompts.py:12")
    high = _patch("f2", "high", requires_auth=True, patch_location="prompts.py:12")
    originals = [item.model_dump() for item in (low, high)]
    for ordered in permutations([low, high]):
        (merged,) = _merge_artefacts(list(ordered))
        assert merged.priority == "high"
        assert merged.finding_ids == ["f1", "f2"]
        assert merged.per_finding_rationale == {"f1": "Reason f1", "f2": "Reason f2"}
        assert merged.requires_auth is True
        assert merged.patch_location == "prompts.py:12"
    assert [item.model_dump() for item in (low, high)] == originals


@pytest.mark.parametrize(
    "changes",
    [
        {"patch_location": "other.py:10"},
        {"requires_hitl": True},
        {"privilege_scope": "admin"},
        {"component_type": "TOOL"},
    ],
)
def test_incompatible_patch_contexts_do_not_merge(changes: dict) -> None:
    first = _patch("f1", "high")
    second = _patch("f2", "high", **changes)
    assert len(_merge_artefacts([first, second])) == 2


def test_dedup_identity_includes_security_fields_and_source_location() -> None:
    first = _patch("f1", "high")
    second = first.model_copy(update={"finding_ids": ["f2"], "priority": "low"})
    assert _artefact_dedup_key(first) == _artefact_dedup_key(second)
    assert _artefact_dedup_key(first) != _artefact_dedup_key(
        first.model_copy(update={"requires_auth": True})
    )
    assert _artefact_dedup_key(first) != _artefact_dedup_key(
        first.model_copy(update={"patch_location": "another.py"})
    )


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_bad_handler_cannot_assign_another_findings_id(
    monkeypatch: pytest.MonkeyPatch, asynchronous: bool
) -> None:
    synth = RemediationSynthesizer()
    bad = _patch("unrelated", "high")
    monkeypatch.setattr(synth, "_dispatch_deterministic", lambda *args: [bad])
    with pytest.raises(ValueError, match="source finding IDs"):
        _synthesize([_finding()], asynchronous, synth)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_collection_caps_priority_without_mutating_handler_objects(
    monkeypatch: pytest.MonkeyPatch, asynchronous: bool
) -> None:
    produced = _patch("f1", "critical")
    before = produced.model_dump()
    synth = RemediationSynthesizer()
    monkeypatch.setattr(synth, "_dispatch_deterministic", lambda *args: [produced])
    (result,) = _synthesize([_finding(severity="info")], asynchronous, synth)
    assert result.priority == "info"
    assert produced.model_dump() == before


def test_empty_input_has_no_actions() -> None:
    assert _synthesize([]) == []
    assert _synthesize([], True) == []


def _node(name: str, kind: NodeType = NodeType.AGENT, high_privilege: bool = False) -> Node:
    return Node(
        name=name,
        component_type=kind,
        confidence=1,
        metadata=NodeMetadata(high_privilege=high_privilege),
    )


def test_privilege_inference_requires_reachability_and_unique_tool() -> None:
    agent = _node("AgentA")
    unrelated = _node("OtherAgent")
    first = _node("first_tool", NodeType.TOOL, True)
    second = _node("second_tool", NodeType.TOOL, True)
    not_tool = _node("not_a_tool", NodeType.AGENT, True)
    edges = [Edge(source=unrelated.id, target=first.id, relationship_type="CALLS")]
    sbom = AiSbomDocument(
        target="fixture",
        nodes=[agent, unrelated, first, second, not_tool],
        edges=edges,
    )
    finding = _finding(scenario="AUTH_BYPASS")
    assert RemediationSynthesizer(sbom)._resolve_privilege_tool_name(finding) == ""
    sbom.edges += [
        Edge(source=agent.id, target=second.id, relationship_type="CALLS"),
        Edge(source=second.id, target=agent.id, relationship_type="CALLS"),
        Edge(source=agent.id, target=not_tool.id, relationship_type="CALLS"),
    ]
    assert RemediationSynthesizer(sbom)._resolve_privilege_tool_name(finding) == "second_tool"
    sbom.edges.append(Edge(source=agent.id, target=first.id, relationship_type="CALLS"))
    assert RemediationSynthesizer(sbom)._resolve_privilege_tool_name(finding) == ""
    assert (
        RemediationSynthesizer(sbom)._resolve_privilege_tool_name(
            finding | {"tool_name": "declared_tool"}
        )
        == "declared_tool"
    )


def test_titled_tool_evidence_is_retained_without_sbom() -> None:
    finding = _finding(title="Unauthenticated agent can access tool 'explicit_tool'")
    assert RemediationSynthesizer()._resolve_privilege_tool_name(finding) == "explicit_tool"


def test_unattributed_auth_finding_does_not_invent_an_unprotected_tool() -> None:
    (action,) = _synthesize([_finding(scenario="AUTH_BYPASS", affected_component="GET /public")])
    assert action.component == "GET /public"
    assert action.component_type == "system"
    assert action.requires_auth is False
    assert "No unique high-privilege tool" in action.change_detail
    assert "without authentication" not in action.change_detail


def test_rendered_priority_matches_the_canonical_finding() -> None:
    import json

    from nuguard.redteam.report import to_json, to_markdown

    raw = _finding(scenario="AUTH_BYPASS", title="Access to tool 'delete_record'")
    finding = Finding.model_validate(raw)
    actions = _synthesize([raw])
    payload = json.loads(to_json([finding], remediation_plan=actions))
    assert payload["findings"][0]["severity"] == "high"
    assert all(action["priority"] != "critical" for action in payload["remediation_plan"])
    assert "[CRITICAL]" not in to_markdown([finding], remediation_plan=actions)
