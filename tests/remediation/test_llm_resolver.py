"""Tests for nuguard.remediation.llm.resolve_remediation_llm_client: it must
build the client from the standard, top-level `llm` config — the same
client every other NuGuard command uses — not a redteam-specific fallback
chain."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nuguard.remediation.llm import resolve_remediation_llm_client


def _cfg(**overrides) -> SimpleNamespace:
    defaults = dict(
        litellm_model="gemini/gemini-2.0-flash",
        litellm_api_key=None,
        litellm_api_base=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_uses_standard_llm_config():
    cfg = _cfg(litellm_model="gemini/gemini-2.0-flash", litellm_api_key="general-key")
    with patch("nuguard.common.llm_client.LLMClient") as mock_cls:
        resolve_remediation_llm_client(cfg)
    mock_cls.assert_called_once_with(
        model="gemini/gemini-2.0-flash", api_key="general-key", api_base=None
    )


def test_ignores_redteam_specific_config_even_if_present():
    # Any redteam.llm/eval_llm fields present on cfg must not influence the
    # resolved client — remediation always uses the standard `llm` config.
    cfg = _cfg(litellm_model="gemini/gemini-2.0-flash", litellm_api_key="general-key")
    cfg.redteam_llm_model = "openai/gpt-5"
    cfg.redteam_llm_api_key = "rt-key"
    with patch("nuguard.common.llm_client.LLMClient") as mock_cls:
        resolve_remediation_llm_client(cfg)
    mock_cls.assert_called_once_with(
        model="gemini/gemini-2.0-flash", api_key="general-key", api_base=None
    )


def test_returns_none_when_no_model_resolves_at_all():
    cfg = _cfg(litellm_model=None)
    result = resolve_remediation_llm_client(cfg)
    assert result is None


def test_construction_failure_propagates_instead_of_being_swallowed():
    cfg = _cfg(litellm_model="gemini/gemini-2.0-flash")
    with patch("nuguard.common.llm_client.LLMClient", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            resolve_remediation_llm_client(cfg)
