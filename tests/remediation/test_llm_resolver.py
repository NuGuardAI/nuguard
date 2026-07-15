"""Tests for nuguard.remediation.llm.resolve_remediation_llm_client's fallback chain:
redteam.llm -> redteam.eval_llm -> the general llm config."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from nuguard.remediation.llm import resolve_remediation_llm_client


def _cfg(**overrides) -> SimpleNamespace:
    defaults = dict(
        redteam_llm_model=None,
        redteam_llm_api_key=None,
        redteam_llm_api_base=None,
        redteam_eval_llm_model=None,
        redteam_eval_llm_api_key=None,
        redteam_eval_llm_api_base=None,
        litellm_model="gemini/gemini-2.0-flash",
        litellm_api_key=None,
        litellm_api_base=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_prefers_redteam_llm_when_configured():
    cfg = _cfg(
        redteam_llm_model="openai/gpt-5",
        redteam_llm_api_key="rt-key",
        redteam_eval_llm_model="gemini/gemini-2.0-flash",
        litellm_model="gemini/gemini-2.0-flash",
    )
    with patch("nuguard.common.llm_client.LLMClient") as mock_cls:
        resolve_remediation_llm_client(cfg)
    mock_cls.assert_called_once_with(
        model="openai/gpt-5", api_key="rt-key", api_base=None
    )


def test_falls_back_to_eval_llm_when_redteam_llm_unset():
    cfg = _cfg(
        redteam_eval_llm_model="azure/gpt-5-mini",
        redteam_eval_llm_api_key="eval-key",
    )
    with patch("nuguard.common.llm_client.LLMClient") as mock_cls:
        resolve_remediation_llm_client(cfg)
    mock_cls.assert_called_once_with(
        model="azure/gpt-5-mini", api_key="eval-key", api_base=None
    )


def test_falls_back_to_general_llm_when_neither_redteam_field_set():
    cfg = _cfg(litellm_model="gemini/gemini-2.0-flash", litellm_api_key="general-key")
    with patch("nuguard.common.llm_client.LLMClient") as mock_cls:
        resolve_remediation_llm_client(cfg)
    mock_cls.assert_called_once_with(
        model="gemini/gemini-2.0-flash", api_key="general-key", api_base=None
    )


def test_returns_none_when_no_model_resolves_at_all():
    cfg = _cfg(litellm_model=None)
    result = resolve_remediation_llm_client(cfg)
    assert result is None


def test_returns_none_when_construction_raises():
    cfg = _cfg(litellm_model="gemini/gemini-2.0-flash")
    with patch("nuguard.common.llm_client.LLMClient", side_effect=RuntimeError("boom")):
        result = resolve_remediation_llm_client(cfg)
    assert result is None
