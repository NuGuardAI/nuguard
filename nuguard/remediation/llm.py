"""Resolves the LLM client used to author remediation text.

Shared by the ``behavior``, ``redteam``, and ``analyze`` CLI commands so
remediation always uses the one standard, configured LLM client instead of
each command resolving its own.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.config import NuGuardConfig


def resolve_remediation_llm_client(cfg: "NuGuardConfig") -> "LLMClient | None":
    """Resolve the LLMClient used for remediation-text synthesis.

    Uses the standard, top-level ``llm`` config (``cfg.litellm_model`` /
    ``cfg.litellm_api_key`` / ``cfg.litellm_api_base``) — the same client
    every other NuGuard command uses — so remediation prose is authored with
    whatever model the user has configured for the tool as a whole, not a
    separate redteam-specific model resolved independently of it. Returns
    ``None`` when no model is configured at all (LLM enrichment is optional
    everywhere — remediation then falls back to deterministic templates).
    """
    if not cfg.litellm_model:
        return None
    from nuguard.common.llm_client import LLMClient  # noqa: PLC0415

    return LLMClient(
        model=cfg.litellm_model,
        api_key=cfg.litellm_api_key,
        api_base=cfg.litellm_api_base,
    )
