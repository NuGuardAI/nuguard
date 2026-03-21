"""Logs each attack step and its result for audit and replay."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

_log = logging.getLogger(__name__)


class ActionLogger:
    """Writes a JSONL log of every attack step for audit and replay."""

    def __init__(self, log_path: Path | None = None) -> None:
        self._path = log_path
        self._records: list[dict] = []

    def log(
        self,
        chain_id: str,
        step_id: str,
        goal_type: str,
        payload: str,
        response: str,
        succeeded: bool,
    ) -> None:
        """Log a single attack step result to memory and optionally to a JSONL file."""
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "chain_id": chain_id,
            "step_id": step_id,
            "goal_type": goal_type,
            "payload_length": len(payload),
            "response_length": len(response),
            "succeeded": succeeded,
        }
        self._records.append(record)
        if self._path:
            with self._path.open("a") as f:
                f.write(json.dumps(record) + "\n")

    @property
    def records(self) -> list[dict]:
        """Return a copy of all logged records."""
        return list(self._records)
