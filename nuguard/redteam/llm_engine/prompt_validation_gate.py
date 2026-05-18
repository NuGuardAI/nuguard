"""Prompt validation and deduplication gate for redteam payload generation.

The gate runs after the LLM returns turn sequences and before those sequences
are injected into exploit chains. It enforces basic quality, scenario relevance,
and deduplication constraints so generated prompts are more targeted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.redteam.scenarios.scenario_types import AttackScenario

_log = get_logger(__name__)

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "i",
    "if", "in", "is", "it", "of", "on", "or", "that", "the", "to", "we", "with",
    "you", "your", "this", "those", "these", "our", "their", "they", "them",
}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        _log.warning("Invalid %s=%r; using default %.3f", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        _log.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-zA-Z0-9_\-]{3,}", text.lower())
    return [t for t in toks if t not in _STOPWORDS]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


@dataclass
class PromptValidationGateConfig:
    enabled: bool = _env_bool("NUGUARD_REDTEAM_PROMPT_GATE_ENABLED", True)
    min_turns: int = _env_int("NUGUARD_REDTEAM_PROMPT_GATE_MIN_TURNS", 2)
    max_turns: int = _env_int("NUGUARD_REDTEAM_PROMPT_GATE_MAX_TURNS", 3)
    min_turn_chars: int = _env_int("NUGUARD_REDTEAM_PROMPT_GATE_MIN_TURN_CHARS", 16)
    min_relevance_score: float = _env_float("NUGUARD_REDTEAM_PROMPT_GATE_MIN_RELEVANCE", 0.12)
    max_similarity: float = _env_float("NUGUARD_REDTEAM_PROMPT_GATE_MAX_SIMILARITY", 0.96)
    keep_best_effort_when_empty: bool = _env_bool(
        "NUGUARD_REDTEAM_PROMPT_GATE_KEEP_BEST_EFFORT", True
    )


class PromptValidationGate:
    """Filters low-quality and duplicate turn sequences for a scenario."""

    def __init__(self, config: PromptValidationGateConfig | None = None) -> None:
        self._cfg = config or PromptValidationGateConfig()

    def filter_sequences(
        self,
        scenario: "AttackScenario",
        sequences: list[list[str]],
    ) -> list[list[str]]:
        if not sequences:
            return []
        if not self._cfg.enabled:
            return sequences

        anchors = self._scenario_anchors(scenario)
        kept: list[list[str]] = []
        dropped = 0
        original = list(sequences)
        drop_reasons: dict[str, int] = {
            "invalid_shape": 0,
            "duplicate_exact": 0,
            "low_relevance": 0,
            "duplicate_near": 0,
        }

        # Pass 1: structural + relevance + exact dedup
        seen_exact: set[str] = set()
        for seq in sequences:
            if not self._valid_shape(seq):
                dropped += 1
                drop_reasons["invalid_shape"] += 1
                continue

            flat = self._flatten(seq)
            norm = _normalize_text(flat)
            if norm in seen_exact:
                dropped += 1
                drop_reasons["duplicate_exact"] += 1
                continue

            relevance = self._relevance(flat, anchors)
            if relevance < self._cfg.min_relevance_score:
                dropped += 1
                drop_reasons["low_relevance"] += 1
                continue

            seen_exact.add(norm)
            kept.append(seq)

        # Pass 2: near-duplicate dedup across already-kept sequences
        deduped: list[list[str]] = []
        for seq in kept:
            if any(
                SequenceMatcher(None, self._flatten(seq), self._flatten(prev)).ratio()
                >= self._cfg.max_similarity
                for prev in deduped
            ):
                dropped += 1
                drop_reasons["duplicate_near"] += 1
                continue
            deduped.append(seq)

        if not deduped and self._cfg.keep_best_effort_when_empty and original:
            best = max(original, key=lambda s: self._relevance(self._flatten(s), anchors))
            deduped = [best]
            _log.info(
                "prompt-gate fallback | scenario=%r kept best-effort sequence after dropping all",
                scenario.title,
            )

        if dropped:
            reason_summary = ", ".join(
                f"{reason}={count}"
                for reason, count in drop_reasons.items()
                if count > 0
            ) or "none"
            _log.info(
                "prompt-gate | scenario=%r dropped=%d kept=%d reasons=[%s]",
                scenario.title,
                dropped,
                len(deduped),
                reason_summary,
            )

        return deduped

    def _valid_shape(self, sequence: list[str]) -> bool:
        if len(sequence) < self._cfg.min_turns or len(sequence) > self._cfg.max_turns:
            return False
        return all(len(turn.strip()) >= self._cfg.min_turn_chars for turn in sequence)

    @staticmethod
    def _flatten(sequence: list[str]) -> str:
        return " ".join(turn.strip() for turn in sequence if turn and turn.strip())

    def _relevance(self, flat_text: str, anchor_tokens: set[str]) -> float:
        if not anchor_tokens:
            return 1.0
        payload_tokens = set(_tokenize(flat_text))
        if not payload_tokens:
            return 0.0
        overlap = payload_tokens.intersection(anchor_tokens)
        return len(overlap) / float(len(anchor_tokens))

    @staticmethod
    def _scenario_anchors(scenario: "AttackScenario") -> set[str]:
        parts: list[str] = [
            scenario.title,
            scenario.description,
            scenario.goal_type.value,
            scenario.scenario_type.value,
        ]
        if scenario.chain:
            for step in scenario.chain.steps:
                if step.step_type in ("INJECT", "INVOKE") and step.payload:
                    parts.append(step.payload[:250])
                    break

        tokens = _tokenize(" ".join(parts))
        # Keep a focused token set so relevance scoring stays stable.
        return set(tokens[:20])
