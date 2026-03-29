"""LiteLLM wrapper shared across all nuguard capabilities.

When ``LITELLM_API_KEY`` is not set (or ``api_key`` is explicitly ``None``),
``LLMClient.complete`` returns a canned template response without making any
external network calls.  This ensures all capabilities function without an LLM
key.
"""

from __future__ import annotations

import os
from typing import Any

from nuguard.common.logging import get_logger

_log = get_logger(__name__)

_DEFAULT_MODEL = "gemini/gemini-2.0-flash"


def _resolve_api_key(model: str) -> str | None:
    """Return the best available API key for *model*.

    Checks provider-specific env vars before the generic LITELLM_API_KEY so
    that users who only have (e.g.) GEMINI_API_KEY set don't need to duplicate
    it under a second name.
    """
    # Provider-specific keys, checked in priority order
    _GEMINI_PREFIXES = ("gemini/", "google/", "vertex_ai/")
    if any(model.startswith(p) for p in _GEMINI_PREFIXES):
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "LITELLM_API_KEY"):
            val = os.environ.get(var)
            if val:
                _log.debug("Using %s for model %s", var, model)
                return val
        return None

    # Generic fallback for all other providers
    return os.environ.get("LITELLM_API_KEY") or None


class LLMClient:
    """Thin async wrapper around LiteLLM.

    Args:
        model: LiteLLM model string.  Defaults to ``LITELLM_MODEL`` env var,
               then ``gemini/gemini-2.0-flash``.
        api_key: API key for the model provider.  Defaults to
                 ``LITELLM_API_KEY`` env var.  When ``None`` the client
                 returns canned responses.
        api_base: Optional base URL override for self-hosted or proxy endpoints.
        budget_tokens: Optional token budget (informational; not enforced here).
        google_api_key: Optional Google API key for Vertex AI models.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        min_temperature: float | None = None,
        api_base: str | None = None,
        budget_tokens: int | None = None,
        google_api_key: str | None = None,
    ) -> None:
        self.model: str = (
            model
            or os.environ.get("LITELLM_MODEL", "")
            or _DEFAULT_MODEL
        )
        self.api_key: str | None = api_key or self._resolve_api_key(self.model)
        self.min_temperature: float | None = min_temperature
        self.api_base: str | None = api_base
        self.budget_tokens: int | None = budget_tokens
        self.google_api_key: str | None = google_api_key

        if self.api_key is None:
            _log.debug(
                "No API key found — LLMClient will return canned responses."
            )

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        label: str = "",
        **kwargs: Any,
    ) -> str:
        """Return a completion for *prompt*.

        Args:
            prompt: The user message to complete.
            system: Optional system prompt.
            label: Human-readable context string emitted in debug logs
                   (e.g. ``"payload-gen scenario=foo step=1"``).

        When no API key is configured the method returns
        :meth:`_canned_response` without making a network call.
        """
        if self.api_key is None:
            return self._canned_response(prompt)

        try:
            import litellm  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "litellm is required for LLM completions. "
                "Install with: pip install litellm"
            ) from exc

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        _log.debug(
            "LLMClient.complete model=%s prompt_len=%d%s",
            self.model,
            len(prompt),
            f" [{label}]" if label else "",
        )
        if self.min_temperature is not None:
            kwargs["temperature"] = max(
                float(kwargs.get("temperature", self.min_temperature)),
                self.min_temperature,
            )
        if self.api_base:
            kwargs.setdefault("api_base", self.api_base)
        if self.google_api_key:
            kwargs.setdefault("vertex_credentials", self.google_api_key)
        response = await litellm.acompletion(
            model=self.model,
            messages=messages,
            api_key=self.api_key,
            **kwargs,
        )
        content: str = response.choices[0].message.content or ""
        return content

    def _canned_response(self, prompt: str) -> str:
        """Return a deterministic template response for *prompt*.

        This is used when no LLM API key is configured.  It produces a
        structured placeholder that downstream consumers can detect and handle
        gracefully.
        """
        short = prompt[:80].replace("\n", " ")
        return (
            f"[NUGUARD_CANNED_RESPONSE] Template analysis for: {short!r}. "
            "Set LITELLM_API_KEY for LLM-enriched results."
        )
