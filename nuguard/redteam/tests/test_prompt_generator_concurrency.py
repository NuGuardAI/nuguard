"""enrich_all() must cap simultaneous generation completions at ``concurrency``."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.llm_engine.prompt_cache import PromptCache
from nuguard.redteam.llm_engine.prompt_generator import LLMPromptGenerator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.sbom.models import AiSbomDocument

_GOALS = [GoalType.DATA_EXFILTRATION, GoalType.TOOL_ABUSE, GoalType.POLICY_VIOLATION]


class _FakeLLM:
    """Records peak overlap; unparseable replies force per-scenario fallbacks."""

    def __init__(self, delay: float = 0.01) -> None:
        self.active = 0
        self.peak = 0
        self.calls = 0
        self._delay = delay

    async def complete(self, prompt: str, system: str = "", label: str = "", **kw: object) -> str:
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self._delay)
            return "not parseable"
        finally:
            self.active -= 1


def _scenarios(per_family: int) -> list[AttackScenario]:
    return [
        AttackScenario(
            scenario_id=f"{g.value}-{i}",
            goal_type=g,
            scenario_type=ScenarioType.GUARDRAIL_BYPASS,
            title=f"{g.value} {i}",
            description="d",
        )
        for g in _GOALS
        for i in range(per_family)
    ]


def _generator(llm: _FakeLLM) -> LLMPromptGenerator:
    sbom = AiSbomDocument(target="t", nodes=[], edges=[])
    return LLMPromptGenerator(llm, sbom, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("limit", [1, 3])
async def test_enrich_all_bounds_batches_and_fallbacks(tmp_path: Path, limit: int) -> None:
    # 12/family -> 2 batches per family; every batch fails to parse -> 36 fallbacks.
    llm = _FakeLLM()
    await _generator(llm).enrich_all(_scenarios(12), PromptCache(tmp_path), "k", concurrency=limit)
    assert llm.calls == 6 + 36
    assert llm.peak == limit


async def test_enrich_all_releases_slots_on_cancellation(tmp_path: Path) -> None:
    llm = _FakeLLM(delay=0.2)
    gen = _generator(llm)
    task = asyncio.create_task(
        gen.enrich_all(_scenarios(12), PromptCache(tmp_path), "k", concurrency=2)
    )
    await asyncio.sleep(0.05)
    assert llm.active == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert llm.active == 0

    # A fresh call is not starved by permits leaked from the cancelled one.
    llm._delay = 0.01
    await asyncio.wait_for(
        gen.enrich_all(_scenarios(1), PromptCache(tmp_path), "k2", concurrency=2), timeout=5
    )
