"""Resolves the LLM client used to author remediation text.

Shared by the ``behavior``, ``redteam``, and ``analyze`` CLI commands so the
fallback chain lives in one place instead of being reimplemented per command.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.config import NuGuardConfig

_log = get_logger(__name__)


def resolve_remediation_llm_client(cfg: "NuGuardConfig") -> "LLMClient | None":
    """Resolve the LLMClient used for remediation-text synthesis.

    Fallback chain: ``redteam.llm`` (attack-payload model, generally the
    highest-capability model configured) -> ``redteam.eval_llm`` -> the
    general ``llm`` config. Applied uniformly by ``nuguard behavior``,
    ``nuguard redteam``, and ``nuguard analyze`` so remediation prose is
    authored by the strongest model configured, regardless of which command
    is run — ``cfg.redteam_llm_model`` etc. are parsed by ``load_config()``
    unconditionally even when the redteam section is otherwise untouched, so
    this resolves to ``None`` gracefully for a ``nuguard behavior`` run with
    no redteam.* config present.

    Caveat: ``redteam.llm`` is documented "must be uncensored" for
    attack-payload generation — reusing it here repurposes an
    uncensored-persona model for a defensive-writing task. This is safe in
    practice because every remediation LLM call passes ``system=`` explicitly
    (see ``nuguard.remediation.prompts``) — ``LLMClient.default_system_prompt``
    is only used when a caller omits ``system``, so the client's persona
    default is never engaged by remediation code.
    """
    model = cfg.redteam_llm_model
    api_key = cfg.redteam_llm_api_key
    api_base = cfg.redteam_llm_api_base
    if not model:
        model = cfg.redteam_eval_llm_model or cfg.litellm_model
        api_key = cfg.redteam_eval_llm_api_key or cfg.litellm_api_key
        api_base = cfg.redteam_eval_llm_api_base or cfg.litellm_api_base
    if not model:
        return None
    try:
        from nuguard.common.llm_client import LLMClient  # noqa: PLC0415

        return LLMClient(model=model, api_key=api_key, api_base=api_base)
    except Exception as exc:
        _log.warning("resolve_remediation_llm_client: could not build LLM client: %s", exc)
        return None
