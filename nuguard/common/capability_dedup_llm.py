"""LLM-assisted semantic dedup for capability/tool discovery.

:func:`~nuguard.common.discovery.apply_capability_discovery`'s existing dedup
against the SBOM's already-known tool/sub-agent names is a plain
``str.lower()`` set-membership check.  That catches exact (case-insensitive)
name matches but misses naming-convention paraphrases a human would recognize
as the same underlying capability — e.g. ``"send_email"`` vs
``"SendEmailTool"`` vs ``"email_sender"`` — which would otherwise create a
duplicate SBOM node for a tool/agent that's already known.

:func:`llm_dedup_capability_names` is an *additive* second pass: it is only
ever asked about names that already survived the heuristic dedup, and any
failure (no LLM configured, call error, malformed response) falls back to an
empty mapping — i.e. every candidate is treated as genuinely new, exactly the
pre-existing behavior.  It never removes or overrides an already-detected
name; it can only additionally collapse near-duplicates.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient

_log = get_logger(__name__)

_CANNED_RESPONSE_MARKER = "[NUGUARD_CANNED_RESPONSE]"

_SYSTEM_PROMPT = (
    "You compare two lists of tool/agent names extracted from an AI system. "
    "Some names in the 'candidates' list may refer to the exact same "
    "underlying tool or agent as a name already in the 'existing' list, just "
    "spelled or cased differently (e.g. 'send_email' and 'SendEmailTool' are "
    "the same capability). Only report a match when you are confident they "
    "denote the same capability, not merely a similar one. Respond with a "
    "single JSON object mapping each matched candidate name (verbatim) to "
    "the exact existing name (verbatim) it duplicates. Omit any candidate "
    "you are not confident about — do not guess. Respond with JSON only, "
    "no other text."
)


def _build_prompt(candidates: list[str], existing: list[str]) -> str:
    return (
        f"candidates: {json.dumps(candidates)}\n"
        f"existing: {json.dumps(existing)}\n"
    )


def _is_llm_configured(llm: "LLMClient | None") -> bool:
    return llm is not None and getattr(llm, "api_key", None) is not None


def _parse_mapping(raw: str, candidates: list[str], existing: list[str]) -> dict[str, str]:
    """Defensively parse *raw* as a ``{candidate: existing_name}`` JSON mapping.

    Anything that doesn't parse as a JSON object of strings, or whose keys/
    values don't correspond to real input names, is silently dropped — the
    LLM's output is never trusted blindly.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    candidate_set = set(candidates)
    existing_set = set(existing)
    mapping: dict[str, str] = {}
    for key, value in parsed.items():
        if (
            isinstance(key, str)
            and isinstance(value, str)
            and key in candidate_set
            and value in existing_set
        ):
            mapping[key] = value
    return mapping


async def llm_dedup_capability_names(
    candidates: list[str],
    existing: list[str],
    llm: "LLMClient | None",
) -> dict[str, str]:
    """Return ``{candidate: canonical_existing_name}`` for names in
    *candidates* that an LLM judges to denote the same tool/agent as one
    already in *existing*.

    Candidates absent from the returned mapping are unaffected — callers
    should continue treating them as genuinely new, exactly as before this
    function existed. Returns ``{}`` (never raises) when *llm* is ``None``,
    not really configured (no API key — canned-response mode), when there is
    nothing to check, or on any error talking to it / parsing its reply.
    """
    if not candidates or not existing:
        return {}
    if not _is_llm_configured(llm):
        _log.debug("llm_dedup_capability_names: no LLM configured — skipping")
        return {}
    assert llm is not None  # narrowed by _is_llm_configured
    try:
        response = await llm.complete(
            _build_prompt(candidates, existing),
            system=_SYSTEM_PROMPT,
            label="capability_dedup",
        )
    except Exception as exc:  # noqa: BLE001 - additive path, never propagate
        _log.warning("llm_dedup_capability_names: LLM call failed: %s", exc)
        return {}
    if _CANNED_RESPONSE_MARKER in response:
        # Belt-and-suspenders: _is_llm_configured should already have caught
        # the no-API-key case, but never trust a canned template as a real
        # dedup verdict if one slips through some other fallback path.
        _log.debug("llm_dedup_capability_names: got canned response — ignoring")
        return {}
    try:
        return _parse_mapping(response, candidates, existing)
    except Exception as exc:  # noqa: BLE001 - defensive, malformed LLM output
        _log.warning("llm_dedup_capability_names: could not parse LLM response: %s", exc)
        return {}
