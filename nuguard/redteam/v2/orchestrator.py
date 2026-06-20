"""Top-level v2 runner: surface → plan → schedule → execute → evaluate → report.

Full pipeline (Phases 0–7 complete):

1. **Recon** — resolve chat endpoint + benign user-data extraction.
2. **Target catalog** — capability-gate KB techniques × SBOM × policy; cached.
3. **Attack surface** — SBOM → normalized surface graph with trust-zone tags.
4. **Objectives** — coverage matrix + per-node/per-clause scenario objectives.
5. **Scheduler** — phased, safe, identity-fresh, resource-locked execution.
6. **Execution** — v1 AttackExecutor/GuidedAttackExecutor (thin adapter layer).
7. **Evaluation** — deterministic → semantic multi-judge → side-effect → robustness → transferability.
8. **Findings** — Verdict → Finding with full design fields; regression export.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nuguard.common.logging import get_logger
from nuguard.models.token_usage import TokenUsage

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig
    from nuguard.common.llm_client import LLMClient
    from nuguard.config import RedteamV2Settings
    from nuguard.models.finding import Finding
    from nuguard.models.policy import CognitivePolicy
    from nuguard.redteam.target.canary import CanaryConfig

_log = get_logger(__name__)

# Concurrency ceiling for the phased scheduler.
_DEFAULT_CONCURRENCY = 5


@dataclass
class RedteamV2Result:
    """Aggregate output of a v2 run.

    Mirrors the shape the CLI report path expects so the v2 engine can reuse
    the same Markdown/JSON/SARIF rendering as v1.
    """

    findings: list["Finding"] = field(default_factory=list)
    scenario_records: list[Any] = field(default_factory=list)
    scan_outcome: str = "no_findings"
    config_notes: list[str] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    resolved_chat_path: str = "/chat"
    resolved_chat_path_source: str = "config"


class RedteamV2Orchestrator:
    """Coordinates the full v2 pipeline (surface → plan → execute → evaluate → findings).

    The constructor accepts the same core inputs as the v1
    :class:`~nuguard.redteam.executor.orchestrator.RedteamOrchestrator` so the
    CLI can switch engines with minimal branching.  Extra v1-only keyword
    arguments are accepted and silently discarded via ``**_ignored``.
    """

    def __init__(
        self,
        *,
        sbom: object,
        target_url: str,
        settings: "RedteamV2Settings | None" = None,
        policy: "CognitivePolicy | None" = None,
        policy_controls: list | None = None,
        canary_config: "CanaryConfig | None" = None,
        profile: str = "ci",
        chat_path: str = "",
        auth_config: "AuthConfig | None" = None,
        redteam_llm: "LLMClient | None" = None,
        eval_llm: "LLMClient | None" = None,
        verbose: bool = False,
        **_ignored: Any,
    ) -> None:
        from nuguard.config import RedteamV2Settings as _Settings

        self.sbom = sbom
        self.target_url = target_url
        self.settings = settings or _Settings()
        self.policy = policy
        self.policy_controls = policy_controls or []
        self.canary_config = canary_config
        self.profile = profile
        # Track whether the caller explicitly configured a chat path.
        # When False, recon probes SBOM + live endpoints instead of assuming /chat.
        self._chat_path_explicit = bool(chat_path)
        self.chat_path = chat_path or "/chat"
        self.auth_config = auth_config
        self.redteam_llm = redteam_llm
        self.eval_llm = eval_llm
        self.verbose = verbose

    # ── main entry point ─────────────────────────────────────────────────────────

    async def run(self) -> RedteamV2Result:
        """Execute the full v2 pipeline and return confirmed findings."""
        config_notes: list[str] = []

        # ── 1. Recon: resolve endpoint + optional user-data extraction ────────
        _log.info("v2 pipeline: starting recon")
        recon = await self._run_recon(config_notes)

        # ── 2. Build the per-target catalog (capability-gated, cached) ────────
        _log.info("v2 pipeline: building target catalog")
        catalog, from_cache = self._build_catalog(recon, config_notes)
        if from_cache:
            config_notes.append("target catalog loaded from cache (SBOM+policy hash unchanged)")

        # ── 3. Attack surface + capability profile ────────────────────────────
        _log.info("v2 pipeline: building attack surface")
        surface = self._build_surface()

        # ── 4. Coverage matrix + objectives ──────────────────────────────────
        _log.info("v2 pipeline: generating objectives")
        objectives, coverage = self._generate_objectives(surface, catalog.technique_ids, config_notes)
        if not objectives:
            config_notes.append("no objectives generated — SBOM may have no addressable nodes")
            return RedteamV2Result(
                scan_outcome="no_objectives",
                config_notes=config_notes,
                resolved_chat_path=recon.chat_path,
                resolved_chat_path_source=recon.endpoint_source,
            )

        # ── 5–6. Execute objectives through the phased scheduler ──────────────
        _log.info("v2 pipeline: executing %d objectives", len(objectives))
        scheduled_results = await self._run_scheduler(objectives, surface, recon, config_notes)

        # ── 7. Layered evaluation ─────────────────────────────────────────────
        _log.info("v2 pipeline: evaluating outcomes")
        verdicts, outcomes_by_id = await self._evaluate(scheduled_results, objectives)

        # ── 8. Build findings from confirmed verdicts ─────────────────────────
        _log.info("v2 pipeline: building findings")
        from nuguard.redteam.v2.findings.builder import build_findings

        findings = build_findings(verdicts, objectives, outcomes=outcomes_by_id)

        # ── Assemble result ───────────────────────────────────────────────────
        scenario_records = _build_scenario_records(scheduled_results, verdicts)
        scan_outcome = (
            "critical_findings" if any(
                f.severity.value in ("critical", "high") for f in findings
            )
            else ("findings" if findings else "no_findings")
        )

        _log.info(
            "v2 pipeline complete: %d finding(s), outcome=%s", len(findings), scan_outcome
        )
        return RedteamV2Result(
            findings=findings,
            scenario_records=scenario_records,
            scan_outcome=scan_outcome,
            config_notes=config_notes,
            resolved_chat_path=recon.chat_path,
            resolved_chat_path_source=recon.endpoint_source,
        )

    # ── stage helpers ─────────────────────────────────────────────────────────

    async def _run_recon(self, notes: list[str]) -> Any:
        from nuguard.redteam.v2.surface.recon import (
            ReconResult,
            resolve_chat_endpoint,
            run_recon,
        )
        from nuguard.sbom.models import AiSbomDocument

        auth_headers = self.auth_config.to_headers() if self.auth_config else None
        sbom = self.sbom if isinstance(self.sbom, AiSbomDocument) else None

        if sbom is None:
            # Minimal recon without a typed SBOM.
            notes.append("SBOM is not an AiSbomDocument — recon limited to config defaults")
            return ReconResult(
                chat_path=self.chat_path,
                chat_payload_key="message",
                chat_payload_list=False,
                response_key=None,
                endpoint_source="config",
            )

        # ── Step 1: resolve the chat endpoint (zero-I/O SBOM + live probe) ──
        # Pass an empty path when the user did not explicitly configure one so
        # that resolve_chat_endpoint consults the SBOM and probes live endpoints.
        # Passing "/chat" (the default) short-circuits to source="config" without
        # any discovery, which causes 405s when the real endpoint differs.
        _probe_path = self.chat_path if self._chat_path_explicit else ""
        path, key, is_list, resp_key, source = await resolve_chat_endpoint(
            sbom,
            chat_path=_probe_path,
            target_url=self.target_url,
            auth_headers=auth_headers,
            allow_live_probe=True,
            timeout=self.settings.request_timeout,
        )
        _log.info(
            "recon: endpoint resolved via %r — path=%s key=%s list=%s",
            source, path, key, is_list,
        )

        # ── Step 2: build the client with the resolved endpoint config ────────
        # Building before recon (as before) meant the discovery prompts went to
        # the unresolved /chat path with the wrong payload shape.
        client = self._build_target_client(
            chat_path=path,
            chat_payload_key=key,
            chat_payload_list=is_list,
            chat_response_key=resp_key,
            auth_headers=auth_headers,
            request_timeout=self.settings.request_timeout,
        ) if self.target_url else None

        # ── Step 3: user-data discovery with the correctly-configured client ──
        # Pass the already-resolved path so run_recon skips re-probing.
        recon = await run_recon(
            sbom,
            chat_path=path,
            chat_payload_key=key,
            chat_payload_list=is_list,
            target_url=self.target_url,
            auth_headers=auth_headers,
            allow_live_probe=False,  # already resolved in step 1
            client=client,
            timeout=self.settings.request_timeout,
        )
        # Preserve the source from step 1 (run_recon would mark it "config" since
        # the path is already resolved and non-empty).
        recon.endpoint_source = source

        notes.append(f"chat endpoint resolved via {source!r}: {path}")
        if recon.has_user_data:
            notes.append(
                f"recon extracted {len(recon.user_ids)} user id(s)"
                + (f", name={recon.user_name!r}" if recon.user_name else "")
            )
        return recon

    def _build_catalog(self, recon: Any, notes: list[str]) -> Any:
        from nuguard.redteam.v2.surface.target_catalog import build_target_catalog
        from nuguard.sbom.models import AiSbomDocument

        sbom = self.sbom
        if not isinstance(sbom, AiSbomDocument):
            # Synthesize a minimal empty SBOM so the catalog still builds.
            try:
                from nuguard.sbom.models import AiSbomDocument as _Doc
                sbom = _Doc(nodes=[], edges=[], target="v2-run")
            except Exception:
                pass

        catalog, from_cache = build_target_catalog(
            sbom,  # type: ignore[arg-type]
            policy=self.policy,
            target_url=self.target_url,
            scan_profile=self.profile,
        )
        return catalog, from_cache

    def _build_surface(self) -> Any:
        from nuguard.redteam.v2.surface.attack_surface import AttackSurface
        from nuguard.sbom.models import AiSbomDocument

        sbom = self.sbom
        if not isinstance(sbom, AiSbomDocument):
            try:
                sbom = AiSbomDocument(nodes=[], edges=[], target="v2-run")
            except Exception:
                pass
        return AttackSurface.from_sbom(sbom, policy=self.policy)  # type: ignore[arg-type]

    def _generate_objectives(
        self,
        surface: Any,
        technique_ids: list[str],
        notes: list[str],
    ) -> tuple[list[Any], Any]:
        from nuguard.redteam.v2.planning.objective_generator import generate_objectives

        # Phase filter: if settings.phases is non-empty, only include those phases.
        objectives, coverage = generate_objectives(
            surface,
            policy=self.policy,
            technique_ids=technique_ids or None,
        )

        # Filter by enabled phases if configured.
        enabled_phases = set(self.settings.phases)
        if enabled_phases:
            from nuguard.redteam.v2.scheduler.phases import Phase
            enabled_ints: set[int] = set()
            for name in enabled_phases:
                # Accept phase enum names ("RECON", "WARMUP") or integer strings ("1", "2").
                try:
                    enabled_ints.add(int(name))
                    continue
                except ValueError:
                    pass
                try:
                    enabled_ints.add(int(Phase[name.upper()]))
                except KeyError:
                    pass
            if enabled_ints:
                objectives = [o for o in objectives if o.execution_phase in enabled_ints]
                notes.append(f"phase filter active: {sorted(enabled_ints)} ({len(objectives)} objectives)")

        # Max-per-phase cap.
        if self.settings.max_per_phase > 0:
            from collections import defaultdict
            by_phase: dict[int, list] = defaultdict(list)
            for o in objectives:
                by_phase[o.execution_phase].append(o)
            capped: list[Any] = []
            for ph_objs in by_phase.values():
                capped.extend(ph_objs[: self.settings.max_per_phase])
            objectives = capped

        notes.append(
            f"objectives: {len(objectives)} generated, "
            f"{len(coverage.gaps())} coverage gap(s)"
        )
        return objectives, coverage

    async def _run_scheduler(
        self,
        objectives: list[Any],
        surface: Any,
        recon: Any,
        notes: list[str],
    ) -> list[Any]:
        from nuguard.redteam.v2.execution.runner import KillChainState, ObjectiveRunner
        from nuguard.redteam.v2.scheduler.safety import SafetyPolicy
        from nuguard.redteam.v2.scheduler.scheduler import PhasedScheduler

        auth_headers = self.auth_config.to_headers() if self.auth_config else None
        client = self._build_target_client(
            chat_path=getattr(recon, "chat_path", self.chat_path),
            chat_payload_key=getattr(recon, "chat_payload_key", "message"),
            chat_payload_list=getattr(recon, "chat_payload_list", False),
            chat_response_key=getattr(recon, "response_key", None),
            auth_headers=auth_headers,
            request_timeout=self.settings.request_timeout,
        )
        canary = self._build_canary()
        static_exec = self._build_static_executor(client, canary)
        guided_exec = self._build_guided_executor(client, canary)

        killchain = KillChainState()
        runner = ObjectiveRunner(
            sbom=self.sbom,  # type: ignore[arg-type]
            profile=surface.profile,
            static_executor=static_exec,
            guided_executor=guided_exec,
            client=client,
            policy=self.policy,
            killchain=killchain,
        )

        safety = SafetyPolicy(
            dry_run_only=self.settings.dry_run_only,
            allow_external_egress=False,
        )
        scheduler = PhasedScheduler(
            concurrency=_DEFAULT_CONCURRENCY,
            safety=safety,
            stop_on_critical=True,
            objective_timeout=self.settings.objective_timeout,
        )

        scheduled = await scheduler.run(objectives, runner)
        completed = sum(1 for r in scheduled if r.status == "completed")
        skipped = len(scheduled) - completed
        notes.append(f"scheduler: {completed} executed, {skipped} skipped/errored")
        return scheduled

    async def _evaluate(
        self, scheduled_results: list[Any], objectives: list[Any]
    ) -> tuple[list[Any], dict[str, Any]]:
        from nuguard.redteam.v2.evaluation.pipeline import EvaluationPipeline
        from nuguard.redteam.v2.evaluation.verdict import EvaluationInput

        outcomes_by_id: dict[str, Any] = {}
        eval_inputs: list[EvaluationInput] = []
        obj_by_id = {o.objective_id: o for o in objectives}

        for sr in scheduled_results:
            if sr.result is None:
                continue
            obj = obj_by_id.get(sr.objective.objective_id)
            if obj is None:
                continue
            outcomes_by_id[sr.objective.objective_id] = sr.result
            inp = EvaluationInput.from_outcome(obj, sr.result)
            eval_inputs.append(inp)

        if not eval_inputs:
            return [], outcomes_by_id

        from nuguard.config import RedteamFindingTriggers

        pipeline = EvaluationPipeline(
            llm=self.eval_llm,
            judge_count=self.settings.semantic_judge_count,
            judge_quorum=self.settings.semantic_judge_quorum,
            transferability_enabled=self.settings.transferability_enabled,
            triggers=RedteamFindingTriggers(),
        )
        verdicts = await pipeline.evaluate_all(eval_inputs)
        return verdicts, outcomes_by_id

    # ── v1 infrastructure builders ────────────────────────────────────────────

    def _build_target_client(
        self,
        *,
        chat_path: str = "/chat",
        chat_payload_key: str = "message",
        chat_payload_list: bool = False,
        chat_response_key: str | None = None,
        auth_headers: dict[str, str] | None = None,
        request_timeout: float = 60.0,
    ) -> Any:
        from nuguard.redteam.target.client import TargetAppClient

        return TargetAppClient(
            base_url=self.target_url,
            chat_path=chat_path,
            chat_payload_key=chat_payload_key,
            chat_payload_list=chat_payload_list,
            chat_response_key=chat_response_key,
            default_headers=auth_headers,
            timeout=request_timeout,
        )

    def _build_canary(self) -> Any:
        from nuguard.redteam.target.canary import CanaryConfig, CanaryScanner

        cfg = self.canary_config or CanaryConfig()
        return CanaryScanner(cfg)

    def _build_static_executor(self, client: Any, canary: Any) -> Any:
        from nuguard.redteam.executor.executor import AttackExecutor
        from nuguard.sbom.models import AiSbomDocument

        sbom = self.sbom if isinstance(self.sbom, AiSbomDocument) else None
        return AttackExecutor(
            client=client,
            policy=self.policy,
            canary=canary,
            eval_llm=self.eval_llm,
            mutation_llm=self.redteam_llm,
            sbom=sbom,
        )

    def _build_guided_executor(self, client: Any, canary: Any) -> Any | None:
        if self.redteam_llm is None:
            return None

        from nuguard.models.exploit_chain import GoalType
        from nuguard.redteam.executor.guided_executor import GuidedAttackExecutor
        from nuguard.redteam.llm_engine.conversation_director import ConversationDirector
        from nuguard.sbom.models import AiSbomDocument

        sbom = self.sbom if isinstance(self.sbom, AiSbomDocument) else None
        director = ConversationDirector(
            llm=self.redteam_llm,
            eval_llm=self.eval_llm or self.redteam_llm,
            goal_type=GoalType.PROMPT_DRIVEN_THREAT,  # placeholder — overridden per scenario
            goal_description="",
        )
        return GuidedAttackExecutor(
            client=client,
            director=director,
            canary=canary,
            sbom=sbom,
        )


# ── utilities ─────────────────────────────────────────────────────────────────

def _build_scenario_records(scheduled_results: list[Any], verdicts: list[Any]) -> list[dict]:
    """Thin scenario-record list for the report path (mirrors v1 ScenarioRecord shape)."""
    verdict_map = {v.objective_id: v for v in verdicts}
    records: list[dict] = []
    for sr in scheduled_results:
        oid = sr.objective.objective_id
        verdict = verdict_map.get(oid)
        had_finding = bool(verdict.succeeded if verdict else False)
        # Map scheduler status to the chain_status values expected by report helpers.
        _sched_status = sr.status
        if _sched_status == "completed":
            chain_status = "succeeded" if had_finding else "completed"
        else:
            chain_status = "aborted"
        outcome = sr.result
        step_count = int(getattr(outcome, "step_count", 0) or 0)
        records.append(
            {
                "objective_id": oid,
                "title": sr.objective.title,
                # Expose family under both its native key and the v1 report key
                "family": sr.objective.family,
                "goal_type": sr.objective.family,
                "phase": int(sr.phase),
                "scheduler_status": _sched_status,
                # v1 report-compat fields
                "chain_status": chain_status,
                "had_finding": had_finding,
                "impact_score": 0.0,
                "turns_used": step_count,
                "turns_budget": step_count,
                "duration_s": 0.0,
                "steps": [],
                # Verdict details
                "succeeded": verdict.succeeded if verdict else False,
                "confidence": verdict.confidence.value if verdict else "none",
                "severity": verdict.severity.value if verdict else "info",
            }
        )
    return records
