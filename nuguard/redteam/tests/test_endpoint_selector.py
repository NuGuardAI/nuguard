"""Tests for relevance-based API endpoint selection (endpoint_selector)."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from nuguard.redteam.scenarios.endpoint_selector import (
    PROBE_FAMILIES,
    EndpointPlan,
    build_endpoint_plan,
    heuristic_pick,
)
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType as NodeType


def _ep(i: int, method: str = "GET", params: bool = True, auth: bool = True) -> Node:
    return Node(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"ep{i}"),
        name=f"ep{i}",
        component_type=NodeType.API_ENDPOINT,
        confidence=0.9,
        metadata=NodeMetadata(
            endpoint=f"/api/r{i}/{{id}}" if params else f"/api/r{i}",
            method=method,
            auth_required=auth,
            path_params=["id"] if params else [],
        ),
    )


def _sbom(n: int) -> AiSbomDocument:
    return AiSbomDocument(
        generated_at=datetime.now(UTC),
        target="test-app",
        nodes=[_ep(i) for i in range(n)],
        edges=[],
    )


class _FakeLLM:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls = 0

    async def complete(self, prompt: str, system: str = "", label: str = "", **kw: object) -> str:
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.asyncio
async def test_no_plan_below_threshold() -> None:
    assert await build_endpoint_plan(_sbom(10), profile="full", llm=_FakeLLM("{}")) is None


@pytest.mark.asyncio
async def test_ci_without_llm_spot_checks_three() -> None:
    plan = await build_endpoint_plan(_sbom(30), profile="ci", llm=None)
    assert plan is not None
    for fam in PROBE_FAMILIES:
        assert len(plan.allowed[fam]) == 3
    assert plan.notes  # the missing-LLM fallback is reported, not silent


@pytest.mark.asyncio
async def test_ci_small_app_not_narrowed() -> None:
    assert await build_endpoint_plan(_sbom(3), profile="ci", llm=None) is None


@pytest.mark.asyncio
async def test_llm_scopes_families_above_threshold() -> None:
    sbom = _sbom(30)
    ids = [str(n.id) for n in sbom.nodes]
    reply = json.dumps({"xss": [ids[0], ids[1], "bogus-id"], "jwt_tampering": [ids[2]]})
    plan = await build_endpoint_plan(sbom, profile="full", llm=_FakeLLM(reply), threshold=25)
    assert plan is not None
    assert plan.allowed["xss"] == {ids[0], ids[1]}  # unknown ids dropped
    assert plan.permits("xss", ids[0]) and not plan.permits("xss", ids[5])
    assert plan.permits("idor", ids[5])  # family the LLM omitted stays unrestricted


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [RuntimeError("boom"), "not json"])
async def test_llm_failure_falls_back_with_note(reply: str | Exception) -> None:
    plan = await build_endpoint_plan(_sbom(30), profile="full", llm=_FakeLLM(reply))
    assert plan is not None and plan.notes
    assert plan.permits("xss", "anything")  # no narrowing on failure


def test_heuristic_prefers_attackable_endpoints() -> None:
    plain = _ep(0, params=False, auth=False)
    rich = _ep(1, method="POST")
    assert heuristic_pick([plain, rich], 1) == [rich]


def test_generator_honours_endpoint_plan() -> None:
    sbom = _sbom(6)
    keep = str(sbom.nodes[0].id)
    full = ScenarioGenerator(sbom)._api_attack_scenarios()
    plan = EndpointPlan(allowed={f: {keep} for f in PROBE_FAMILIES})
    narrowed = ScenarioGenerator(sbom, endpoint_plan=plan)._api_attack_scenarios()
    assert narrowed and len(narrowed) < len(full)
    assert all(s.target_node_ids == [keep] for s in narrowed)
