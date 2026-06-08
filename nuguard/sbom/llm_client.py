"""
LLM client for AI SBOM enrichment.

Wraps litellm to provide a provider-agnostic interface supporting:
- Any litellm-compatible model string (e.g. "gpt-4o-mini",
  "anthropic/claude-3-haiku-20240307", "ollama/mistral")
- Structured JSON output via response_format
- Token budget tracking with BudgetExhaustedError
- Graceful degradation: callers catch exceptions and fall back to
  deterministic output

Only imported when AiSbomConfig.enable_llm=True.
"""

from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)

_litellm_noise_suppressed = False

# Reasoning / thinking models that reject temperature (or accept only temperature=1).
# Covers OpenAI o1/o3/o4 and gpt-5 class; mirrors nuguard.common.llm_client.
_REASONING_MODEL_FAMILIES = ("o1", "o3", "o4", "gpt-5")


def _is_reasoning_model(model: str) -> bool:
    """Return True for models that do not support arbitrary temperature values."""
    _, _, name = model.rpartition("/")
    name_lower = (name or model).lower()
    return any(name_lower.startswith(f) for f in _REASONING_MODEL_FAMILIES)


def _suppress_litellm_noise(litellm: Any) -> None:
    """Silence litellm's startup credential-probe warnings (one-time)."""
    global _litellm_noise_suppressed
    if _litellm_noise_suppressed:
        return
    litellm.suppress_debug_info = True
    litellm.set_verbose = False
    logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
    logging.getLogger("litellm").setLevel(logging.CRITICAL)
    _litellm_noise_suppressed = True


class BudgetExhaustedError(Exception):
    """Raised when the token budget for LLM calls has been exhausted."""


_VERTEX_BASE = "https://aiplatform.googleapis.com/v1/publishers/google/models"


class LLMClient:
    """Provider-agnostic LLM client backed by litellm.

    Parameters
    ----------
    model:
        Any litellm-compatible model string, e.g. ``"gpt-4o-mini"``,
        ``"anthropic/claude-haiku-4-5"``, ``"ollama/mistral"``, or
        ``"vertex_ai/gemini-2.5-flash"`` (uses direct httpx when
        ``google_api_key`` is provided).
    api_key:
        Optional API key. When ``None``, litellm falls back to the
        corresponding environment variable (``OPENAI_API_KEY``,
        ``ANTHROPIC_API_KEY``, etc.).
    api_base:
        Optional base URL override (e.g. Azure AI Foundry endpoint).
        When ``None``, litellm uses the provider default.
    budget_tokens:
        Maximum total tokens (prompt + completion) to spend across all
        calls on this client instance. Raises ``BudgetExhaustedError``
        once the budget is exceeded.
    google_api_key:
        GCP API key for Vertex AI Gemini. When set alongside a
        ``vertex_ai/*`` model, requests bypass litellm and hit
        ``aiplatform.googleapis.com`` directly with ``?key=``.
    vertex_location:
        Vertex AI region (e.g. ``"us-central1"``). Reserved for future
        use; the global publisher endpoint does not require it.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        api_base: str | None = None,
        budget_tokens: int = 50_000,
        google_api_key: str | None = None,
        vertex_location: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._budget = budget_tokens
        self._tokens_used = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._google_api_key = google_api_key
        self._vertex_location = vertex_location

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        return self._output_tokens

    def _check_budget(self) -> None:
        if self._tokens_used >= self._budget:
            raise BudgetExhaustedError(
                f"Token budget of {self._budget} exhausted (used: {self._tokens_used})"
            )

    def _record_usage(self, response: Any) -> int:
        """Extract token count from litellm response and update budget."""
        try:
            usage = response.usage
            prompt = usage.prompt_tokens or 0
            completion = usage.completion_tokens or 0
            tokens = prompt + completion
        except (AttributeError, TypeError):
            prompt = completion = tokens = 0
        self._input_tokens += prompt
        self._output_tokens += completion
        self._tokens_used += tokens
        return tokens

    def _is_vertex_ai(self) -> bool:
        return self._model.startswith("vertex_ai/")

    async def _vertex_ai_complete(self, system: str, user: str) -> tuple[str, int]:
        """Direct httpx call to Vertex AI Gemini using a GCP API key.

        Hits ``https://aiplatform.googleapis.com/v1/publishers/google/models/
        {model}:generateContent?key={api_key}`` — the same endpoint used by
        the NuGuard-app reference implementation.  Does NOT use litellm.
        """
        import httpx

        model_name = self._model[len("vertex_ai/") :]
        url = f"{_VERTEX_BASE}/{model_name}:generateContent?key={self._google_api_key}"
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "system_instruction": {"parts": [{"text": system}]},
            "generationConfig": {"temperature": 0.0},
        }
        _log.debug("_vertex_ai_complete model=%s", model_name)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=body, headers={"Content-Type": "application/json"})
        if resp.status_code != 200:
            raise RuntimeError(f"Vertex AI error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        candidates = data.get("candidates", [])
        text: str = candidates[0]["content"]["parts"][0]["text"] if candidates else ""
        usage = data.get("usageMetadata", {})
        prompt = usage.get("promptTokenCount", 0)
        completion = usage.get("candidatesTokenCount", 0)
        tokens = usage.get("totalTokenCount", prompt + completion)
        if not prompt and not completion and tokens:
            prompt = tokens // 2
            completion = tokens - prompt
        self._input_tokens += prompt
        self._output_tokens += completion
        self._tokens_used += tokens
        return text, tokens

    async def complete_text(self, system: str, user: str) -> tuple[str, int]:
        """Call the LLM and return ``(response_text, tokens_used)``.

        Signature matches the ``LLMCallFn`` protocol expected by
        ``core.verification.verify_uncertain_nodes``.

        Raises
        ------
        BudgetExhaustedError
            When the token budget has been exhausted before this call.
        Exception
            Any litellm error propagates to the caller, which should
            fall back to deterministic behaviour.
        """
        if self._is_vertex_ai() and self._google_api_key:
            self._check_budget()
            return await self._vertex_ai_complete(system, user)

        try:
            import litellm
        except ImportError as exc:
            raise ImportError(
                "litellm is required for LLM enrichment. "
                "Install it with: pip install 'ai-sbom[llm]'"
            ) from exc

        _suppress_litellm_noise(litellm)
        self._check_budget()

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if not _is_reasoning_model(self._model):
            kwargs["temperature"] = 0.0
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base

        _log.debug(
            "complete_text model=%s budget_left=%d", self._model, self._budget - self._tokens_used
        )
        try:
            response = await litellm.acompletion(**kwargs)
        except litellm.UnsupportedParamsError:
            kwargs.pop("temperature", None)
            response = await litellm.acompletion(**kwargs)
        tokens = self._record_usage(response)
        text: str = response.choices[0].message.content or ""
        return text, tokens

    async def complete_structured(
        self,
        system: str,
        user: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Call the LLM and return a parsed JSON dict.

        Uses ``response_format={"type": "json_object"}`` when the model
        supports it (OpenAI, Anthropic, most hosted providers). Falls back
        to parsing raw text as JSON for providers that ignore the hint.

        Parameters
        ----------
        system:
            System prompt.
        user:
            User prompt. Should instruct the model to respond with JSON.
        response_schema:
            JSON Schema dict describing the expected response object.
            Used only for documentation/validation here — the actual
            enforcement is up to the model.

        Returns
        -------
        dict
            Parsed JSON response. Returns ``{}`` on parse failure so
            callers can fall back gracefully.
        """
        if self._is_vertex_ai() and self._google_api_key:
            self._check_budget()
            raw_text, _ = await self._vertex_ai_complete(system, user)
            raw = raw_text.strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                raw = "\n".join(ln for ln in lines if not ln.startswith("```"))
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                raw = raw[start : end + 1]
            try:
                return dict(json.loads(raw))
            except json.JSONDecodeError as exc:
                _log.warning("complete_structured (vertex): JSON parse failed: %s", exc)
                return {}

        try:
            import litellm
        except ImportError as exc:
            raise ImportError(
                "litellm is required for LLM enrichment. "
                "Install it with: pip install 'ai-sbom[llm]'"
            ) from exc

        _suppress_litellm_noise(litellm)
        self._check_budget()

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        if not _is_reasoning_model(self._model):
            kwargs["temperature"] = 0.0
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base

        _log.debug(
            "complete_structured model=%s schema_keys=%s",
            self._model,
            list(response_schema.get("properties", {}).keys()),
        )
        try:
            response = await litellm.acompletion(**kwargs)
        except litellm.UnsupportedParamsError:
            kwargs.pop("temperature", None)
            response = await litellm.acompletion(**kwargs)
        self._record_usage(response)

        raw = response.choices[0].message.content or ""
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(ln for ln in lines if not ln.startswith("```"))
        # Find the JSON object boundaries
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start : end + 1]
        try:
            return dict(json.loads(raw))
        except json.JSONDecodeError as exc:
            _log.warning("complete_structured: JSON parse failed: %s", exc)
            return {}


async def enrich_node_descriptions(
    nodes: list,  # list[Node] — avoid circular import
    llm_client: "LLMClient",
) -> None:
    """Use LLM to write descriptions for AGENT/TOOL nodes missing or short ones.

    Mutates nodes in-place. Skips nodes where description is already >30 chars.
    Called from AiSbomExtractor._llm_enrich() after other enrichment steps.
    """
    _SYSTEM = (
        "You are an AI security analyst writing concise descriptions for AI system components."
    )
    for node in nodes:
        component_type = getattr(node, "component_type", None)
        # Accept both enum and string representations
        ct_str = str(getattr(component_type, "value", component_type)) if component_type is not None else ""
        if ct_str not in ("AGENT", "TOOL"):
            continue

        meta = getattr(node, "metadata", None)
        if meta is None:
            continue

        description = getattr(meta, "description", None) or ""
        if len(description) >= 30:
            continue  # already has a sufficient description

        # Stop early if budget is nearly exhausted
        if llm_client.tokens_used >= llm_client._budget * 0.9:
            _log.debug("enrich_node_descriptions: budget threshold reached, stopping early")
            break

        name = getattr(node, "name", "") or ""
        framework = getattr(meta, "framework", None) or "unknown"
        excerpt = (getattr(meta, "system_prompt_excerpt", None) or "")[:300]
        parameters = getattr(meta, "parameters", None) or []
        param_names = ", ".join(p.name for p in parameters if p.name)

        user_prompt = (
            f"Component name: {name}\n"
            f"Type: {ct_str}\n"
            f"Framework: {framework}\n"
            f"System prompt excerpt: {excerpt}\n"
            f"Parameters: {param_names}\n\n"
            "Write a single sentence (15-40 words) describing what this component does. "
            "Reply with ONLY the description text."
        )

        try:
            text, _ = await llm_client.complete_text(system=_SYSTEM, user=user_prompt)
            text = text.strip()
            if text:
                meta.description = text[:200]
                _log.debug("enrich_node_descriptions: set description for %s (%s)", name, ct_str)
        except Exception as exc:
            _log.warning(
                "enrich_node_descriptions: failed for node %s (%s): %s", name, ct_str, exc
            )


async def enrich_descriptive_names(
    nodes: list,  # list[Node] — avoid circular import
    llm_client: "LLMClient",
) -> None:
    """Use LLM to generate a human-readable descriptive_name for every node.

    Batches all nodes into a single structured call to minimise token usage.
    Mutates nodes in-place. Skips nodes that already have descriptive_name set.
    Called from AiSbomExtractor._llm_enrich() as the final enrichment step.
    """
    import json

    targets = [
        n for n in nodes
        if getattr(getattr(n, "metadata", None), "descriptive_name", None) is None
    ]
    if not targets:
        return

    if llm_client.tokens_used >= llm_client._budget * 0.9:
        _log.debug("enrich_descriptive_names: budget threshold reached, skipping")
        return

    _SYSTEM = (
        "You are an AI security analyst generating short, human-readable names for AI system "
        "components. Given a list of components, return a JSON object mapping each component's "
        "id to a descriptive name (3–6 words, title case) that reflects its purpose and type, "
        "e.g. 'User Authentication API', 'PostgreSQL Credentials Store', 'LangGraph Chat Agent'."
    )

    items = []
    for node in targets:
        meta = getattr(node, "metadata", None)
        ct = str(getattr(getattr(node, "component_type", None), "value", "")) or "UNKNOWN"
        name = getattr(node, "name", "") or ""
        desc = (getattr(meta, "description", None) or "")[:100]
        items.append({"id": str(node.id), "type": ct, "name": name, "desc": desc})

    user_prompt = (
        f"Components (JSON list):\n{json.dumps(items, ensure_ascii=False)}\n\n"
        "Return a JSON object: {\"<id>\": \"<Descriptive Name>\", ...} for every component."
    )

    response_schema = {
        "type": "object",
        "additionalProperties": {"type": "string"},
    }

    try:
        result = await llm_client.complete_structured(
            system=_SYSTEM,
            user=user_prompt,
            response_schema=response_schema,
        )
        if not isinstance(result, dict):
            return
        id_map = {str(n.id): n for n in targets}
        for node_id_str, label in result.items():
            if isinstance(label, str) and label.strip() and node_id_str in id_map:
                id_map[node_id_str].metadata.descriptive_name = label.strip()[:120]
        _log.debug("enrich_descriptive_names: assigned names to %d nodes", len(result))
    except Exception as exc:
        _log.warning("enrich_descriptive_names: batch call failed: %s", exc)
