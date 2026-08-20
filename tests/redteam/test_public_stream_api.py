from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nuguard.models.finding import Finding, Severity
from nuguard.models.token_usage import TokenUsage
from nuguard.redteam.public_api import RedteamRunRequest, RedteamRunResult, run_redteam_stream


def _finding() -> Finding:
    return Finding(
        finding_id="rt-1",
        title="Prompt injection",
        severity=Severity.HIGH,
        description="desc",
        goal_type="prompt_driven_threat",
        scenario_type="PROMPT_INJECTION",
    )


@pytest.mark.asyncio
async def test_parity_rt_001(monkeypatch):
    expected = RedteamRunResult(
        findings=[_finding()],
        scenario_records=[{"title": "s1"}],
        scan_outcome="high_findings",
        token_usage=TokenUsage(input_tokens=2, output_tokens=3),
        resolved_chat_path="/chat",
        resolved_chat_path_source="config",
        scenarios_run=1,
    )

    async def _fake_run(*args, **kwargs):
        _ = (args, kwargs)
        return expected

    monkeypatch.setattr("nuguard.redteam.public_api.run_redteam", _fake_run)

    handle = await run_redteam_stream(RedteamRunRequest(target_url="http://target"), sbom=MagicMock())
    events = [event async for event in handle.events]
    result = await handle.final_result()

    assert events[-1].event_type == "completed"
    assert result.scan_outcome == "high_findings"
    assert result.scenarios_run == 1
    assert len(result.findings) == 1
