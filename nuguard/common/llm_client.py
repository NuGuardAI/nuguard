"""LiteLLM wrapper shared across all nuguard capabilities.

When ``LITELLM_API_KEY`` is not set (or ``api_key`` is explicitly ``None``),
``LLMClient.complete`` returns a canned template response without making any
external network calls.  This ensures all capabilities function without an LLM
key.

Auth note for Vertex AI
-----------------------
LiteLLM's ``vertex_ai/`` model prefix requires Google Application Default
Credentials (ADC) or a service-account JSON file — a plain Google Cloud API key
(``AQ.…``) will trigger an ADC credential error.  If the resolved API key is
NOT a service-account JSON, ``LLMClient.__init__`` automatically rewrites the
model string to the equivalent ``gemini/`` prefix, which uses the Gemini REST
endpoint and accepts plain API keys directly.

To use true Vertex AI (e.g. for data-residency reasons) set
``GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json`` and keep ``vertex_ai/``
in the model string.
"""

from __future__ import annotations

import json
import os
from typing import Any

from nuguard.common.logging import get_logger

_log = get_logger(__name__)

_DEFAULT_MODEL = "gemini/gemini-2.0-flash"

_GEMINI_PREFIXES = ("gemini/", "google/", "vertex_ai/")
_VERTEX_PREFIXES = ("vertex_ai/",)


# ---------------------------------------------------------------------------
# Key / credential helpers
# ---------------------------------------------------------------------------

def _resolve_api_key(model: str) -> str | None:
    """Return the best available API key for *model*.

    Checks provider-specific env vars before the generic LITELLM_API_KEY so
    that users who only have (e.g.) GEMINI_API_KEY set don't need to duplicate
    it under a second name.
    """
    if any(model.startswith(p) for p in _GEMINI_PREFIXES):
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "LITELLM_API_KEY"):
            val = os.environ.get(var)
            if val:
                _log.debug("Using %s for model %s", var, model)
                return val
        return None

    # Generic fallback for all other providers
    return os.environ.get("LITELLM_API_KEY") or None


def _is_service_account_json(value: str) -> bool:
    """Return True if *value* looks like a service-account JSON credential."""
    try:
        obj = json.loads(value)
        return isinstance(obj, dict) and obj.get("type") == "service_account"
    except (json.JSONDecodeError, ValueError):
        pass
    # Also accept a file path to a JSON file
    if value.endswith(".json") and os.path.isfile(value):
        try:
            with open(value) as fh:
                obj = json.load(fh)
            return isinstance(obj, dict) and obj.get("type") == "service_account"
        except Exception:
            pass
    return False


def _vertex_to_gemini(model: str) -> str:
    """Rewrite ``vertex_ai/X`` → ``gemini/X`` for API-key-based auth."""
    _, _, model_id = model.partition("/")
    return f"gemini/{model_id}"


def _resolve_vertex_params() -> dict[str, str]:
    """Return vertex_project / vertex_location kwargs for LiteLLM.

    These are passed as call-level kwargs so LiteLLM's Vertex backend knows
    which GCP project and region to target.
    """
    params: dict[str, str] = {}
    project = (
        os.environ.get("VERTEXAI_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
    )
    location = (
        os.environ.get("VERTEXAI_LOCATION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
    )
    if project:
        params["vertex_project"] = project
    if location:
        params["vertex_location"] = location
    return params


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------

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
        self.api_key: str | None = api_key or _resolve_api_key(self.model)
        self.min_temperature: float | None = min_temperature
        self.api_base: str | None = api_base
        self.budget_tokens: int | None = budget_tokens
        self.google_api_key: str | None = google_api_key

        # vertex_ai/ requires ADC or a service-account JSON.  A plain API key
        # (e.g. AQ.… from Google Cloud Console) only works with the gemini/
        # REST endpoint.  Auto-rewrite so callers don't have to know this.
        if (
            any(self.model.startswith(p) for p in _VERTEX_PREFIXES)
            and self.api_key is not None
            and not _is_service_account_json(self.api_key)
            and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        ):
            rewritten = _vertex_to_gemini(self.model)
            _log.warning(
                "vertex_ai/ requires ADC or a service-account JSON; "
                "plain API key detected — rewriting model %r → %r. "
                "Set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON "
                "to use the Vertex AI endpoint instead.",
                self.model,
                rewritten,
            )
            self.model = rewritten

        if self.api_key is None:
            _log.debug("No API key found — LLMClient will return canned responses.")

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

        Returns the model response text.  On any LLM-level error (auth,
        rate-limit, network, invalid response) returns :meth:`_canned_response`
        so callers can continue without crashing.
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
        if any(self.model.startswith(p) for p in _VERTEX_PREFIXES):
            for k, v in _resolve_vertex_params().items():
                kwargs.setdefault(k, v)

        try:
            response = await litellm.acompletion(
                model=self.model,
                messages=messages,
                api_key=self.api_key,
                **kwargs,
            )
            content: str = response.choices[0].message.content or ""
            return content

        except litellm.AuthenticationError as exc:
            _log.warning(
                "LLM authentication failed (model=%s label=%s): %s",
                self.model, label or "-", exc,
            )
            if any(self.model.startswith(p) for p in _VERTEX_PREFIXES):
                _log.warning(
                    "Vertex AI requires ADC or a service-account JSON. "
                    "Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json "
                    "or switch the model to 'gemini/%s' for API-key auth.",
                    self.model.split("/", 1)[1],
                )
            return self._canned_response(prompt)

        except litellm.RateLimitError as exc:
            _log.warning(
                "LLM rate limit reached (model=%s label=%s): %s",
                self.model, label or "-", exc,
            )
            return self._canned_response(prompt)

        except litellm.ServiceUnavailableError as exc:
            _log.warning(
                "LLM service unavailable (model=%s label=%s): %s",
                self.model, label or "-", exc,
            )
            return self._canned_response(prompt)

        except litellm.BadRequestError as exc:
            _log.warning(
                "LLM bad request (model=%s label=%s): %s",
                self.model, label or "-", exc,
            )
            return self._canned_response(prompt)

        except litellm.APIConnectionError as exc:
            _log.warning(
                "LLM connection error (model=%s label=%s): %s",
                self.model, label or "-", exc,
            )
            return self._canned_response(prompt)

        except litellm.Timeout as exc:
            _log.warning(
                "LLM request timed out (model=%s label=%s): %s",
                self.model, label or "-", exc,
            )
            return self._canned_response(prompt)

        except Exception as exc:
            _log.warning(
                "Unexpected LLM error (model=%s label=%s): %s: %s",
                self.model, label or "-", type(exc).__name__, exc,
            )
            return self._canned_response(prompt)

    def _canned_response(self, prompt: str) -> str:
        """Return a deterministic template response for *prompt*.

        This is used when no LLM API key is configured or when a recoverable
        LLM error occurs.  It produces a structured placeholder that downstream
        consumers can detect and handle gracefully.
        """
        short = prompt[:80].replace("\n", " ")
        return (
            f"[NUGUARD_CANNED_RESPONSE] Template analysis for: {short!r}. "
            "Set LITELLM_API_KEY for LLM-enriched results."
        )
