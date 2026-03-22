"""Guided adversarial conversation — data models.

A GuidedConversation is a live, LLM-steered multi-turn attack where each
attacker message is generated in real time based on the full conversation
history, rather than from a pre-built ExploitChain.

Unlike static chains (which are pre-built before any interaction), guided
conversations exploit agent self-disclosures and adapt the next prompt to
whatever the target just said.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from nuguard.models.exploit_chain import GoalType


class TurnRecord(BaseModel):
    """A single attacker→agent exchange within a guided conversation."""

    turn: int
    attacker_message: str
    agent_response: str
    progress_score: float = 0.0        # 0.0–1.0: assessed after this turn
    progress_reasoning: str = ""       # one-sentence LLM explanation
    tactic_used: str = ""              # e.g. "rapport", "normalise", "bridge", "escalate"
    milestone_reached: int | None = None   # index into GuidedConversation.milestones


class GuidedConversation(BaseModel):
    """Self-contained record of a live adversarial conversation.

    Populated progressively during execution by GuidedAttackExecutor.
    Serialisable to JSON for reporting.
    """

    conversation_id: str
    goal_type: GoalType
    goal_description: str          # e.g. "extract patient PHI from the medical agent"
    milestones: list[str] = Field(default_factory=list)   # filled by ConversationDirector.plan_milestones()
    max_turns: int = 12
    target_node_id: str = ""
    sbom_path: list[str] = Field(default_factory=list)
    owasp_asi_ref: str | None = None
    owasp_llm_ref: str | None = None
    mitre_atlas_technique: str | None = None

    # Populated during execution
    turns: list[TurnRecord] = Field(default_factory=list)
    final_progress: float = 0.0
    succeeded: bool = False
    abort_reason: Literal["max_turns", "hard_refusal", "target_unavailable", ""] = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @property
    def current_turn_number(self) -> int:
        return len(self.turns)

    @property
    def last_progress(self) -> float:
        if not self.turns:
            return 0.0
        return self.turns[-1].progress_score

    def consecutive_stalled_turns(self) -> int:
        """Count how many consecutive recent turns have progress_score < 0.2."""
        count = 0
        for t in reversed(self.turns):
            if t.progress_score < 0.2:
                count += 1
            else:
                break
        return count

    def format_transcript(self, max_chars_per_turn: int = 600) -> str:
        """Return a human-readable conversation transcript for reports."""
        lines = []
        for t in self.turns:
            score_str = f"{t.progress_score:.2f}"
            tactic = f"[{t.tactic_used}]" if t.tactic_used else ""
            milestone_str = f"[milestone {t.milestone_reached}]" if t.milestone_reached is not None else ""
            lines.append(
                f"Turn {t.turn} {tactic}{milestone_str} (progress={score_str})"
            )
            att = t.attacker_message[:max_chars_per_turn]
            if len(t.attacker_message) > max_chars_per_turn:
                att += "…"
            lines.append(f"  Attacker: {att}")
            resp = t.agent_response[:max_chars_per_turn]
            if len(t.agent_response) > max_chars_per_turn:
                resp += "…"
            lines.append(f"  Agent:    {resp}")
            if t.progress_reasoning:
                lines.append(f"  → {t.progress_reasoning}")
        return "\n".join(lines)
