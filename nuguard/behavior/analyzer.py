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
                # ── Endpoint auto-discovery ─────────────────────────────
                # When target_endpoint is not configured, attempt to infer
                # it from SBOM metadata (zero-I/O) then fall back to a live
                # HTTP probe.  This mirrors the RedteamOrchestrator logic so
                # that both analysis modes share the same discovery path.
                cfg_endpoint = getattr(self._config, "target_endpoint", "") or ""
                if not cfg_endpoint and self._sbom is not None:
                    from nuguard.common.endpoint_probe import (  # noqa: PLC0415
                        discover_chat_config_from_sbom,
                        probe_chat_endpoints,
                    )

                    # 1. Static SBOM-based discovery (no network)
                    disc_path, disc_payload_key, disc_payload_list, disc_response_key = (
                        discover_chat_config_from_sbom(
                            self._sbom,
                            chat_path="",
                            chat_payload_key=getattr(self._config, "chat_payload_key", "message") or "message",
                            chat_payload_list=bool(getattr(self._config, "chat_payload_list", False)),
                        )
                    )

                    if disc_path:
                        _log.info(
                            "BehaviorAnalyzer: SBOM-discovered endpoint %s "
                            "(key=%s list=%s response_key=%s)",
                            disc_path, disc_payload_key, disc_payload_list, disc_response_key,
                        )
                        updates: dict = {"target_endpoint": disc_path}
                        if disc_payload_key and disc_payload_key != getattr(self._config, "chat_payload_key", "message"):
                            updates["chat_payload_key"] = disc_payload_key
                        if disc_payload_list != bool(getattr(self._config, "chat_payload_list", False)):
                            updates["chat_payload_list"] = disc_payload_list
                        if disc_response_key and not getattr(self._config, "chat_response_key", ""):
                            updates["chat_response_key"] = disc_response_key
                        self._config = self._config.model_copy(update=updates)
                    else:
                        # 2. Live HTTP probe fallback
                        auth_headers: dict[str, str] = {}
                        try:
                            from nuguard.common.auth import AuthConfig  # noqa: PLC0415
                            from nuguard.common.auth_runtime import (
                                resolve_auth_runtime,  # noqa: PLC0415
                            )
                            va = getattr(self._config, "auth", None)
                            if va and getattr(va, "type", "none") != "none":
                                ac = AuthConfig(
                                    type=va.type,
                                    header=getattr(va, "header", ""),
                                    username=getattr(va, "username", ""),
                                    password=getattr(va, "password", ""),
                                )
                                rt = resolve_auth_runtime(auth_config=ac)
                                auth_headers = getattr(rt, "initial_headers", {}) or {}
                        except Exception:
                            pass

                        probe_result = await probe_chat_endpoints(
                            target_url=target_url,
                            sbom=self._sbom,
                            auth_headers=auth_headers or None,
                            timeout=15.0,
                        )
                        if probe_result:
                            probed_path, probed_key, probed_list = probe_result
                            _log.info(
                                "BehaviorAnalyzer: live-probed endpoint %s (key=%s list=%s)",
                                probed_path, probed_key, probed_list,
                            )
                            probe_updates: dict = {"target_endpoint": probed_path}
                            if probed_key and probed_key != getattr(self._config, "chat_payload_key", "message"):
                                probe_updates["chat_payload_key"] = probed_key
                            if probed_list != bool(getattr(self._config, "chat_payload_list", False)):
                                probe_updates["chat_payload_list"] = probed_list
                            self._config = self._config.model_copy(update=probe_updates)
                        else:
                            _log.warning(
                                "BehaviorAnalyzer: endpoint auto-discovery found nothing "
                                "for %s — scenarios will use default /chat",
                                target_url,
                            )

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
