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


class LLMClient:
    """Thin async wrapper around LiteLLM.

    Args:
        model: LiteLLM model string.  Defaults to ``LITELLM_MODEL`` env var,
               then ``gemini/gemini-2.0-flash``.
        api_key: API key for the model provider.  Defaults to
                 ``LITELLM_API_KEY`` env var.  When ``None`` the client
                 returns canned responses.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        min_temperature: float | None = None,
    ) -> None:
        self.model: str = (
            model
            or os.environ.get("LITELLM_MODEL", "")
            or _DEFAULT_MODEL
        )
        self.api_key: str | None = api_key or os.environ.get("LITELLM_API_KEY") or None
        self.min_temperature: float | None = min_temperature

        if self.api_key is None:
            _log.debug(
                "No LITELLM_API_KEY found — LLMClient will return canned responses."
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
