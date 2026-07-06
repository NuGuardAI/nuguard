"""SimilarityMissTracker — suppress redundant attack scenarios after repeated misses.

After *miss_threshold* misses whose payload fingerprints are sufficiently similar
(Jaccard ≥ similarity_threshold), subsequent scenarios with overlapping payloads
and the same GoalType are skipped before any HTTP calls are made.

This prevents wasting test budget on attack variants that have already been
conclusively rejected by the target — e.g. continuing to try "bulk_export +
metadata-hiding" DATA_EXFILTRATION probes after 4 misses from different scenarios.

Design notes
------------
* Fingerprinting uses a token set extracted from all step payloads (static
  chains) and the goal description + milestones (guided conversations).
* Clusters are grouped by ``goal_type`` so that a string of DATA_EXFILTRATION
  misses does not suppress unrelated PRIVILEGE_ESCALATION attempts.
* Within a goal_type, clusters grow through union — each miss widens the covered
  token space, making future checks more conservative.
* Jaccard similarity is fast (no embeddings) and works well for short,
  keyword-rich security payloads.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from nuguard.common.logging import get_logger
from nuguard.redteam.scenarios.scenario_types import AttackScenario

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stop-word list: common English words + generic security/LLM scaffolding
# that appear in virtually every payload and carry no discriminating signal.
# ---------------------------------------------------------------------------
_STOP: frozenset[str] = frozenset({
    # Common English
    "the", "and", "for", "this", "that", "with", "your", "are", "can", "not",
    "you", "have", "from", "will", "about", "when", "what", "how", "please",
    "would", "could", "like", "need", "want", "help", "just", "also", "was",
    "been", "has", "its", "our", "their", "they", "some", "all", "any", "use",
    "show", "give", "get", "let", "see", "now", "then", "but", "one", "two",
    "make", "sure", "tell", "said", "text", "output", "input", "based", "only",
    "here", "more", "very", "into", "than", "such", "each", "must", "may",
    # Generic AI/security scaffolding
    "system", "user", "message", "response", "request", "data", "information",
    "agent", "chat", "llm", "model", "api", "step", "task", "function",
    "prompt", "query", "answer", "result", "following", "below", "above",
    "provide", "using", "used", "send", "receive", "return", "call",
    "test", "example", "format", "include", "details",
})


def _extract_tokens(text: str) -> frozenset[str]:
    """Lowercase, tokenise, and remove stop-words from *text*."""
    raw = re.findall(r"\b[a-z_][a-z0-9_]{2,}\b", text.lower())
    return frozenset(t for t in raw if t not in _STOP)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _scenario_tokens(scenario: AttackScenario) -> frozenset[str]:
    """Extract a discriminating token fingerprint from a scenario's payloads."""
    parts: list[str] = [scenario.title, scenario.description]
    if scenario.chain:
        for step in scenario.chain.steps:
            parts.append(step.payload)
    if scenario.guided_conversation:
        gc = scenario.guided_conversation
        parts.append(gc.goal_description)
        parts.extend(gc.milestones)
    return _extract_tokens(" ".join(parts))


def _scenario_cluster_key(scenario: AttackScenario) -> str:
    """Return the cluster key for *scenario*: goal_type + sorted target node IDs.

    Incorporating target node IDs isolates per-agent miss clusters so that
    misses against one agent do not suppress the same attack against a different
    agent.  Up to 2 node IDs are used (covers most single- and dual-node specs).

    Policy-derived scenarios (restricted-topic/action probes, HITL bypass, raw
    section probes) additionally fold in a hash of the underlying policy clause
    (``chain.policy_clauses[0]``). These builders reuse a handful of fixed
    boilerplate templates across every topic — only a short clause fragment
    differs — so the shared wording alone can push unrelated topics over the
    Jaccard similarity threshold once enough misses accumulate. Scoping the
    cluster by clause keeps miss-suppression confined to variants of the SAME
    topic (e.g. explicit/curious/fiction framings of "self-harm") without
    letting misses on one topic (e.g. off-topic chatter) silently suppress an
    entirely different one (e.g. self-harm).
    """
    targets = "|".join(sorted(scenario.target_node_ids[:2]))
    clause = scenario.chain.policy_clauses[0] if scenario.chain and scenario.chain.policy_clauses else ""
    clause_suffix = f":{hashlib.sha256(clause.encode()).hexdigest()[:8]}" if clause else ""
    return f"{scenario.goal_type.value}:{targets}{clause_suffix}"


class SimilarityMissTracker:
    """Track missed scenarios to suppress future attempts with similar payloads.

    Parameters
    ----------
    miss_threshold:
        Number of misses in a cluster before similar scenarios are suppressed.
        Default: 4.
    similarity_threshold:
        Minimum Jaccard overlap required to consider two scenarios "similar".
        Tuned so that scenario variants sharing the same attack noun (e.g.
        ``bulk_export``, ``exfil``, ``metadata``) cluster together but distinct
        attack families (e.g. prompt injection vs privilege escalation) do not.
        Default: 0.25.
    """

    def __init__(
        self,
        miss_threshold: int = 4,
        similarity_threshold: float = 0.25,
    ) -> None:
        self._miss_threshold = max(1, miss_threshold)
        self._similarity_threshold = max(0.0, min(1.0, similarity_threshold))
        # goal_type_value → list of [token_set, miss_count]
        self._clusters: dict[str, list[list]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_miss(self, scenario: AttackScenario) -> None:
        """Record that *scenario* completed with no finding."""
        tokens = _scenario_tokens(scenario)
        if not tokens:
            return
        key = _scenario_cluster_key(scenario)
        goal = scenario.goal_type.value
        for cluster in self._clusters[key]:
            if _jaccard(tokens, cluster[0]) >= self._similarity_threshold:
                cluster[0] = cluster[0] | tokens  # widen the cluster
                cluster[1] += 1
                _log.debug(
                    "SimilarityMissTracker: goal=%s cluster miss_count=%d tokens=%d",
                    goal, cluster[1], len(cluster[0]),
                )
                return
        # First miss in this cluster
        self._clusters[key].append([tokens, 1])
        _log.debug(
            "SimilarityMissTracker: new cluster for goal=%s (total clusters=%d)",
            goal, len(self._clusters[key]),
        )

    def should_skip(self, scenario: AttackScenario) -> bool:
        """Return True when this scenario is too similar to already-failed attacks.

        Called after acquiring the execution semaphore so that results from
        concurrently-running scenarios can be incorporated before we decide.
        """
        tokens = _scenario_tokens(scenario)
        if not tokens:
            return False
        key = _scenario_cluster_key(scenario)
        goal = scenario.goal_type.value
        for cluster in self._clusters.get(key, []):
            if (
                cluster[1] >= self._miss_threshold
                and _jaccard(tokens, cluster[0]) >= self._similarity_threshold
            ):
                _log.info(
                    "SimilarityMissTracker: skipping '%s' (goal=%s, "
                    "similar cluster missed %d times, Jaccard=%.2f)",
                    scenario.title,
                    goal,
                    cluster[1],
                    _jaccard(tokens, cluster[0]),
                )
                return True
        return False

    # ------------------------------------------------------------------
    # Inspection helpers (for tests / reporting)
    # ------------------------------------------------------------------

    def miss_count_for(self, scenario: AttackScenario) -> int:
        """Return the miss count of the cluster most similar to *scenario* (0 if none)."""
        tokens = _scenario_tokens(scenario)
        key = _scenario_cluster_key(scenario)
        best = 0
        for cluster in self._clusters.get(key, []):
            if _jaccard(tokens, cluster[0]) >= self._similarity_threshold:
                best = max(best, cluster[1])
        return best

    def cluster_count(self, goal_type_value: str) -> int:
        """Return the number of miss clusters recorded for *goal_type_value*.

        Note: counts clusters whose key starts with *goal_type_value* (since keys
        now include target node IDs after the colon separator).
        """
        prefix = goal_type_value + ":"
        return sum(
            len(clusters)
            for k, clusters in self._clusters.items()
            if k == goal_type_value or k.startswith(prefix)
        )
