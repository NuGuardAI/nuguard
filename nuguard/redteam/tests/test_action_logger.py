"""Unit tests for nuguard.redteam.target.action_logger.ActionLogger."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuguard.redteam.target.action_logger import ActionLogger


def _log_one(logger: ActionLogger, **kwargs) -> None:
    logger.log(
        chain_id=kwargs.get("chain_id", "chain-1"),
        step_id=kwargs.get("step_id", "step-1"),
        goal_type=kwargs.get("goal_type", "TOOL_ABUSE"),
        payload=kwargs.get("payload", "test payload"),
        response=kwargs.get("response", "test response"),
        succeeded=kwargs.get("succeeded", True),
    )


def test_records_starts_empty() -> None:
    logger = ActionLogger()
    assert logger.records == []


def test_after_one_log_records_has_one_entry() -> None:
    logger = ActionLogger()
    _log_one(logger)
    assert len(logger.records) == 1


def test_record_has_expected_keys() -> None:
    logger = ActionLogger()
    _log_one(logger)
    record = logger.records[0]
    for key in ("chain_id", "step_id", "goal_type", "succeeded"):
        assert key in record


def test_record_values_match_logged_values() -> None:
    logger = ActionLogger()
    logger.log(
        chain_id="chain-42",
        step_id="step-99",
        goal_type="DATA_EXFILTRATION",
        payload="inject payload",
        response="leaked data",
        succeeded=True,
    )
    record = logger.records[0]
    assert record["chain_id"] == "chain-42"
    assert record["step_id"] == "step-99"
    assert record["goal_type"] == "DATA_EXFILTRATION"
    assert record["succeeded"] is True


def test_record_stores_payload_and_response_lengths() -> None:
    logger = ActionLogger()
    logger.log(
        chain_id="c1",
        step_id="s1",
        goal_type="TOOL_ABUSE",
        payload="hello",
        response="world!",
        succeeded=False,
    )
    record = logger.records[0]
    assert record["payload_length"] == len("hello")
    assert record["response_length"] == len("world!")


def test_multiple_log_calls_accumulate_all_records() -> None:
    logger = ActionLogger()
    for i in range(5):
        logger.log(
            chain_id=f"chain-{i}",
            step_id=f"step-{i}",
            goal_type="TOOL_ABUSE",
            payload="p",
            response="r",
            succeeded=True,
        )
    assert len(logger.records) == 5


def test_log_path_writes_jsonl_file(tmp_path: Path) -> None:
    log_file = tmp_path / "audit.jsonl"
    logger = ActionLogger(log_path=log_file)
    _log_one(logger)
    assert log_file.exists()


def test_jsonl_file_contains_valid_json_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "audit.jsonl"
    logger = ActionLogger(log_path=log_file)
    logger.log(
        chain_id="chain-1",
        step_id="step-1",
        goal_type="TOOL_ABUSE",
        payload="test",
        response="response",
        succeeded=False,
    )
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["chain_id"] == "chain-1"
    assert parsed["step_id"] == "step-1"
    assert parsed["goal_type"] == "TOOL_ABUSE"
    assert parsed["succeeded"] is False


def test_jsonl_multiple_calls_write_multiple_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "audit.jsonl"
    logger = ActionLogger(log_path=log_file)
    _log_one(logger, chain_id="c1", step_id="s1")
    _log_one(logger, chain_id="c2", step_id="s2")
    lines = [l for l in log_file.read_text().strip().split("\n") if l]
    assert len(lines) == 2
