"""Phase 0 scaffold tests for the v2 red-team engine.

Covers config plumbing (``redteam.engine`` + ``redteam.v2.*``) and the
orchestrator stub returning an empty result.
"""
from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

from nuguard.config import RedteamV2Settings, load_config
from nuguard.redteam.v2 import RedteamV2Orchestrator, RedteamV2Result


def test_engine_defaults_to_v1() -> None:
    from nuguard.config import NuGuardConfig

    cfg = NuGuardConfig()
    assert cfg.redteam_engine == "v1"
    settings = cfg.resolved_redteam_v2_settings()
    assert isinstance(settings, RedteamV2Settings)
    assert settings.knowledge_base_version == "0.1.0"
    assert settings.semantic_judge_count == 3
    assert settings.dry_run_only is True


def test_redteam_v2_config_round_trips(tmp_path: Path) -> None:
    config_file = tmp_path / "nuguard.yaml"
    config_file.write_text(
        textwrap.dedent(
            """
            redteam:
              engine: v2
              v2:
                knowledge_base_version: 1.2.3
                phases:
                  - recon
                  - boundary_mapping
                semantic_judge:
                  count: 5
                  quorum: 3
                transferability_enabled: false
                max_per_phase: 7
                dry_run_only: false
            """
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_file)

    assert cfg.redteam_engine == "v2"
    settings = cfg.resolved_redteam_v2_settings()
    assert settings.knowledge_base_version == "1.2.3"
    assert settings.phases == ["recon", "boundary_mapping"]
    assert settings.semantic_judge_count == 5
    assert settings.semantic_judge_quorum == 3
    assert settings.transferability_enabled is False
    assert settings.max_per_phase == 7
    assert settings.dry_run_only is False


def test_orchestrator_stub_returns_empty_result() -> None:
    orch = RedteamV2Orchestrator(
        sbom=object(),
        target_url="http://localhost:9999",
        settings=RedteamV2Settings(),
        chat_path="/api/chat",
    )
    result = asyncio.run(orch.run())

    assert isinstance(result, RedteamV2Result)
    assert result.findings == []
    # Minimal SBOM has no addressable nodes → pipeline returns no_objectives.
    assert result.scan_outcome in ("no_findings", "no_objectives")
    assert result.resolved_chat_path == "/api/chat"
    assert result.token_usage.total_tokens == 0


def test_orchestrator_tolerates_v1_kwargs() -> None:
    """The constructor accepts (and ignores) extra v1-style kwargs so the CLI
    can switch engines with minimal branching."""
    orch = RedteamV2Orchestrator(
        sbom=object(),
        target_url="http://localhost:9999",
        concurrency=5,
        scenario_timeout=180.0,
        finding_triggers=None,
        guided_conversations=True,
    )
    result = asyncio.run(orch.run())
    assert result.findings == []
