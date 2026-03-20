from __future__ import annotations

from argparse import Namespace

from xelo.cli import _build_extraction_config
from xelo.config import AiSbomConfig


def _scan_args(
    *,
    llm: bool = False,
    llm_model: str | None = None,
    llm_budget_tokens: int | None = None,
    llm_api_key: str | None = None,
) -> Namespace:
    return Namespace(
        llm=llm,
        llm_model=llm_model,
        llm_budget_tokens=llm_budget_tokens,
        llm_api_key=llm_api_key,
        llm_api_base=None,
    )


def test_extraction_config_respects_env_enable_llm_true(monkeypatch) -> None:
    monkeypatch.setenv("AISBOM_ENABLE_LLM", "true")
    cfg = AiSbomConfig()
    assert cfg.enable_llm is True


def test_extraction_config_respects_env_enable_llm_false(monkeypatch) -> None:
    monkeypatch.setenv("AISBOM_ENABLE_LLM", "false")
    cfg = AiSbomConfig()
    assert cfg.enable_llm is False


def test_cli_env_takes_precedence_when_llm_flag_not_passed(monkeypatch) -> None:
    """When --llm is not passed, the env var governs."""
    monkeypatch.setenv("AISBOM_ENABLE_LLM", "true")
    cfg = _build_extraction_config(_scan_args(llm=False))
    assert cfg.enable_llm is True


def test_cli_flag_enables_llm_regardless_of_env(monkeypatch) -> None:
    """When --llm is passed, LLM is enabled even if env says false."""
    monkeypatch.setenv("AISBOM_ENABLE_LLM", "false")
    cfg = _build_extraction_config(_scan_args(llm=True))
    assert cfg.enable_llm is True


def test_legacy_deterministic_only_input_maps_to_enable_llm() -> None:
    cfg = AiSbomConfig(deterministic_only=False)
    assert cfg.enable_llm is True
