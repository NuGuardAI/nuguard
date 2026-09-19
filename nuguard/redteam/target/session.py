"""Attack session - tracks multi-turn conversation state."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class TurnRecord:
    turn: int
    prompt: str
    response: str
    tool_calls: list[dict]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AttackSession:
    """Tracks multi-turn conversation state for a single exploit chain execution."""

    session_id: str
    target_url: str
    chain_id: str
    turns: list[TurnRecord] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)  # step_id → response text
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    golden_data: str = ""                              # verbatim response from DISCOVER step
    golden_ids: list[str] = field(default_factory=list)  # IDs extracted from golden_data
    golden_name: str = ""                              # customer name extracted from DISCOVER step
    warmup_context: str = ""                           # agent self-disclosures from warmup turn
    # The full wire-level JSON body of the most recent request sent on this
    # session (post payload_key wrapping, chat_payload_extras merge, session
    # context) — set by TargetAppClient._send_impl() right before the POST,
    # regardless of success or failure, so callers can capture "the original
    # request that resulted in this error" even when send() only returns a
    # short error label like "[HTTP 502]". Concurrency-safe because each
    # scenario/chain execution gets its own AttackSession, even though the
    # underlying TargetAppClient is shared across concurrent scenarios.
    last_request_body: Any = None

    def add_turn(
        self, prompt: str, response: str, tool_calls: list[dict] | None = None
    ) -> TurnRecord:
        """Record a single prompt/response turn and return the TurnRecord."""
        record = TurnRecord(
            turn=len(self.turns) + 1,
            prompt=prompt,
            response=response,
            tool_calls=tool_calls or [],
        )
        self.turns.append(record)
        return record

    def add_evidence(self, step_id: str, response: str) -> None:
        """Store response text as evidence for a step."""
        self.evidence[step_id] = response

    @property
    def last_response(self) -> str:
        """Return the most recent response text, or empty string if no turns."""
        return self.turns[-1].response if self.turns else ""

    @property
    def all_tool_calls(self) -> list[dict]:
        """Return all tool calls across all turns."""
        calls = []
        for t in self.turns:
            calls.extend(t.tool_calls)
        return calls
