"""Tests for redteam.mode=campaign config plumbing (increment 1 / commit 1)."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from nuguard.config import load_config
from nuguard.redteam.campaign.config import CampaignConfig
from nuguard.redteam.campaign.orchestrator import CampaignOrchestrator
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator


def _cfg(tmp_path: Path, body: str):
    f = tmp_path / "nuguard.yaml"
    f.write_text(body)
    return load_config(f)


def test_defaults_are_bounded_and_budgets_unlimited() -> None:
    c = CampaignConfig()
    assert c.max_turns_per_branch == 24 and c.max_branch_tokens == 8000
    assert c.max_run_target_requests is None and c.max_run_seconds is None


def test_bad_bounds_rejected() -> None:
    with pytest.raises(ValidationError):
        CampaignConfig(max_turns_per_branch=0)
    with pytest.raises(ValidationError):
        CampaignConfig(max_objective_turns=30, max_turns_per_branch=10)


def test_confirm_in_fresh_sessions_conflicts_with_no_reset_claim() -> None:
    with pytest.raises(ValidationError, match="cannot reset"):
        CampaignConfig(confirm_in_fresh_sessions=True, target_supports_session_reset=False)
    CampaignConfig(confirm_in_fresh_sessions=False, target_supports_session_reset=False)


def test_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError):
        CampaignConfig(bogus=1)  # type: ignore[call-arg]


def test_yaml_campaign_block_flows_into_config(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, "redteam:\n  mode: campaign\n  campaign:\n    max_turns_per_branch: 30\n")
    assert cfg.redteam_mode == "campaign"
    assert cfg.resolved_redteam_campaign_config().max_turns_per_branch == 30


def test_invalid_mode_rejected(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        _cfg(tmp_path, "redteam:\n  mode: turbo\n")


def test_legacy_warmup_conflicts_with_campaign_warmup(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="pre_run_warmup"):
        _cfg(tmp_path, "redteam:\n  mode: campaign\n  pre_run_warmup: 2\n")
    # Fine when campaign warm-up is off, or in legacy modes.
    _cfg(tmp_path, "redteam:\n  mode: campaign\n  pre_run_warmup: 2\n  campaign:\n    campaign_warmup: false\n")
    _cfg(tmp_path, "redteam:\n  mode: concurrent\n  pre_run_warmup: 2\n")


def test_campaign_orchestrator_reuses_legacy_preparation_but_enriches_just_in_time() -> None:
    assert issubclass(CampaignOrchestrator, RedteamOrchestrator)
    assert CampaignOrchestrator._eager_enrichment is False
    assert RedteamOrchestrator._eager_enrichment is True


def test_legacy_concurrency_limit_feeds_campaign_target_limiter(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, "redteam:\n  mode: campaign\n  max_concurrent_requests: 3\n")
    assert cfg.resolved_redteam_campaign_config().max_concurrent_requests == 3
    cfg2 = _cfg(tmp_path, "redteam:\n  mode: campaign\n  campaign:\n    max_concurrent_requests: 2\n")
    assert cfg2.resolved_redteam_campaign_config().max_concurrent_requests == 2
