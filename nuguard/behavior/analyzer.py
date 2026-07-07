"""BehaviorAnalyzer — top-level orchestrator for static + dynamic behavior analysis."""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from nuguard.behavior.alignment import check_alignment
from nuguard.behavior.intent import extract_intent
from nuguard.behavior.models import BehaviorAnalysisResult, IntentProfile
from nuguard.behavior.prompt_cache import BehaviorPromptCache
from nuguard.behavior.recommendations import RecommendationEngine
from nuguard.behavior.runner import BehaviorRunner
from nuguard.behavior.scenarios import build_scenarios
from nuguard.common.logging import get_logger
from nuguard.config import BehaviorConfig
from nuguard.models.token_usage import TokenUsage

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.models.policy import CognitivePolicy, PolicyControl
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)


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
        config: BehaviorConfig,
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

        static_findings = [f.model_dump(mode="json") for f in static_findings_objs]

        # Step 3: Dynamic analysis
        dynamic_findings: list[dict] = []
        coverage = []
        scenario_results = []
        skipped_scenario_names: list[str] = []
        _dynamic_run_result = None  # captured for abort/inconclusive propagation
        _dynamic_scan_outcome = None

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

                # ── Judge verdict cache ──────────────────────────────────
                # v3: skip repeat LLM judge calls on warm runs
                judge_cache_dir = getattr(self._config, "judge_cache_dir", "") or ""
                judge_cache = None
                if judge_cache_dir:
                    from nuguard.behavior.judge_cache import JudgeCache
                    judge_cache = JudgeCache(
                        cache_dir=judge_cache_dir,
                        sbom_key=cache_key,
                    )

                # ── Create runner early so we can run pre-scan discovery ─
                # Discovery happens BEFORE scenario generation so the
                # discovered user profile (name + IDs) can be injected into
                # the LLM prompts that generate scenario messages.
                runner = BehaviorRunner(
                    config=self._config,
                    sbom=self._sbom,
                    policy=self._policy,
                    intent=intent,
                    llm_client=self._llm,
                    judge_cache=judge_cache,
                )
                pre_scan_profile = await runner.discover()

                # ── Golden-data fallback ─────────────────────────────────
                # When live discovery returns nothing, build a DiscoveredProfile
                # from config-supplied golden_data so test payloads use real
                # account IDs/names instead of synthetic placeholders.
                # Priority: live discovery > behavior.golden_data > redteam.golden_data
                if pre_scan_profile is None or pre_scan_profile.is_empty:
                    _gd = (
                        getattr(self._config, "golden_data", {}) or {}
                    ) or (
                        getattr(self._config, "redteam_golden_data", {}) or {}
                    )
                    if _gd:
                        from nuguard.common.discovery import (  # noqa: PLC0415
                            profile_from_golden_data,
                        )
                        _config_profile = profile_from_golden_data(_gd)
                        if _config_profile is not None:
                            pre_scan_profile = _config_profile
                            from nuguard.common.console import _console  # noqa: PLC0415
                            _console.print(
                                f"  [bold cyan]Pre-scan discovery (config golden_data):[/bold cyan] "
                                f"name={_config_profile.customer_name!r}  ids={_config_profile.ids}"
                            )

                # ── Scenario cache / build ───────────────────────────────
                # v3: skip LLM generation on warm runs.
                # NOTE: we intentionally bypass the cache when a non-empty
                # profile was discovered so that scenario messages are
                # personalised with real user data on every fresh run.
                scenarios = None
                if pre_scan_profile is None or pre_scan_profile.is_empty:
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
                        skipped_out=skipped_scenario_names,
                        pre_scan_profile=pre_scan_profile,
                    )
                    # Only cache when there is no personalised profile so
                    # the cached scenarios are reusable across sessions.
                    if pre_scan_profile is None or pre_scan_profile.is_empty:
                        scenario_cache.save(cache_key, scenarios)
                _log.info("BehaviorAnalyzer.analyze: %d scenarios to execute", len(scenarios))

                run_result = await runner.run(scenarios, pre_scan_profile=pre_scan_profile)
                _dynamic_run_result = run_result
                _dynamic_scan_outcome = run_result.scan_outcome
                dynamic_findings = run_result.findings
                coverage = run_result.coverage
                scenario_results = run_result.scenario_results

                # FP-2: Downgrade BA-008 HITL static findings to LOW when the
                # corresponding dynamic guardrail probe passed.  A passing probe
                # confirms the agent handles the HITL trigger in its runtime
                # behaviour even though no formal GUARDRAIL node exists in the SBOM.
                passed_scenario_names = {
                    sr.get("scenario_name", "") if isinstance(sr, dict) else getattr(sr, "scenario_name", "")
                    for sr in scenario_results
                    if (
                        (sr.get("verdict") if isinstance(sr, dict) else getattr(sr, "verdict", None))
                        in ("PASS", "pass")
                    )
                }
                hitl_probe_passed = any("hitl" in n.lower() for n in passed_scenario_names)
                if hitl_probe_passed:
                    for sf in static_findings:
                        if sf.get("finding_id", "").startswith("BA-008") and sf.get("severity") == "high":
                            sf["severity"] = "low"
                            sf["description"] = (
                                sf.get("description", "")
                                + " [Downgraded: dynamic HITL probe passed, confirming runtime handling.]"
                            )

        # Step 4: Build analysis result
        result = BehaviorAnalysisResult(
            intent=intent,
            static_findings=static_findings,
            dynamic_findings=dynamic_findings,
            coverage=coverage,
            scenario_results=scenario_results,
            scenarios_skipped=skipped_scenario_names,
        )
        if _dynamic_scan_outcome is not None:
            result.dynamic_scan_outcome = _dynamic_scan_outcome
        if _dynamic_run_result is not None:
            result.gap_aggregation_stats = dict(getattr(_dynamic_run_result, "gap_aggregation_stats", {}) or {})
            result.coverage_mapping_diagnostics = dict(getattr(_dynamic_run_result, "coverage_mapping_diagnostics", {}) or {})
            result.effective_endpoint = str(getattr(_dynamic_run_result, "effective_endpoint", "") or "")
            result.target_endpoint_source = str(getattr(_dynamic_run_result, "target_endpoint_source", "config") or "config")
            result.config_notes = list(getattr(_dynamic_run_result, "config_notes", []) or [])

        # Step 5: Generate recommendations
        result.recommendations = self._rec_engine.generate(result)

        # Step 5a: Build run profile metadata for report comparability.
        scenario_type_counts: dict[str, int] = {}
        total_turns = 0
        coverage_turns = 0
        for sr in scenario_results:
            scenario_type_counts[sr.scenario_type] = scenario_type_counts.get(sr.scenario_type, 0) + 1
            total_turns += int(sr.total_turns or 0)
            coverage_turns += int(sr.coverage_turns or 0)

        target_url = str(getattr(self._config, "target", "") or "")
        payload_key = str(getattr(self._config, "chat_payload_key", "message") or "message")
        endpoint_for_fp = str(
            getattr(_dynamic_run_result, "effective_endpoint", "")
            or getattr(self._config, "target_endpoint", "")
            or "/chat"
        )
        fingerprint_seed = f"{target_url}|{endpoint_for_fp}|{payload_key}"

        try:
            from nuguard import __version__ as nuguard_version
        except Exception:
            nuguard_version = "unknown"

        result.run_profile = {
            "nuguard_version": str(nuguard_version),
            "behavior_engine_version": "v1",
            "scenarios_planned": len(scenario_results) + len(skipped_scenario_names),
            "scenarios_executed": len(scenario_results),
            "scenarios_skipped": len(skipped_scenario_names),
            "scenario_types": scenario_type_counts,
            "total_turns": total_turns,
            "coverage_turns": coverage_turns,
            "max_scenarios_cap": getattr(self._config, "max_scenarios", None),
            "llm_used": bool(self._llm),
            "llm_model": str(getattr(self._config, "llm_model", "") or "") or None,
            "target_fingerprint": hashlib.sha256(fingerprint_seed.encode("utf-8")).hexdigest(),
        }

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
        elif _dynamic_run_result is not None and _dynamic_run_result.scan_outcome in (
            "aborted_target_unavailable",
            "inconclusive_target_errors",
        ):
            # No findings from either phase; propagate target-health outcome from runner
            result.scan_outcome = _dynamic_run_result.scan_outcome
        else:
            result.scan_outcome = "no_findings"

        # Step 7: LLM executive summary (opt-in — only when llm_client is configured)
        if self._llm and all_findings:
            try:
                from nuguard.redteam.llm_engine.summary_generator import LLMSummaryGenerator
                frameworks: list[str] = []
                if self._sbom and self._sbom.summary:
                    frameworks = list(getattr(self._sbom.summary, "frameworks_detected", None) or [])
                summary_gen = LLMSummaryGenerator(self._llm)
                result.llm_executive_summary = await summary_gen.behavior_executive_summary(
                    target_url=getattr(self._config, "target", "") or "",
                    app_purpose=intent.app_purpose,
                    risk_score=result.overall_risk_score,
                    coverage_pct=result.coverage_percentage,
                    alignment_score=result.intent_alignment_score,
                    scenarios_run=len(result.scenario_results),
                    static_findings=static_findings,
                    dynamic_findings=dynamic_findings,
                    frameworks=frameworks,
                )
            except Exception as exc:
                _log.warning("Behavior executive summary generation failed: %s", exc)

        # Aggregate token usage from dynamic runner + analyzer-level LLM calls
        if _dynamic_run_result is not None:
            result.token_usage = result.token_usage + TokenUsage(
                input_tokens=_dynamic_run_result.input_tokens_used,
                output_tokens=_dynamic_run_result.output_tokens_used,
            )
        if self._llm is not None:
            in_, out_ = self._llm.token_counts
            result.token_usage = result.token_usage + TokenUsage(
                input_tokens=in_,
                output_tokens=out_,
                llm_model=getattr(self._llm, "model", None),
            )

        # Step 8: Warn when the LLM was mostly unavailable during this run.
        # Without this, a run that silently degraded to deterministic/structural
        # fallbacks (bad API key, auth failure, rate limits) still produces a
        # report that looks like normal LLM-graded output with no indication
        # that scenario generation, turn adaptation, and judging didn't use the LLM.
        if self._llm is not None:
            total_calls, canned_calls = self._llm.canned_response_counts
            if total_calls >= 5 and canned_calls / total_calls >= 0.3:
                pct = round(100 * canned_calls / total_calls)
                result.config_notes.append(
                    f"LLM degraded during this run: {canned_calls}/{total_calls} LLM calls "
                    f"({pct}%) fell back to canned/template responses. Scenario generation, "
                    "turn adaptation, and judging used deterministic fallbacks — findings and "
                    "PASS/FAIL verdicts in this report may not reflect full LLM-based evaluation."
                )

        _log.info(
            "BehaviorAnalyzer.analyze: complete — outcome=%s, risk=%.1f, coverage=%.0f%%",
            result.scan_outcome,
            result.overall_risk_score,
            result.coverage_percentage * 100,
        )
        return result
