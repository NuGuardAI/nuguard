"""Direct-HTTP LLM call detection for Go — no SDK import to key off.

Found via the phase-5 ground-truth cross-check in docs/go-support.md:
mosaic-care/healthcare-service's actual primary LLM integration
(``backend/chat/chat.go``) hand-rolls an HTTP client
(``anthropicReq``/``anthropicResp`` structs,
``http.NewRequest(http.MethodPost, "https://api.anthropic.com/v1/messages", ...)``)
with the model name in a plain ``const chatModel = "claude-sonnet-4-6"``.
None of the SDK-import-gated adapters (``anthropic_sdk.py``, ``go_openai.py``,
``google_genai.py``, ``langchaingo.py``) can see this — by definition there's
no matching import — so this is keyed on well-known LLM API hostnames found
in string literals instead of an import gate.

Unlike every other Go adapter in this package, this isn't a
``GoFrameworkAdapter`` (there's no ``handles_imports`` to gate on) — it's a
plain function called unconditionally on every ``.go`` file's parse result,
mirroring ``prompts.py``'s ``extract_go_prompt_constants``.

On a hostname match, this walks the same file's other string literals for a
value matching ``MODEL_NAME_PATTERNS`` — the exact pattern set the
``model_generic`` regex adapter already uses (imported from
``adapters/registry.py``, not duplicated) — and emits a MODEL node anchored
to the *model-string's own* file/line, not wherever ``model_generic``'s
whole-file regex sweep happens to land.
"""

from __future__ import annotations

from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ..registry import MODEL_NAME_PATTERNS

# Substring -> provider. Native provider API hosts first (there's no SDK
# import here to already carry that attribution, unlike the Python/TS
# proxy-pattern tables this intentionally does NOT reuse — those exist to
# resolve an OpenAI-compatible client's base_url override, a different
# problem from "no SDK at all").
_HOST_TO_PROVIDER: tuple[tuple[str, str], ...] = (
    ("api.anthropic.com", "anthropic"),
    ("api.openai.com", "openai"),
    ("generativelanguage.googleapis.com", "google"),
    ("aiplatform.googleapis.com", "google"),
    ("api.mistral.ai", "mistral"),
    ("api.cohere.ai", "cohere"),
    ("api.groq.com", "groq"),
    ("api.together.xyz", "togetherai"),
    ("api.deepseek.com", "deepseek"),
    ("api.fireworks.ai", "fireworks"),
    ("api.perplexity.ai", "perplexity"),
    ("openrouter.ai", "openrouter"),
)


def _resolve_provider(value: str) -> str | None:
    lowered = value.lower()
    for host, provider in _HOST_TO_PROVIDER:
        if host in lowered:
            return provider
    return None


def extract_go_direct_http_llm_calls(parse_result: Any, rel_path: str) -> list[ComponentDetection]:
    """Emit a MODEL node when a known LLM API host and a model-ID string
    both appear as string literals in the same file, with no SDK import.
    """
    literals = parse_result.string_literals
    if not literals:
        return []

    host_hit = next(
        ((lit, provider) for lit in literals if (provider := _resolve_provider(lit.value))),
        None,
    )
    if host_hit is None:
        return []
    _, provider = host_hit

    detections: list[ComponentDetection] = []
    seen: set[str] = set()

    for lit in literals:
        if not any(pattern.search(lit.value) for pattern in MODEL_NAME_PATTERNS):
            continue
        model_name = lit.value.strip()
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)

        detections.append(
            ComponentDetection(
                component_type=ComponentType.MODEL,
                canonical_name=canonicalize_text(model_name.lower()),
                display_name=model_name,
                adapter_name="go_direct_http_llm",
                priority=90,
                confidence=0.85,
                metadata={
                    "provider": provider,
                    "framework": "go_direct_http_llm",
                    "language": "golang",
                },
                file_path=rel_path,
                line=lit.line,
                snippet=model_name,
                evidence_kind="ast_string_literal",
            )
        )

    return detections


__all__ = ["extract_go_direct_http_llm_calls"]
