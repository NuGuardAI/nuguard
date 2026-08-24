"""LLM-powered attack payload generator.

Uses the redteam (uncensored) LLM to produce 3–5 diverse, context-rich
attack variants for each scenario, grounded in the SBOM and cognitive policy.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nuguard.common.env_utils import env_float as _env_float
from nuguard.common.env_utils import env_int as _env_int
from nuguard.common.env_utils import env_optional_float as _env_optional_float
from nuguard.common.env_utils import env_optional_int as _env_optional_int
from nuguard.common.llm_client import LLMClient
from nuguard.common.logging import get_logger
from nuguard.models.exploit_chain import ExploitStep
from nuguard.sbom.models import AiSbomDocument

if TYPE_CHECKING:
    from nuguard.common.discovery import DiscoveredProfile
    from nuguard.models.policy import CognitivePolicy
    from nuguard.redteam.scenarios.scenario_types import AttackScenario

from .prompt_builders import (
    _FAMILY_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
    _build_family_prompt,
    _build_user_prompt,
)
from .prompt_cache import PromptCache
from .prompt_parsing import _parse_family_response, _parse_turn_sequences
from .prompt_validation_gate import PromptValidationGate

__all__ = [
    "LLMPromptGenerator",
    # Re-exported for backward-compatible imports (tests, orchestrator.py)
    # after the prompt_builders.py / prompt_parsing.py split.
    "_FAMILY_SYSTEM_PROMPT",
    "_SYSTEM_PROMPT",
    "_build_family_prompt",
    "_build_user_prompt",
    "_parse_family_response",
    "_parse_turn_sequences",
]

_log = get_logger(__name__)

_PROMPT_GENERATION_TEMPERATURE = _env_float("NUGUARD_REDTEAM_PROMPT_GENERATION_TEMPERATURE", 0.7)
_PROMPT_GENERATION_TOP_P = _env_optional_float("NUGUARD_REDTEAM_PROMPT_GENERATION_TOP_P")
_PROMPT_GENERATION_MAX_TOKENS = _env_optional_int("NUGUARD_REDTEAM_PROMPT_GENERATION_MAX_TOKENS")
_PROMPT_GENERATION_VARIANTS_DEFAULT = _env_int("NUGUARD_REDTEAM_PROMPT_GENERATION_VARIANTS", 2)


def _generation_kwargs() -> dict[str, float | int]:
    kwargs: dict[str, float | int] = {
        "temperature": _PROMPT_GENERATION_TEMPERATURE,
    }
    if _PROMPT_GENERATION_TOP_P is not None:
        kwargs["top_p"] = _PROMPT_GENERATION_TOP_P
    if _PROMPT_GENERATION_MAX_TOKENS is not None:
        kwargs["max_tokens"] = _PROMPT_GENERATION_MAX_TOKENS
    return kwargs


class LLMPromptGenerator:
    """Generates diverse attack payloads per scenario using the redteam LLM."""

    def __init__(
        self,
        llm: LLMClient,
        sbom: AiSbomDocument,
        policy: "CognitivePolicy | None",
        n_variants: int | None = None,
        discovered_profile: "DiscoveredProfile | None" = None,
    ) -> None:
        self._llm = llm
        self._sbom = sbom
        self._policy = policy
        self._n_variants = n_variants if n_variants is not None else _PROMPT_GENERATION_VARIANTS_DEFAULT
        self._gate = PromptValidationGate()
        self._discovered_profile = discovered_profile

    # Maximum scenarios per bulk family call. Larger batches produce prompts
    # that exceed model context limits and reliably time out at 120 s.
    _FAMILY_BATCH_SIZE = 10

    async def enrich_family(
        self,
        scenarios: list["AttackScenario"],
    ) -> dict[str, list[list[str]]]:
        """One LLM call per batch of up to _FAMILY_BATCH_SIZE scenarios sharing the same goal_type.

        Splits large families into batches and runs each batch in parallel.
        Falls back to parallel per-scenario calls if any batch fails to parse.
        """
        if not scenarios:
            return {}

        goal = scenarios[0].goal_type.value

        # Split into batches to avoid giant prompts that timeout.
        batches = [
            scenarios[i : i + self._FAMILY_BATCH_SIZE]
            for i in range(0, len(scenarios), self._FAMILY_BATCH_SIZE)
        ]

        async def _try_batch(batch: list["AttackScenario"]) -> dict[str, list[list[str]]]:
            label = f"payload-gen-family | goal={goal} n={len(batch)}"
            prompt = _build_family_prompt(
                batch, self._sbom, self._policy, self._n_variants,
                profile=self._discovered_profile,
            )
            try:
                raw = await asyncio.wait_for(
                    self._llm.complete(
                        prompt,
                        system=_FAMILY_SYSTEM_PROMPT,
                        label=label,
                        **_generation_kwargs(),
                    ),
                    timeout=90.0,
                )
                if not raw.startswith("[NUGUARD_CANNED_RESPONSE]"):
                    parsed = _parse_family_response(raw, batch)
                    if parsed:
                        scenario_by_id = {s.scenario_id: s for s in batch}
                        filtered: dict[str, list[list[str]]] = {}
                        for sid, seqs in parsed.items():
                            scenario = scenario_by_id.get(sid)
                            if scenario is None:
                                continue
                            gated = self._gate.filter_sequences(scenario, seqs)
                            if gated:
                                filtered[sid] = gated
                        if filtered:
                            return filtered
                        _log.info(
                            "payload-gen-family gate rejected all variants | goal=%r n=%d — fallback",
                            goal, len(batch),
                        )
                    _log.info(
                        "payload-gen-family parse failure | goal=%r n=%d — fallback",
                        goal, len(batch),
                    )
            except asyncio.TimeoutError:
                _log.warning("payload-gen-family timeout | goal=%r n=%d (90s)", goal, len(batch))
            except Exception as exc:
                _log.warning("payload-gen-family failed | goal=%r: %s", goal, exc)

            # Fallback: parallel per-scenario calls for this batch
            return await self._enrich_scenarios_parallel(batch)

        _log.debug(
            "Generating LLM attack sequences for %d scenarios in family %r (%d batch(es))",
            len(scenarios), goal, len(batches),
        )
        batch_results = await asyncio.gather(*(_try_batch(b) for b in batches))
        result: dict[str, list[list[str]]] = {}
        for br in batch_results:
            result.update(br)
        return result

    async def _enrich_scenarios_parallel(
        self,
        scenarios: list["AttackScenario"],
    ) -> dict[str, list[list[str]]]:
        """Run per-scenario enrichment calls in parallel and return combined results."""
        results = await asyncio.gather(
            *(self.enrich_scenario(s) for s in scenarios),
            return_exceptions=True,
        )
        out: dict[str, list[list[str]]] = {}
        for s, res in zip(scenarios, results):
            if isinstance(res, list) and res:
                out[s.scenario_id] = res
        return out

    async def enrich_scenario(self, scenario: "AttackScenario") -> list[list[str]]:
        """Return LLM-generated multi-turn attack sequences for the scenario.

        Each element is a list of 2-3 turn strings (SETUP → PROBE → ATTACK).
        """
        prompt = _build_user_prompt(
            scenario, self._sbom, self._policy, self._n_variants,
            profile=self._discovered_profile,
        )
        label = (
            f"payload-gen | scenario={scenario.title!r} "
            f"goal={scenario.goal_type.value} type={scenario.scenario_type.value}"
        )
        _log.debug("Generating %d LLM attack sequences for scenario %r", self._n_variants, scenario.title)
        try:
            raw = await asyncio.wait_for(
                self._llm.complete(
                    prompt,
                    system=_SYSTEM_PROMPT,
                    label=label,
                    **_generation_kwargs(),
                ),
                timeout=60.0,
            )
            if raw.startswith("[NUGUARD_CANNED_RESPONSE]"):
                return []
            sequences = _parse_turn_sequences(raw)
            sequences = self._gate.filter_sequences(scenario, sequences)
            _log.debug(
                "payload-gen done | scenario=%r → %d sequences (%d turns each on avg)",
                scenario.title,
                len(sequences),
                sum(len(s) for s in sequences) // max(len(sequences), 1),
            )
            return sequences[: self._n_variants]
        except asyncio.TimeoutError:
            _log.warning("payload-gen timeout | scenario=%r (60s)", scenario.title)
            return []
        except Exception as exc:
            _log.warning(
                "payload-gen failed | scenario=%r: %s", scenario.title, exc
            )
            return []

    async def enrich_all(
        self,
        scenarios: list["AttackScenario"],
        cache: PromptCache,
        cache_key: str,
        concurrency: int = 5,  # retained for API compat; family calls are sequential
    ) -> dict[str, list[list[str]]]:
        """Return {scenario_id: [[turn1, turn2, turn3], ...]} for all scenarios.

        Loads from cache if available; otherwise groups scenarios by goal_type and
        issues **one LLM call per family** (instead of one per scenario).  Falls back
        to per-scenario calls for any family whose bulk response fails to parse.

        Cache entries are keyed by a deterministic slug (goal|type|title) so they
        survive across runs where scenario UUIDs are regenerated.
        """
        def _slug(s: "AttackScenario") -> str:
            return f"{s.goal_type.value}|{s.scenario_type.value}|{s.title}"

        cached_data = cache.load(cache_key)
        if cached_data is not None:
            result: dict[str, list[list[str]]] = {}
            cached_scenarios = cached_data.get("scenarios", {})
            for s in scenarios:
                entry = cached_scenarios.get(_slug(s)) or cached_scenarios.get(s.scenario_id, {})
                if not isinstance(entry, dict):
                    continue
                sequences = entry.get("turn_sequences", [])
                # Backward compat: old caches store flat "payloads" — wrap each as single-turn
                if not sequences:
                    sequences = [[p] for p in entry.get("payloads", []) if p]
                if sequences:
                    result[s.scenario_id] = sequences
            if result:
                _log.info("Prompt cache hit — loaded LLM sequences for %d/%d scenarios",
                          len(result), len(scenarios))
                return result
            _log.info("Prompt cache miss (no matching scenarios) — regenerating")

        # Group by goal_type → parallel batch calls per family
        families: dict[str, list["AttackScenario"]] = {}
        for s in scenarios:
            families.setdefault(s.goal_type.value, []).append(s)

        _log.info(
            "Generating LLM attack sequences: %d scenarios across %d goal families (parallel)",
            len(scenarios), len(families),
        )

        # Run all families concurrently — each family is independent.
        family_results = await asyncio.gather(
            *(self.enrich_family(fam) for fam in families.values()),
        )
        result = {}
        for fr in family_results:
            result.update(fr)

        cache_scenarios: dict[str, dict] = {}
        for scenario in scenarios:
            sequences = result.get(scenario.scenario_id)
            if sequences:
                cache_scenarios[_slug(scenario)] = {
                    "title": scenario.title,
                    "goal_type": scenario.goal_type.value,
                    "scenario_type": scenario.scenario_type.value,
                    "turn_sequences": sequences,
                }

        if cache_scenarios:
            cache.save(cache_key, cache_scenarios)

        return result


def _inject_llm_payloads(
    scenarios: list["AttackScenario"],
    llm_payloads: dict[str, list[list[str]]],
) -> list["AttackScenario"]:
    """Inject LLM-generated multi-turn sequences into scenario chains.

    For each scenario the first turn-sequence replaces the existing INJECT steps
    with a graduated chain: TURN 1 (innocuous) → TURN 2 (probe) → TURN 3 (attack).
    Additional sequences are appended as variant chains with ``on_failure="mutate"``.

    Steps that precede the primary INJECT step (e.g. warm-up steps added by the
    static builder) are preserved.
    """
    updated: list["AttackScenario"] = []
    for scenario in scenarios:
        sequences = llm_payloads.get(scenario.scenario_id)
        if not sequences or scenario.chain is None:
            updated.append(scenario)
            continue

        scenario = scenario.model_copy(deep=True)
        chain = scenario.chain
        assert chain is not None  # narrowed above

        # Find the LAST primary inject/invoke step — preserve any warm-up steps before it
        primary_idx = next(
            (
                i for i, s in reversed(list(enumerate(chain.steps)))
                if s.step_type in ("INJECT", "INVOKE") and s.payload
            ),
            None,
        )
        if primary_idx is None:
            updated.append(scenario)
            continue

        primary = chain.steps[primary_idx]
        # Keep all steps up to (but not including) the primary attack step
        preceding_steps = chain.steps[:primary_idx]

        # Build new steps from the first turn-sequence
        first_sequence = sequences[0]
        new_steps: list[ExploitStep] = list(preceding_steps)
        _build_turn_steps(new_steps, first_sequence, primary, variant_idx=0)

        # Append additional variant sequences as on_failure="mutate" turns
        for v_idx, sequence in enumerate(sequences[1:], 1):
            _build_turn_steps(new_steps, sequence, primary, variant_idx=v_idx)

        chain.steps = new_steps
        updated.append(scenario)
    return updated


def _build_turn_steps(
    steps: list[ExploitStep],
    turns: list[str],
    template: ExploitStep,
    variant_idx: int,
) -> None:
    """Append 2-3 ExploitStep objects for one LLM turn-sequence into *steps*.

    The last turn carries the attack payload (success_signal, use_llm_eval, etc.
    copied from the template step).  Preceding turns are innocuous and always
    succeed (empty success_signal, on_failure='skip').
    """

    n = len(turns)
    for t_idx, turn_text in enumerate(turns):
        is_last = t_idx == n - 1
        step_id = f"{template.step_id}_v{variant_idx}_t{t_idx + 1}"
        if is_last:
            # Attack turn: carry all signal/eval settings from the template
            steps.append(ExploitStep(
                step_id=step_id,
                step_type=template.step_type,
                description=f"{'LLM variant ' + str(variant_idx) + ': ' if variant_idx else ''}Attack turn — {template.description}",
                payload=turn_text,
                target_node_id=template.target_node_id,
                success_signal=template.success_signal,
                on_failure="mutate" if variant_idx == 0 else "skip",
                use_llm_eval=template.use_llm_eval,
                abort_chain_on_success=template.abort_chain_on_success,
                contributes_to_finding=template.contributes_to_finding,
            ))
        else:
            label = ["Setup", "Probe"][min(t_idx, 1)]
            steps.append(ExploitStep(
                step_id=step_id,
                step_type=template.step_type,
                description=f"{label} turn — {template.description}",
                payload=turn_text,
                target_node_id=template.target_node_id,
                success_signal="",
                on_failure="skip",
            ))
