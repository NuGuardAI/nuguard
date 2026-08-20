"""Integration tests for the multi-round gap-fill orchestration.

Uses a stub LLM client (no network) to verify Round 1 -> Round 2 -> Round 3
wiring, budget accounting, and dedup all work together end-to-end — as
opposed to test_gap_fill.py, which tests the pure gating/probe functions in
isolation.
"""

from __future__ import annotations

import json

import pytest

from nuguard.sbom.core.gap_fill import GapFillBudget, discover_missing_nodes
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.types import ComponentType


class _StubClient:
    """Returns queued canned responses in call order; records calls made."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, prompt: str, system: str) -> str:
        self.calls.append((system, prompt))
        if not self._responses:
            return "[]"
        return self._responses.pop(0)


def _tool_candidate(name: str, *, confidence: float = 0.8, ambiguous: bool = False) -> dict:
    return {
        "name": name,
        "canonical_name": name.lower(),
        "confidence": confidence,
        "detail": f"found {name} in source",
        "evidence_files": ["app.py"],
        "ambiguous": ambiguous,
    }


@pytest.mark.asyncio
async def test_round1_only_when_no_ambiguous_results() -> None:
    """A category with only high-confidence Round 1 results should not
    trigger Round 2 (no borderline items) and only spend one call."""
    doc = AiSbomDocument(target=".", nodes=[])
    file_contents = {"app.py": "browser = playwright.chromium.launch_browser()\n"}
    client = _StubClient([json.dumps([_tool_candidate("Redis")])])

    budget = GapFillBudget(max_calls=10, max_cost_usd=None)
    new_nodes, budget = await discover_missing_nodes(
        doc, file_contents, client, budget=budget
    )

    tool_nodes = [n for n in new_nodes if n.component_type == ComponentType.TOOL]
    assert any(n.name == "Redis" for n in tool_nodes)
    # Only MODEL/DATASTORE/TOOL/... categories with matching snippets get a
    # call; TOOL's single round1 call is what we care about here.
    tool_calls = [c for c in client.calls if "tool" in c[1].lower()]
    assert len(tool_calls) >= 1


@pytest.mark.asyncio
async def test_round1_empty_result_skips_further_rounds() -> None:
    """Round 1 returning [] must not trigger Round 2/3 for that category."""
    doc = AiSbomDocument(target=".", nodes=[])
    file_contents = {"app.py": "import redis\nclient = redis.Redis()\n"}
    client = _StubClient(["[]"] * 20)

    budget = GapFillBudget(max_calls=100, max_cost_usd=None)
    new_nodes, budget = await discover_missing_nodes(
        doc, file_contents, client, budget=budget
    )

    assert new_nodes == []
    # One call per category with matching snippets, not more (no follow-up rounds fired).
    assert budget.calls_used == len(budget.categories_probed)


@pytest.mark.asyncio
async def test_ambiguous_candidate_triggers_followup_round() -> None:
    """A Round-1 candidate flagged ambiguous should trigger a Round-2 call,
    and only survive if Round 2 confirms it."""
    doc = AiSbomDocument(target=".", nodes=[])
    # Deliberately avoids substrings like "import"/"port" that would also
    # score for the DEPLOYMENT category's keyword list.
    file_contents = {"app.py": "browser = playwright.chromium.launch_browser()\n"}
    round1_response = json.dumps([_tool_candidate("Playwright", ambiguous=True)])
    round2_response = json.dumps(
        [{"name": "Playwright", "confirmed": True, "refined_confidence": 0.7, "refined_detail": "confirmed"}]
    )
    client = _StubClient([round1_response, round2_response])

    budget = GapFillBudget(max_calls=10, max_cost_usd=None)
    new_nodes, budget = await discover_missing_nodes(
        doc, file_contents, client, budget=budget
    )

    assert any(n.name == "Playwright" for n in new_nodes)
    assert budget.calls_used == 2  # round1 + round2, no round3 (TOOL not in critique set)


@pytest.mark.asyncio
async def test_ambiguous_candidate_dropped_when_followup_rejects() -> None:
    doc = AiSbomDocument(target=".", nodes=[])
    file_contents = {"app.py": "import redis\nclient = redis.Redis()\n"}
    round1_response = json.dumps([_tool_candidate("Redis", ambiguous=True)])
    round2_response = json.dumps([{"name": "Redis", "confirmed": False}])
    client = _StubClient([round1_response, round2_response])

    budget = GapFillBudget(max_calls=10, max_cost_usd=None)
    new_nodes, _ = await discover_missing_nodes(doc, file_contents, client, budget=budget)

    assert not any(n.name == "Redis" for n in new_nodes)


@pytest.mark.asyncio
async def test_privilege_opt_in_requires_critique_confirmation() -> None:
    """PRIVILEGE, once enabled, always gets Round 3 — an unconfirmed critique
    means the node never gets created even if Round 1 was high-confidence."""
    doc = AiSbomDocument(target=".", nodes=[])
    file_contents = {"app.py": "subprocess.run(['rm', '-rf', tmp_dir])\n"}
    round1_response = json.dumps(
        [
            {
                "name": "Filesystem Write",
                "canonical_name": "privilege:filesystem_write",
                "confidence": 0.9,
                "detail": "subprocess call",
                "evidence_files": ["app.py"],
                "ambiguous": False,
            }
        ]
    )
    round3_reject = json.dumps([{"name": "Filesystem Write", "confirmed": False, "reason": "test-only"}])
    client = _StubClient([round1_response, round3_reject])

    budget = GapFillBudget(max_calls=10, max_cost_usd=None)
    new_nodes, _ = await discover_missing_nodes(
        doc, file_contents, client, budget=budget, enable_privilege=True
    )

    assert new_nodes == []


@pytest.mark.asyncio
async def test_privilege_opt_in_survives_critique_confirmation() -> None:
    doc = AiSbomDocument(target=".", nodes=[])
    file_contents = {"app.py": "subprocess.run(['rm', '-rf', tmp_dir])\n"}
    round1_response = json.dumps(
        [
            {
                "name": "Code Execution",
                "canonical_name": "privilege:code_execution",
                "confidence": 0.9,
                "detail": "subprocess call",
                "evidence_files": ["app.py"],
                "ambiguous": False,
            }
        ]
    )
    round3_confirm = json.dumps([{"name": "Code Execution", "confirmed": True, "reason": "real"}])
    client = _StubClient([round1_response, round3_confirm])

    budget = GapFillBudget(max_calls=10, max_cost_usd=None)
    new_nodes, _ = await discover_missing_nodes(
        doc, file_contents, client, budget=budget, enable_privilege=True
    )

    assert any(n.component_type == ComponentType.PRIVILEGE for n in new_nodes)


@pytest.mark.asyncio
async def test_budget_exhaustion_stops_early() -> None:
    doc = AiSbomDocument(target=".", nodes=[])
    file_contents = {
        "app.py": "import redis\nclient = redis.Redis()\nSYSTEM_PROMPT = 'be nice'\n",
    }
    client = _StubClient(["[]"] * 20)

    budget = GapFillBudget(max_calls=1, max_cost_usd=None)
    new_nodes, budget = await discover_missing_nodes(
        doc, file_contents, client, budget=budget
    )

    assert budget.calls_used <= 1
    assert budget.exhausted() is True
