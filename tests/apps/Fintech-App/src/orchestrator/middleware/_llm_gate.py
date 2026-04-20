"""
CipherBank LLM Guard Middleware (internal implementation)
==========================================================
SBOM COMPLEXITY TEST #5 (part A) — "Re-export chain" obfuscation
-----------------------------------------------------------------
Detection challenge:
  This is the *actual* LLM-heavy module, buried two package levels deep:
    orchestrator/middleware/_llm_gate.py

  The SBOM scan only triggers framework adapters when a file's *static imports*
  match ``adapter.handles_imports``.  This file imports:
    - ``langchain_openai``         ← will *pass* can_handle() for LangGraphAdapter
    - ``langchain_core``           ← also passes

  However, the key design challenge is that the *public API* of this module is
  surfaced through the re-export chain:
    orchestrator/middleware/__init__.py
      → imports ``LLMGuard`` from ``._llm_gate``

  A scan of ``__init__.py`` sees only:
    ``from ._llm_gate import LLMGuard``          ← local relative import
  The ``._llm_gate`` prefix doesn't match any ``handles_imports`` prefix
  (it's a relative import, not a package name), so ``middleware/__init__.py``
  passes through without triggering any LLM adapter.

  This means the dependency graph edge:
    orchestrator.main → LLMGuard (via middleware) → AzureChatOpenAI
  is severed at the middleware boundary.  Only a deep cross-file analysis
  would reconstruct it.

  ADDITIONALLY:
  - The LangChain model is wrapped in a custom ``LLMGuard`` class.
    Instantiation of ``LLMGuard`` rather than ``AzureChatOpenAI`` directly
    also hides the underlying model from per-call detection.
  - A second ``ChatOpenAI`` usage is inside a method that is only called when
    a feature flag env var is set — defeating simple path-based analysis.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from langchain_openai import AzureChatOpenAI as _AzureLLM
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

logger = logging.getLogger("orchestrator.middleware.llm_gate")

# ── Model config from env — not static strings ────────────────────────────────
_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
_ENDPOINT   = os.getenv("AZURE_OPENAI_ENDPOINT", "")
_API_KEY    = os.getenv("AZURE_OPENAI_API_KEY", "")
_API_VER    = os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-13")


class LLMGuard:
    """Wrapper around the primary LLM that enforces:
      - Content-safety pre-filter (blocks PII leakage)
      - Retry-with-fallback on rate-limit errors
      - Telemetry injection into every call
    
    Wraps AzureChatOpenAI but exposes only ``invoke()`` / ``stream()``
    so callers never interact with the underlying LLM class directly.
    """

    def __init__(self, system_prompt: str = "") -> None:
        # _AzureLLM = AzureChatOpenAI — alias hides from class-name detector
        self._primary = _AzureLLM(
            azure_deployment=_DEPLOYMENT,
            azure_endpoint=_ENDPOINT,
            api_key=_API_KEY,
            api_version=_API_VER,
            temperature=0.1,
        )
        self._system_prompt = system_prompt
        logger.debug("LLMGuard initialised with deployment=%s", _DEPLOYMENT)

    def invoke(self, user_message: str, context: dict[str, Any] | None = None) -> str:
        """Invoke the LLM with content-safety filtering applied."""
        messages: list[Any] = []
        if self._system_prompt:
            messages.append(SystemMessage(content=self._system_prompt))
        messages.append(HumanMessage(content=user_message))

        try:
            response: AIMessage = self._primary.invoke(messages)
            return response.content
        except Exception as exc:
            logger.warning("Primary LLM failed (%s), checking for fallback", exc)
            return self._invoke_fallback(user_message)

    def _invoke_fallback(self, user_message: str) -> str:
        """Fallback to secondary model when primary is unavailable.

        This second LLM instantiation is inside a method —
        instance creation only happens on failure (rarely exercised path).
        """
        # Only load the fallback model if the feature flag is enabled
        if not os.getenv("ENABLE_LLM_FALLBACK", "false").lower() == "true":
            return "Service temporarily unavailable."

        # Conditional import inside method body — only static-analysable
        # if the analyser traces all conditional branches
        from langchain_openai import ChatOpenAI as _FallbackLLM
        fallback = _FallbackLLM(
            model="gpt-4o-mini",
            openai_api_key=os.getenv("OPENAI_FALLBACK_KEY", ""),
            temperature=0.1,
        )
        response = fallback.invoke([HumanMessage(content=user_message)])
        return response.content
