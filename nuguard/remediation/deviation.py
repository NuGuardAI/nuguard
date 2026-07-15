"""LLM-authored remediation text for per-turn behavior deviations.

``RemediationSynthesizer`` only ever sees promoted ``Finding`` objects, but the
"Deviation Evidence" section of the behavior report (see
:mod:`nuguard.behavior.report`) renders raw per-turn ``deviations`` dicts
directly — many of these (``intent_misalignment``, ``capability_gap``) never
get promoted into a ``Finding`` at all, or get merged into a bucketed finding
whose description no longer matches the original turn. Those deviations
previously fell back to a purely keyword-bucketed template
(``_deviation_remediation_hint``) that produced identical text for every
finding sharing a bucket, regardless of what the transcript actually showed.

This module enriches deviation dicts in place with an LLM-authored,
turn-evidence-grounded ``remediation`` string. Callers should keep the
deterministic template as a fallback for when no LLM client is configured or
a given call fails.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from nuguard.common.logging import get_logger
from nuguard.remediation.prompts import DEVIATION_REMEDIATION_SYSTEM, DEVIATION_REMEDIATION_USER

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient

_log = get_logger(__name__)

_CANNED_RESPONSE_PREFIX = "[NUGUARD_CANNED_RESPONSE]"
_MAX_UNIQUE_CALLS = 30


async def enrich_deviation_remediations_async(
    scenario_results: list[Any],
    llm_client: "LLMClient | None",
) -> None:
    """Mutate per-turn deviation dicts in place, setting ``remediation`` to
    LLM-authored, transcript-grounded fix text.

    Deduplicates identical ``(deviation_type, description)`` pairs across
    turns — in practice the judge produces a distinct description per
    failure mode, so this collapses repeats without losing specificity — and
    caps the number of distinct LLM calls to bound cost on large runs.
    Leaves ``remediation`` unset (so callers fall back to a template) when
    ``llm_client`` is ``None`` or a call fails/returns a canned response.
    """
    if llm_client is None:
        return

    # (deviation_type, description) -> list of deviation dicts sharing it
    groups: dict[tuple[str, str], list[dict]] = {}
    evidence_by_key: dict[tuple[str, str], dict] = {}
    for sr in scenario_results:
        for v in getattr(sr, "verdicts", None) or []:
            devs = v.get("deviations") or []
            if not devs:
                continue
            for dev in devs:
                key = (str(dev.get("deviation_type", "")), str(dev.get("description", "")))
                groups.setdefault(key, []).append(dev)
                evidence_by_key.setdefault(
                    key,
                    {
                        "gaps": v.get("gaps") or [],
                        "user_message": v.get("user_message") or "",
                        "agent_response": v.get("agent_response") or "",
                    },
                )

    if not groups:
        return

    keys = list(groups.keys())[:_MAX_UNIQUE_CALLS]
    if len(groups) > _MAX_UNIQUE_CALLS:
        _log.debug(
            "enrich_deviation_remediations_async: %d unique deviations, capping LLM calls at %d",
            len(groups),
            _MAX_UNIQUE_CALLS,
        )

    async def _one(key: tuple[str, str]) -> tuple[tuple[str, str], str]:
        deviation_type, description = key
        evidence = evidence_by_key[key]
        text = await _call_llm_async(
            llm_client,
            DEVIATION_REMEDIATION_USER.format(
                deviation_type=deviation_type,
                description=description[:400],
                gaps="; ".join(str(g) for g in evidence["gaps"][:5]) or "none listed",
                user_message=str(evidence["user_message"])[:400],
                agent_response=str(evidence["agent_response"])[:800],
            ),
        )
        return key, text

    results = await asyncio.gather(*(_one(k) for k in keys), return_exceptions=True)
    for outcome in results:
        if isinstance(outcome, BaseException):
            _log.debug("enrich_deviation_remediations_async: one deviation failed: %s", outcome)
            continue
        key, text = outcome
        if not text:
            continue
        for dev in groups[key]:
            dev["remediation"] = text


async def _call_llm_async(llm_client: "LLMClient", prompt: str) -> str:
    try:
        result = ""
        async for chunk in llm_client.complete_stream(
            prompt, system=DEVIATION_REMEDIATION_SYSTEM, label="remediation:deviation"
        ):
            result += chunk
        result = result.strip()
        if result.startswith(_CANNED_RESPONSE_PREFIX):
            return ""
        return result
    except Exception as exc:
        _log.debug("enrich_deviation_remediations_async: LLM call failed: %s", exc)
        return ""
