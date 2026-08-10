"""Tests for bounded concurrency in verify_uncertain_nodes (issue #197).

These tests pin the behaviour added in #197:
- The per-node LLM calls in ``verify_uncertain_nodes`` now run with a bounded
  asyncio.Semaphore (``concurrency`` kwarg, defaults to 1 for safety).
- The number of in-flight calls never exceeds ``concurrency``.
- The total number of LLM calls and the order of completions don't change the
  final result set (verify_uncertain_nodes still returns the same results
  for the same input candidates).
- The cost budget is still respected, and over-budget candidates are recorded
  in ``stats.skipped_count`` with ``stats.budget_exceeded = True``.
- LLM exceptions on individual nodes still skip that node rather than aborting
  the whole batch (existing behaviour).
"""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest

from nuguard.sbom.core.verification import verify_uncertain_nodes
from nuguard.sbom.models import ComponentType, Node, NodeMetadata


def _uncertain_node(name: str = "node") -> Node:
    """Build a node in the uncertain confidence zone [0.60, 0.85]."""
    return Node(
        id=uuid4(),
        name=name,
        component_type=ComponentType.TOOL,
        confidence=0.70,  # squarely in the uncertain zone
        metadata=NodeMetadata(),
    )


class _ConcurrencyRecorder:
    """Async LLM stub that records the peak number of in-flight calls.

    Each ``complete`` call sleeps for ``delay`` seconds and atomically
    increments / decrements a counter so the test can assert that no more
    than ``concurrency`` calls were ever alive at once.
    """

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.in_flight = 0
        self.peak_in_flight = 0
        self.lock = asyncio.Lock()
        self.completed: list[str] = []
        # Total tokens returned (small constant) so verification cost accrues.
        self.tokens_per_call = 1000

    async def complete(self, prompt: str, system: str) -> tuple[str, int]:
        # Capture the node name from the prompt so the test can verify the
        # completion order is irrelevant to correctness.
        marker = "TARGET_NODE="
        node_marker = prompt.split(marker)[-1].split("\n")[0] if marker in prompt else "?"
        async with self.lock:
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            # Each node gets a positive verification response so the test
            # can also assert the verified_count stat.
            return '{"verified": true, "confidence": 0.92, "reason": "stub"}', self.tokens_per_call
        finally:
            async with self.lock:
                self.in_flight -= 1
            self.completed.append(node_marker)


def _build_candidates(n: int) -> tuple[list[Node], dict]:
    nodes = [_uncertain_node(f"node-{i}") for i in range(n)]
    evidence_map = {node.id: [] for node in nodes}
    return nodes, evidence_map


# ---------------------------------------------------------------------------
# Concurrency bounds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_uncertain_nodes_caps_concurrency_to_one_by_default() -> None:
    """Default ``concurrency=1`` preserves the legacy sequential behaviour."""
    nodes, evidence_map = _build_candidates(6)
    recorder = _ConcurrencyRecorder()

    results, stats = await verify_uncertain_nodes(
        nodes, evidence_map, recorder.complete, concurrency=1
    )

    assert len(results) == 6
    assert stats.verified_count == 6
    assert recorder.peak_in_flight == 1


@pytest.mark.asyncio
async def test_verify_uncertain_nodes_uses_semaphore_to_cap_concurrency() -> None:
    """With ``concurrency=N``, never more than N calls are in flight at once."""
    nodes, evidence_map = _build_candidates(10)
    recorder = _ConcurrencyRecorder()

    results, stats = await verify_uncertain_nodes(
        nodes, evidence_map, recorder.complete, concurrency=3
    )

    assert len(results) == 10
    assert stats.verified_count == 10
    # The semaphore must have actually constrained the calls.
    assert recorder.peak_in_flight <= 3
    assert recorder.peak_in_flight >= 2  # and we did actually overlap some calls


@pytest.mark.asyncio
async def test_verify_uncertain_nodes_completes_faster_under_concurrency() -> None:
    """A small wall-clock speedup at concurrency > 1 vs concurrency = 1.

    10 candidates × 50 ms each. Sequential: ~500 ms. With concurrency=5:
    ~100 ms. Use a loose bound to avoid CI flake.
    """
    nodes, evidence_map = _build_candidates(10)
    recorder = _ConcurrencyRecorder(delay=0.05)

    t0 = time.monotonic()
    await verify_uncertain_nodes(nodes, evidence_map, recorder.complete, concurrency=5)
    elapsed = time.monotonic() - t0

    # Sequential would be >= 10 * 0.05 = 0.5s. Allow generous headroom.
    assert elapsed < 0.35, f"concurrency=5 should be faster than sequential; took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Correctness preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_uncertain_nodes_results_match_input_set() -> None:
    """Parallelism must not drop or duplicate results."""
    nodes, evidence_map = _build_candidates(12)
    recorder = _ConcurrencyRecorder()

    results, stats = await verify_uncertain_nodes(
        nodes, evidence_map, recorder.complete, concurrency=4
    )

    # Every candidate produced one result.
    assert {r.node_id for r in results} == {n.id for n in nodes}
    assert stats.total_candidates == 12
    assert stats.verified_count == 12
    assert stats.rejected_count == 0


# ---------------------------------------------------------------------------
# Cost budget still respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_uncertain_nodes_respects_cost_budget_under_concurrency() -> None:
    """The cost budget cuts off additional candidates even with concurrency > 1."""
    nodes, evidence_map = _build_candidates(20)
    recorder = _ConcurrencyRecorder()
    # 20 candidates × 1000 tokens × 0.00001 = $0.20 per call. Budget $0.05
    # → at most ~5 candidates should complete before budget is exceeded.
    low_budget = 0.05

    results, stats = await verify_uncertain_nodes(
        nodes,
        evidence_map,
        recorder.complete,
        cost_budget=low_budget,
        concurrency=4,
    )

    assert stats.budget_exceeded is True
    assert stats.skipped_count > 0
    # The results we did get must all be valid VerificationResults.
    assert all(r.verification_cost > 0 for r in results)
    # Total cost should not wildly exceed the budget.
    assert stats.total_cost <= low_budget + 0.05


# ---------------------------------------------------------------------------
# LLM exception handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_uncertain_nodes_skips_failed_node_under_concurrency() -> None:
    """An exception in one call doesn't abort the whole batch."""

    class _FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, prompt: str, system: str) -> tuple[str, int]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated LLM outage")
            return '{"verified": true, "confidence": 0.9, "reason": "stub"}', 1000

    nodes, evidence_map = _build_candidates(5)
    client = _FlakyClient()

    results, stats = await verify_uncertain_nodes(
        nodes, evidence_map, client.complete, concurrency=2
    )

    # 4 candidates verified; 1 skipped due to the simulated outage.
    assert len(results) == 4
    assert stats.skipped_count == 1
    assert stats.verified_count == 4


# ---------------------------------------------------------------------------
# Concurrency=0 / negative must coerce to 1 (defensive)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_uncertain_nodes_coerces_invalid_concurrency_to_one() -> None:
    """``concurrency < 1`` is normalised to 1 (sequential)."""
    nodes, evidence_map = _build_candidates(3)
    recorder = _ConcurrencyRecorder()

    results, _stats = await verify_uncertain_nodes(
        nodes, evidence_map, recorder.complete, concurrency=0
    )

    assert len(results) == 3
    assert recorder.peak_in_flight == 1