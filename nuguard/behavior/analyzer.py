"""BehaviorAnalyzer — top-level orchestrator for static + dynamic behavior analysis."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nuguard.behavior.alignment import check_alignment
from nuguard.behavior.intent import extract_intent
from nuguard.behavior.models import BehaviorAnalysisResult, IntentProfile
from nuguard.behavior.prompt_cache import BehaviorPromptCache
from nuguard.behavior.recommendations import RecommendationEngine
from nuguard.behavior.runner import BehaviorRunner
from nuguard.behavior.scenarios import build_scenarios

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.models.policy import CognitivePolicy, PolicyControl
    from nuguard.sbom.models import AiSbomDocument

_log = logging.getLogger(__name__)


class BehaviorAnalyzer:
    """Orchestrates static + dynamic behavior analysis.

    Args:
        config: BehaviorConfig.
        sbom: Optional AI-SBOM document.
        policy: Optional parsed CognitivePolicy.
        controls: Optional compiled PolicyControl list.
        llm_client: Optional LLM client.
    """

    def __init__(
        self,
        config: Any,
        sbom: "AiSbomDocument | None" = None,
        policy: "CognitivePolicy | None" = None,
        controls: "list[PolicyControl] | None" = None,
        llm_client: "LLMClient | None" = None,
    ) -> None:
        self._config = config
        self._sbom = sbom
        self._policy = policy
        self._controls = controls
        self._llm = llm_client
        self._rec_engine = RecommendationEngine()

    async def analyze(
        self,
        mode: str = "static+dynamic",
    ) -> BehaviorAnalysisResult:
        """Run the full behavior analysis pipeline.

        Args:
            mode: One of "static", "dynamic", or "static+dynamic".

        Returns:
            Complete BehaviorAnalysisResult.
        """
        _log.info("BehaviorAnalyzer.analyze: mode=%s", mode)

        # Step 1: Extract intent
        intent = await extract_intent(
            policy=self._policy,
            sbom=self._sbom,
            llm_client=self._llm,
        ) if self._policy is not None else IntentProfile(app_purpose="AI application")

        # Step 2: Static alignment checks
        static_findings_objs = []
        if "static" in mode and self._sbom is not None and self._policy is not None:
            static_findings_objs = check_alignment(self._sbom, intent, self._policy)
            _log.info("BehaviorAnalyzer.analyze: %d static findings", len(static_findings_objs))

        static_findings = [f.model_dump() for f in static_findings_objs]

        # Step 3: Dynamic analysis
        dynamic_findings: list[dict] = []
        coverage = []
        scenario_results = []

        if "dynamic" in mode:
            target_url = getattr(self._config, "target", None) or ""
            if not target_url:
                _log.warning("BehaviorAnalyzer.analyze: no target URL for dynamic mode")
            else:
                # ----------------------------------------------------------------
                # v3: scenario prompt cache — skip LLM generation on warm runs
                # ----------------------------------------------------------------
                prompt_cache_dir = getattr(self._config, "prompt_cache_dir", "") or ""
                scenario_cache = BehaviorPromptCache(cache_dir=prompt_cache_dir or None)
                cache_key = scenario_cache.cache_key(self._sbom, self._policy)

                scenarios = scenario_cache.load(cache_key)
                if scenarios is None:
                    # Build scenarios (LLM layers run in parallel — v3)
                    scenarios = await build_scenarios(
                        config=self._config,
                        intent=intent,
                        policy=self._policy,
                        controls=self._controls,
                        sbom=self._sbom,
                        llm_client=self._llm,
                    )
                    scenario_cache.save(cache_key, scenarios)
                _log.info("BehaviorAnalyzer.analyze: %d scenarios to execute", len(scenarios))

                # ----------------------------------------------------------------
                # v3: judge verdict cache — skip repeat LLM judge calls
                # ----------------------------------------------------------------
                judge_cache_dir = getattr(self._config, "judge_cache_dir", "") or ""
                judge_cache = None
                if judge_cache_dir:
                    from nuguard.behavior.judge_cache import JudgeCache
                    judge_cache = JudgeCache(
                        cache_dir=judge_cache_dir,
                        sbom_key=cache_key,
                    )

                # Run scenarios
                runner = BehaviorRunner(
                    config=self._config,
                    sbom=self._sbom,
                    policy=self._policy,
                    intent=intent,
                    llm_client=self._llm,
                    judge_cache=judge_cache,
                )
                run_result = await runner.run(scenarios)
                dynamic_findings = run_result.findings
                coverage = run_result.coverage
                scenario_results = run_result.scenario_results

        # Step 4: Build analysis result
        result = BehaviorAnalysisResult(
            intent=intent,
            static_findings=static_findings,
            dynamic_findings=dynamic_findings,
            coverage=coverage,
            scenario_results=scenario_results,
        )

        # Step 5: Generate recommendations
        result.recommendations = self._rec_engine.generate(result)

        # Step 5b: Synthesize concrete remediation artefacts in parallel.
        # synthesize_async() properly awaits LLM patch calls; the sync synthesize()
        # silently skips them when called from inside a running event loop.
        from nuguard.behavior.remediation import RemediationSynthesizer

        result.remediation_plan = await RemediationSynthesizer(
            sbom=self._sbom,
            policy=self._policy,
            llm_client=self._llm,
        ).synthesize_async(result)

        # Step 6: Determine outcome
        all_findings = static_findings + dynamic_findings
        has_critical = any(str(f.get("severity", "")).lower() == "critical" for f in all_findings)
        has_high = any(str(f.get("severity", "")).lower() == "high" for f in all_findings)
        if has_critical:
            result.scan_outcome = "critical_findings"
        elif has_high:
            result.scan_outcome = "high_findings"
        elif all_findings:
            result.scan_outcome = "findings"
        else:
            result.scan_outcome = "no_findings"

        _log.info(
            "BehaviorAnalyzer.analyze: complete — outcome=%s, risk=%.1f, coverage=%.0f%%",
            result.scan_outcome,
            result.overall_risk_score,
            result.coverage_percentage * 100,
        )
        return result
