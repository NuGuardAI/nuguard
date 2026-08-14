"""
CipherBank Model Provider — Multi-Backend LLM Dispatcher
=========================================================
SBOM COMPLEXITY TEST #3 — "Aliased import + runtime subclass dispatch"
-----------------------------------------------------------------------
Detection challenge A — Import aliasing:
  The SBOM extractor's ``LangGraphAdapter`` / ``LLMClientsAdapter`` detect class
  instantiations by matching ``inst.class_name`` against known class names
  (``ChatOpenAI``, ``ChatAnthropic``, ``AzureChatOpenAI``, etc.).

  This file imports those classes under private aliases:
    ``from langchain.chat_models import ChatOpenAI as _OAI``
    ``from langchain_anthropic import ChatAnthropic as _Ant``
    ``from langchain_openai import AzureChatOpenAI as _Azure``

  When the code runs ``_OAI(...)`` or ``_Azure(...)``, the AST records
  ``class_name = "_OAI"`` — not ``"ChatOpenAI"`` — so it does not appear in
  ``ALL_LLM_CLASSES`` or ``LANGCHAIN_LLM_CLASS_PROVIDERS``.

Detection challenge B — Runtime dispatch:
  The actual instantiation is done inside ``_PROVIDER_MAP``, a dict that maps
  provider strings to factory callables:
    ``_PROVIDER_MAP = {"azure": lambda cfg: _Azure(...), ...}``

  Lambda bodies are not traversed as instantiation nodes in most AST parsers,
  so even if the adapter were looking for ``_Azure``, the lambda form hides it.

Detection challenge C — Model name from env/config:
  The model name is sourced from:
    1. A nested config dict loaded from an env var JSON blob
    2. With a ``.get()`` fallback to a variable built at import time
  Neither is a static string literal and neither appears in ``const_map``.

  Expected SBOM result:
    - FRAMEWORK node for ``langchain`` (import detected, can_handle() = True)
    - NO MODEL nodes (model names are runtime values)
    - NO specific provider class detections (_OAI / _Azure / _Ant not in known-class sets)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

# ── Aliased imports: class names hidden from instantiation detectors ──────────
from langchain.chat_models import ChatOpenAI as _OAI                  # alias
from langchain_openai import AzureChatOpenAI as _Azure                 # alias
from langchain_anthropic import ChatAnthropic as _Ant                  # alias
from langchain_community.chat_models import ChatCohere as _Cohere      # alias
from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger("orchestrator.model_provider")

# ---------------------------------------------------------------------------
# Model name resolution — deliberately not a static string
# ---------------------------------------------------------------------------
# Config blob from env var: '{"provider":"azure","model":"gpt-4o","temperature":0.1}'
_raw_cfg: dict[str, Any] = json.loads(os.getenv("LLM_PROVIDER_CONFIG", "{}"))

# Fallback model name — built from os.getenv, not a string literal
_DEFAULT_MODEL: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
_DEFAULT_TEMP: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))


def _azure_factory(cfg: dict[str, Any]) -> BaseChatModel:
    # _Azure = AzureChatOpenAI — alias hides class name from detector
    return _Azure(
        azure_deployment=cfg.get("model", _DEFAULT_MODEL),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        api_version="2024-05-13",
        temperature=cfg.get("temperature", _DEFAULT_TEMP),
    )


def _openai_factory(cfg: dict[str, Any]) -> BaseChatModel:
    # _OAI = ChatOpenAI — alias hides class name from detector
    return _OAI(
        model=cfg.get("model", _DEFAULT_MODEL),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        temperature=cfg.get("temperature", _DEFAULT_TEMP),
    )


def _anthropic_factory(cfg: dict[str, Any]) -> BaseChatModel:
    # _Ant = ChatAnthropic — alias hides class name from detector
    model_name = cfg.get("model", "claude-" + "3-5-sonnet" + "-20241022")  # concatenated
    return _Ant(
        model=model_name,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        temperature=cfg.get("temperature", _DEFAULT_TEMP),
    )


def _cohere_factory(cfg: dict[str, Any]) -> BaseChatModel:
    # _Cohere = ChatCohere — alias hides class name from detector
    return _Cohere(
        model=cfg.get("model", "command-r-plus"),
        cohere_api_key=os.getenv("COHERE_API_KEY", ""),
    )


# ---------------------------------------------------------------------------
# SBOM COMPLEXITY: Dispatch table — instantiation hidden inside a dict value
# (lambda or callable reference).  AST instantiation scanners look for
# ``Name(...)`` or ``Attribute.Name(...)`` call nodes, not dict-stored callables.
# ---------------------------------------------------------------------------
_PROVIDER_MAP: dict[str, Callable[[dict[str, Any]], BaseChatModel]] = {
    "azure":    _azure_factory,
    "openai":   _openai_factory,
    "anthropic": _anthropic_factory,
    "cohere":   _cohere_factory,
}


def get_llm(override_cfg: dict[str, Any] | None = None) -> BaseChatModel:
    """Return a configured LangChain chat model for the active provider.

    Provider and model are resolved from LLM_PROVIDER_CONFIG env var,
    falling back to ``"azure"`` with ``AZURE_OPENAI_DEPLOYMENT`` model.
    """
    cfg = {**_raw_cfg, **(override_cfg or {})}
    provider = cfg.get("provider", "azure")
    factory = _PROVIDER_MAP.get(provider)
    if factory is None:
        logger.warning("Unknown provider %r, falling back to azure", provider)
        factory = _PROVIDER_MAP["azure"]
    llm = factory(cfg)
    logger.info("LLM provider=%s model=%s", provider, cfg.get("model", _DEFAULT_MODEL))
    return llm


# ── Embedding model — also aliased and env-driven ─────────────────────────────
# The SBOM scanner might look for OpenAIEmbeddings class instantiation.
# Aliasing it to _Embeddings makes the class name unrecognisable.
from langchain_openai import AzureOpenAIEmbeddings as _Embeddings       # alias


def get_embeddings() -> _Embeddings:
    """Return Azure OpenAI embeddings (model from env, not static string)."""
    return _Embeddings(
        azure_deployment=os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-small"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        api_version="2024-05-13",
    )
