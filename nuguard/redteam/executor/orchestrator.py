"""RedteamOrchestrator — ties SBOM → scenarios → executor → findings together."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.redteam.target.log_reader import BufferLogReader, FileLogReader

from nuguard.models.exploit_chain import ExploitChain, GoalType
from nuguard.models.finding import Finding
from nuguard.models.policy import CognitivePolicy
from nuguard.sbom.models import NodeType
from nuguard.redteam.risk_engine import (
    compliance_mapper,
    remediation_generator,
    severity_scorer,
)
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.action_logger import ActionLogger
from nuguard.redteam.target.canary import CanaryConfig, CanaryScanner
from nuguard.redteam.target.client import TargetAppClient, TargetUnavailableError
from nuguard.sbom.models import AiSbomDocument

from .executor import AttackExecutor, StepResult
from .guided_executor import GuidedAttackExecutor

_log = logging.getLogger(__name__)


def _normalize_scenario_token(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _scenario_matches_filter(scenario: AttackScenario, filters: set[str]) -> bool:
    if not filters:
        return True
    goal = _normalize_scenario_token(scenario.goal_type.value)
    scenario_type = _normalize_scenario_token(scenario.scenario_type.value)
    title = _normalize_scenario_token(scenario.title)
    return any(token in goal or token in scenario_type or token in title for token in filters)


@dataclass
class ScenarioRecord:
    """Verbose per-scenario execution record for troubleshooting reports."""

    title: str
    goal_type: str
    scenario_type: str
    description: str  # why the scenario was generated (derived from SBOM signals)
    impact_score: float
    affected: str  # resolved "Name (TYPE)" labels for target nodes
    chain_status: str  # completed | aborted | failed
    had_finding: bool
    steps: list[dict] = field(default_factory=list)  # per-step input/output dicts
    # Transport health counters — populated from step results after execution
    http_2xx: int = 0
    http_4xx: int = 0
    http_5xx: int = 0
    request_errors: int = 0
    timeout_errors: int = 0


def _classify_step_transport(response: str, http_status_code: int | None) -> str:
    """Classify a step's transport outcome into one of five categories.

    Returns one of: ``http_2xx``, ``http_4xx``, ``http_5xx``,
    ``timeout_error``, or ``request_error``.

    For steps that used ``invoke_endpoint`` the HTTP status code is available
    directly.  For steps that went through the chat ``send()`` path the status
    is encoded in the response string (``[HTTP NNN]`` or ``[REQUEST_ERROR: ...]``).
    """
    if http_status_code is not None:
        if 200 <= http_status_code < 300:
            return "http_2xx"
        if 400 <= http_status_code < 500:
            return "http_4xx"
        if http_status_code >= 500:
            return "http_5xx"

    if response.startswith("[REQUEST_ERROR:"):
        if "timeout" in response.lower():
            return "timeout_error"
        return "request_error"

    if response.startswith("[HTTP "):
        try:
            code = int(response[6:9])
            if 200 <= code < 300:
                return "http_2xx"
            if 400 <= code < 500:
                return "http_4xx"
            if code >= 500:
                return "http_5xx"
        except (ValueError, IndexError):
            pass

    # Successful chat response with no error prefix
    return "http_2xx"


def _tally_transport(record: ScenarioRecord, step_results: list) -> None:
    """Accumulate transport health counters into *record* from *step_results*."""
    for sr in step_results:
        category = _classify_step_transport(sr.response, sr.http_status_code)
        if category == "http_2xx":
            record.http_2xx += 1
        elif category == "http_4xx":
            record.http_4xx += 1
        elif category == "http_5xx":
            record.http_5xx += 1
        elif category == "timeout_error":
            record.timeout_errors += 1
        else:
            record.request_errors += 1


def _compute_scan_outcome(
    findings: list,
    records: list[ScenarioRecord],
    strict: bool,
) -> str:
    """Derive the scan-level outcome string from findings and scenario records.

    Values
    ------
    ``passed``
        At least one finding was raised — the target has confirmed vulnerabilities.
    ``no_findings``
        Scan ran to completion but no findings were raised.
    ``inconclusive_target_errors``
        The majority of transport events across all scenarios were server-side
        errors (5xx) or network failures.  Only returned when ``strict=True``;
        with the default ``strict=False`` the outcome falls back to ``no_findings``
        so that existing CI pipelines are not disrupted.
    ``aborted_target_unavailable``
        Every executed scenario has ``chain_status == "aborted"`` or ``"skipped"``
        (the circuit breaker tripped), indicating the target was unreachable.
    """
    if findings:
        return "passed"

    # Check for full abort (circuit breaker fired on every scenario)
    if records and all(r.chain_status in ("aborted", "skipped") for r in records):
        return "aborted_target_unavailable"

    if strict and records:
        total_error = sum(
            r.http_5xx + r.request_errors + r.timeout_errors for r in records
        )
        total_all = sum(
            r.http_2xx + r.http_4xx + r.http_5xx + r.request_errors + r.timeout_errors
            for r in records
        )
        # Threshold: ≥ 80 % of transport events are server-side failures
        if total_all > 0 and total_error / total_all >= 0.80:
            return "inconclusive_target_errors"

    return "no_findings"


def _finding_id(title: str) -> str:
    """Return a slug-based finding ID derived from the title."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:80]


def _discover_chat_config(
    sbom: AiSbomDocument,
    chat_path: str,
    chat_payload_key: str,
    chat_payload_list: bool,
) -> tuple[str, str, bool]:
    """Auto-discover chat endpoint config from SBOM API_ENDPOINT nodes.

    Looks for a POST endpoint whose metadata has ``chat_payload_key`` set.
    Falls back to the provided defaults if nothing is found.

    Returns ``(chat_path, chat_payload_key, chat_payload_list)``.
    """
    candidates: list[tuple[int, str, str, bool, str]] = []
    for node in sbom.nodes:
        if node.component_type != NodeType.API_ENDPOINT:
            continue
        meta = node.metadata
        if not meta:
            continue
        if meta.method and meta.method.upper() != "POST":
            continue
        if not meta.chat_payload_key:
            continue

        discovered_path = meta.endpoint or chat_path
        endpoint_l = discovered_path.lower()
        source = (meta.extras or {}).get("source")

        # Prefer high-confidence, source-backed messaging endpoints over
        # synthetic fallback routes such as '/chat/message'.
        score = 0
        if source == "auto_enrichment":
            score -= 2
        elif source == "runtime_probe":
            score -= 1
        else:
            score += 3

        if node.confidence >= 0.9:
            score += 2
        elif node.confidence >= 0.75:
            score += 1

        if "/chat/message" in endpoint_l:
            score += 2
        elif any(token in endpoint_l for token in ("/chat/queue", "/messages", "/message", "/generate", "/completions", "/respond", "/query")):
            score += 3
        elif endpoint_l.endswith("/chat"):
            score += 1
        if endpoint_l.startswith("/api/"):
            score += 1
        if meta.response_text_key:
            score += 1

        candidates.append(
            (score, discovered_path, meta.chat_payload_key, meta.chat_payload_list, node.name)
        )

    if candidates:
        best = max(candidates, key=lambda item: item[0])
        _log.info(
            "SBOM auto-discovered chat config: path=%s key=%s list=%s (node=%s)",
            best[1], best[2], best[3], best[4],
        )
        return best[1], best[2], best[3]

    return chat_path, chat_payload_key, chat_payload_list


class RedteamOrchestrator:
    """Orchestrates a full red-team scan."""

    # Max scenarios running concurrently against the target. Higher values speed
    # up the scan but increase load on the target app.
    DEFAULT_CONCURRENCY = 5

    def __init__(
        self,
        sbom: AiSbomDocument,
        target_url: str,
        policy: CognitivePolicy | None = None,
        canary_config: CanaryConfig | None = None,
        profile: str = "ci",
        min_impact_score: float = 0.0,
        log_path: Path | None = None,
        chat_path: str = "/chat",
        chat_payload_key: str = "message",
        chat_payload_list: bool = False,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_timeout: float = 120.0,
        redteam_llm: "LLMClient | None" = None,
        eval_llm: "LLMClient | None" = None,
        prompt_cache_dir: "Path | None" = None,
        app_log_reader: "FileLogReader | BufferLogReader | None" = None,
        guided_conversations: bool = True,
        guided_max_turns: int = 12,
        guided_concurrency: int = 3,
        extra_headers: dict[str, str] | None = None,
        strict_outcome: bool = False,
        scenario_filter: list[str] | None = None,
    ) -> None:
        self._sbom = sbom
        self._target_url = target_url
        self._policy = policy
        self._canary_config = canary_config
        self._profile = profile
        self._min_impact = min_impact_score
        self._log_path = log_path
        self._request_timeout = request_timeout
        self._concurrency = max(1, concurrency)
        self._redteam_llm = redteam_llm
        self._eval_llm = eval_llm
        self._prompt_cache_dir = prompt_cache_dir
        self._app_log_reader = app_log_reader
        # Guided conversation settings — only active when redteam_llm is configured
        self._guided_conversations = guided_conversations
        self._guided_max_turns = guided_max_turns
        self._guided_concurrency = max(1, guided_concurrency)
        # Default HTTP headers propagated to every request (e.g. auth header)
        self._extra_headers: dict[str, str] = extra_headers or {}
        # Outcome semantics: when True, a scan with predominantly transport errors
        # is reported as inconclusive rather than no_findings.
        self._strict_outcome = strict_outcome
        self._scenario_filter: set[str] = {
            _normalize_scenario_token(s)
            for s in (scenario_filter or [])
            if s and s.strip()
        }
        # Auto-discover from SBOM; fall back to provided values
        self._chat_path, self._chat_payload_key, self._chat_payload_list = (
            _discover_chat_config(sbom, chat_path, chat_payload_key, chat_payload_list)
        )
        # Populated by run() — scenarios executed and their titles
        self.scenarios_run: int = 0
        self.scenarios_executed: list[tuple[str, str, bool]] = []  # (title, goal_type, had_finding)
        # Verbose per-scenario records — populated regardless of whether a finding was raised
        self.scenario_records: list[ScenarioRecord] = []
        # Node lookup: str(id) → "name (TYPE)" — use str() so UUID objects and
        # string IDs both resolve correctly against scenario.target_node_ids
        self._node_label: dict[str, str] = {
            str(node.id): f"{node.name} ({node.component_type.value})"
            for node in sbom.nodes
            if node.id
        }
        # LLM output attributes — populated by run()
        self.llm_executive_summary: str | None = None
        self.llm_remediations: dict[str, str] = {}
        self.llm_coding_brief: str | None = None
        self.prompt_cache_path: Path | None = None
        self.llm_enriched_scenarios: int = 0          # total scenarios that got LLM payloads (pre-filter)
        self.llm_enriched_executed: int = 0           # enriched scenarios that were actually executed
        self.llm_variants_total: int = 0              # total LLM payload variants injected
        self.prompt_cache_hit: bool = False            # True when payloads loaded from cache
        self.llm_scenario_variants: dict[str, int] = {}  # scenario_title → variant_count
        # Scan-level outcome — populated by run()
        # Values: passed | no_findings | inconclusive_target_errors | aborted_target_unavailable
        self.scan_outcome: str = "no_findings"

    async def run(self) -> list[Finding]:
        """Run the full scan and return a list of findings."""
        _log.info(
            "Starting red-team scan against %s (profile=%s)",
            self._target_url,
            self._profile,
        )

        # 1. Generate scenarios from SBOM + policy.
        # Guided conversations require an LLM — only generate when one is configured.
        _with_guided = self._guided_conversations and bool(self._redteam_llm)
        generator = ScenarioGenerator(self._sbom, self._policy)
        all_scenarios = generator.generate(with_guided=_with_guided)

        # 2. LLM payload enrichment (opt-in — only when redteam_llm is configured)
        if self._redteam_llm and all_scenarios:
            from nuguard.redteam.llm_engine.prompt_cache import PromptCache
            from nuguard.redteam.llm_engine.prompt_generator import (
                LLMPromptGenerator,
                _inject_llm_payloads,
            )
            _cache_dir = self._prompt_cache_dir or Path("tests/output")
            _cache = PromptCache(_cache_dir)
            _cache_key = _cache.cache_key(self._sbom, self._policy)
            _cache_existed = _cache.load(_cache_key) is not None
            _llm_payloads = await LLMPromptGenerator(
                self._redteam_llm, self._sbom, self._policy
            ).enrich_all(all_scenarios, _cache, _cache_key)
            self.prompt_cache_path = _cache.path_for(_cache_key)
            self.prompt_cache_hit = _cache_existed and bool(_llm_payloads)
            self.llm_enriched_scenarios = len(_llm_payloads)
            self.llm_variants_total = sum(len(v) for v in _llm_payloads.values())
            # Build title → variant count for report display
            self.llm_scenario_variants = {
                s.title: len(_llm_payloads[s.scenario_id])
                for s in all_scenarios
                if s.scenario_id in _llm_payloads
            }
            all_scenarios = _inject_llm_payloads(all_scenarios, _llm_payloads)
            _log.info(
                "LLM payload enrichment: %d/%d scenarios enriched (%d total variants, cache=%s)",
                self.llm_enriched_scenarios, len(all_scenarios),
                self.llm_variants_total,
                "hit" if self.prompt_cache_hit else "miss",
            )

        # Filter by profile and impact score
        if self._profile == "ci":
            # ci profile: only high-impact scenarios (score >= 5.0)
            scenarios = [
                s
                for s in all_scenarios
                if s.impact_score >= max(self._min_impact, 5.0)
            ]
        else:
            scenarios = [
                s for s in all_scenarios if s.impact_score >= self._min_impact
            ]

        if self._scenario_filter:
            scenarios = [
                s for s in scenarios if _scenario_matches_filter(s, self._scenario_filter)
            ]

        self.scenarios_run = len(scenarios)
        if self.llm_enriched_scenarios:
            self.llm_enriched_executed = sum(
                1 for s in scenarios if s.scenario_id in _llm_payloads  # type: ignore[possibly-undefined]
            )
        _log.info("Running %d scenarios", self.scenarios_run)

        if not scenarios:
            _log.info(
                "No scenarios met the impact threshold — scan complete with 0 findings"
            )
            return []

        # 2. Set up canary scanner
        canary_scanner: CanaryScanner | None = None
        if self._canary_config:
            canary_scanner = CanaryScanner(self._canary_config)

        # 3. Set up action logger
        logger = ActionLogger(self._log_path)

        # 4. Execute scenarios (with PoisonPayloadServer for indirect injection / RAG)
        findings: list[Finding] = []
        from nuguard.redteam.executor.poison_server import (
            POISON_PAYLOAD_HOST,
            PoisonPayloadServer,
        )
        app_name = ""
        if self._sbom.summary:
            app_name = getattr(self._sbom.summary, "application_name", "") or ""
        async with (
            PoisonPayloadServer(app_name=app_name or "application") as poison_server,
            TargetAppClient(
                self._target_url,
                chat_path=self._chat_path,
                chat_payload_key=self._chat_payload_key,
                chat_payload_list=self._chat_payload_list,
                timeout=self._request_timeout,
                default_headers=self._extra_headers or None,
            ) as client,
        ):
            # Substitute poison server URL into all scenario step payloads that
            # contain the placeholder host.  This makes indirect injection and RAG
            # poisoning scenarios point at our live server instead of a dead host.
            poison_netloc = poison_server.netloc
            for scenario in scenarios:
                if scenario.chain is None:
                    continue
                for step in scenario.chain.steps:
                    if POISON_PAYLOAD_HOST in step.payload:
                        step.payload = step.payload.replace(
                            POISON_PAYLOAD_HOST, poison_netloc
                        )

            executor = AttackExecutor(
                client=client,
                policy=self._policy,
                canary=canary_scanner,
                logger=logger,
                eval_llm=self._eval_llm,
                mutation_llm=self._redteam_llm,
                app_log_reader=self._app_log_reader,
            )

            # Build GuidedAttackExecutor when LLM is configured and guided is enabled
            guided_executor: GuidedAttackExecutor | None = None
            if self._redteam_llm and self._guided_conversations:
                from nuguard.redteam.llm_engine.conversation_director import ConversationDirector
                # Resolve target context from SBOM summary for all guided convs
                _target_ctx = self._build_target_context()
                # ConversationDirector is instantiated per-scenario in _run_guided_scenario;
                # the executor just holds the shared client/canary/logger/log_reader.
                guided_executor = GuidedAttackExecutor(
                    client=client,
                    director=ConversationDirector(  # placeholder — overridden per scenario
                        llm=self._redteam_llm,
                        eval_llm=self._eval_llm or self._redteam_llm,
                        goal_type=GoalType.PROMPT_DRIVEN_THREAT,
                        goal_description="",
                        max_turns=self._guided_max_turns,
                        target_context=_target_ctx,
                    ),
                    logger=logger,
                    canary=canary_scanner,
                    app_log_reader=self._app_log_reader,
                )

            findings, executed, records = await self._run_scenarios(scenarios, executor, guided_executor)
            self.scenarios_executed.extend(executed)
            self.scenario_records.extend(records)

            # 5. Escalation pass: if no findings, run lower-scored scenarios that
            #    were filtered out in the CI pass (minimum impact lowered to 3.0)
            if not findings and self._profile == "ci":
                run_ids = {s.scenario_id for s in scenarios}
                escalation_scenarios = [
                    s for s in all_scenarios
                    if s.impact_score >= 3.0 and s.scenario_id not in run_ids
                ][:10]
                if escalation_scenarios:
                    _log.info(
                        "0 findings — escalating with %d lower-scored scenarios",
                        len(escalation_scenarios),
                    )
                    self.scenarios_run += len(escalation_scenarios)
                    findings, escalation_executed, escalation_records = await self._run_scenarios(
                        escalation_scenarios, executor, guided_executor
                    )
                    self.scenarios_executed.extend(escalation_executed)
                    self.scenario_records.extend(escalation_records)

        _log.info("Scan complete: %d findings", len(findings))

        # Compute scan-level outcome from scenario records
        self.scan_outcome = _compute_scan_outcome(
            findings=findings,
            records=self.scenario_records,
            strict=self._strict_outcome,
        )
        _log.info("Scan outcome: %s", self.scan_outcome)

        # LLM evaluation + summary (opt-in — only when eval_llm is configured)
        if self._eval_llm and findings:
            from nuguard.redteam.llm_engine.summary_generator import LLMSummaryGenerator
            frameworks: list[str] = []
            if self._sbom.summary:
                frameworks = list(getattr(self._sbom.summary, "frameworks_detected", None) or [])
            node_by_id: dict[str, object] = {
                str(node.id): node for node in self._sbom.nodes if node.id
            }
            summary_gen = LLMSummaryGenerator(self._eval_llm)
            self.llm_executive_summary = await summary_gen.executive_summary(
                target_url=self._target_url,
                scenarios_run=self.scenarios_run,
                findings=findings,
                frameworks=frameworks,
                duration_s=0.0,  # duration not tracked here; report layer has it
            )
            for f in findings:
                rem = await summary_gen.remediation(f, node_by_id)
                if rem:
                    self.llm_remediations[f.finding_id] = rem
            if self.llm_remediations:
                self.llm_coding_brief = await summary_gen.coding_agent_brief(
                    findings, self.llm_remediations
                )

        return findings

    async def _run_scenarios(
        self,
        scenarios: list[AttackScenario],
        executor: AttackExecutor,
        guided_executor: "GuidedAttackExecutor | None" = None,
    ) -> tuple[list[Finding], list[tuple[str, str, bool]], list[ScenarioRecord]]:
        """Execute scenarios concurrently and return (findings, executed_records, scenario_records).

        Scenarios are independent of each other — each gets its own session —
        so they can safely run in parallel.  A semaphore caps the number of
        in-flight scenarios to avoid overwhelming the target app.

        If the target endpoint returns too many consecutive errors the circuit
        breaker in ``TargetAppClient`` raises ``TargetUnavailableError``.  We
        catch it, set an abort event, and skip all scenarios that have not yet
        acquired the semaphore.
        """
        sem = asyncio.Semaphore(self._concurrency)
        abort_event = asyncio.Event()

        async def _run_one(
            scenario: AttackScenario,
        ) -> tuple[list[Finding], tuple[str, str, bool], ScenarioRecord]:
            affected = ", ".join(
                self._node_label.get(nid, nid) for nid in scenario.target_node_ids[:2]
            )

            def _skipped_record(status: str) -> ScenarioRecord:
                return ScenarioRecord(
                    title=scenario.title,
                    goal_type=scenario.goal_type.value,
                    scenario_type=scenario.scenario_type.value,
                    description=scenario.description,
                    impact_score=scenario.impact_score,
                    affected=affected,
                    chain_status=status,
                    had_finding=False,
                )

            # Skip immediately if the circuit is already open
            if abort_event.is_set():
                return [], (scenario.title, scenario.goal_type.value, False), _skipped_record("skipped")

            async with sem:
                # Re-check after acquiring the semaphore — another coroutine may
                # have tripped the circuit while we were waiting.
                if abort_event.is_set():
                    return [], (scenario.title, scenario.goal_type.value, False), _skipped_record("skipped")

                try:
                    # Route: guided conversation vs. static chain
                    if scenario.guided_conversation is not None and guided_executor is not None:
                        return await self._run_guided_scenario(
                            scenario, guided_executor, affected
                        )

                    if scenario.chain is None:
                        return [], (scenario.title, scenario.goal_type.value, False), _skipped_record("failed")
                    chain, step_results = await executor.run(scenario.chain)
                    step_details = self._build_step_details(step_results)
                    new_findings = self._build_findings(
                        scenario, chain, step_results, step_details
                    )
                    had_finding = bool(new_findings)
                    record = ScenarioRecord(
                        title=scenario.title,
                        goal_type=scenario.goal_type.value,
                        scenario_type=scenario.scenario_type.value,
                        description=scenario.description,
                        impact_score=scenario.impact_score,
                        affected=affected,
                        chain_status=chain.status,
                        had_finding=had_finding,
                        steps=step_details,
                    )
                    _tally_transport(record, step_results)
                    return (
                        new_findings,
                        (scenario.title, scenario.goal_type.value, had_finding),
                        record,
                    )
                except TargetUnavailableError as exc:
                    _log.error(
                        "Target endpoint unavailable — aborting remaining scenarios. %s", exc
                    )
                    abort_event.set()
                    return [], (scenario.title, scenario.goal_type.value, False), _skipped_record("aborted")
                except Exception as exc:
                    _log.warning("Scenario %s failed: %s", scenario.scenario_id, exc)
                    record = ScenarioRecord(
                        title=scenario.title,
                        goal_type=scenario.goal_type.value,
                        scenario_type=scenario.scenario_type.value,
                        description=scenario.description,
                        impact_score=scenario.impact_score,
                        affected=affected,
                        chain_status="failed",
                        had_finding=False,
                        steps=[],
                    )
                    return [], (scenario.title, scenario.goal_type.value, False), record

        active = [s for s in scenarios if s.chain is not None or s.guided_conversation is not None]
        if not active:
            return [], [], []

        _log.info(
            "Running %d scenarios (concurrency=%d)", len(active), self._concurrency
        )
        results = await asyncio.gather(*(_run_one(s) for s in active))

        findings: list[Finding] = []
        executed: list[tuple[str, str, bool]] = []
        records: list[ScenarioRecord] = []
        for new_findings, exec_tuple, record in results:
            findings.extend(new_findings)
            executed.append(exec_tuple)
            records.append(record)
        return findings, executed, records

    def _build_target_context(self) -> str:
        """Build a one-paragraph context string about the target for the ConversationDirector."""
        parts: list[str] = []
        if self._sbom.summary:
            s = self._sbom.summary
            if getattr(s, "application_name", None):
                parts.append(f"Application: {s.application_name}")
            if getattr(s, "use_case", None):
                parts.append(f"Purpose: {s.use_case[:120]}")
            if getattr(s, "frameworks_detected", None):
                parts.append(f"Frameworks: {', '.join(list(s.frameworks_detected)[:4])}")
        agents = [n for n in self._sbom.nodes if n.component_type == NodeType.AGENT]
        if agents:
            names = ", ".join(n.name for n in agents[:4])
            parts.append(f"Agents: {names}")
        tools = [n for n in self._sbom.nodes if n.component_type == NodeType.TOOL]
        if tools:
            names = ", ".join(n.name for n in tools[:4])
            parts.append(f"Tools: {names}")
        datastores = [n for n in self._sbom.nodes if n.component_type == NodeType.DATASTORE]
        if datastores:
            names = ", ".join(n.name for n in datastores[:3])
            parts.append(f"Datastores: {names}")
        return ". ".join(parts)

    async def _run_guided_scenario(
        self,
        scenario: AttackScenario,
        guided_executor: GuidedAttackExecutor,
        affected: str,
    ) -> tuple[list[Finding], tuple[str, str, bool], ScenarioRecord]:
        """Execute a guided conversation scenario and convert to findings + record."""
        from nuguard.redteam.llm_engine.conversation_director import ConversationDirector
        from nuguard.redteam.target.session import AttackSession

        conv = scenario.guided_conversation
        assert conv is not None  # guard — caller already checked

        # Override the director with a scenario-specific one so goal / context are correct
        target_context = self._build_target_context()
        director = ConversationDirector(
            llm=self._redteam_llm,  # type: ignore[arg-type]
            eval_llm=self._eval_llm or self._redteam_llm,  # type: ignore[arg-type]
            goal_type=conv.goal_type,
            goal_description=conv.goal_description,
            max_turns=conv.max_turns,
            target_context=target_context,
        )
        guided_executor._director = director  # swap in scenario-specific director

        session = AttackSession(
            session_id=conv.conversation_id,
            target_url=self._target_url,
            chain_id=conv.conversation_id,
        )
        populated_conv = await guided_executor.run(conv, session)

        had_finding = populated_conv.succeeded
        finding_list = self._conv_to_finding(scenario, populated_conv, affected)
        chain_status = (
            "succeeded" if populated_conv.succeeded
            else f"aborted:{populated_conv.abort_reason}" if populated_conv.abort_reason
            else "completed"
        )

        # Build turn-level step details for the verbose report
        step_details = [
            {
                "step_type": "GUIDED_TURN",
                "description": f"Turn {t.turn} [{t.tactic_used}]",
                "succeeded": t.progress_score >= director.SUCCESS_THRESHOLD,
                "payload": t.attacker_message[:500],
                "response": t.agent_response[:500],
                "progress_score": t.progress_score,
                "progress_reasoning": t.progress_reasoning,
                "tactic_used": t.tactic_used,
            }
            for t in populated_conv.turns
        ]

        record = ScenarioRecord(
            title=scenario.title,
            goal_type=scenario.goal_type.value,
            scenario_type=scenario.scenario_type.value,
            description=scenario.description,
            impact_score=scenario.impact_score,
            affected=affected,
            chain_status=chain_status,
            had_finding=had_finding,
            steps=step_details,
        )
        # Tally transport health from guided turn responses
        for turn_detail in step_details:
            resp = turn_detail.get("response", "")
            category = _classify_step_transport(resp, None)
            if category == "http_2xx":
                record.http_2xx += 1
            elif category == "http_4xx":
                record.http_4xx += 1
            elif category == "http_5xx":
                record.http_5xx += 1
            elif category == "timeout_error":
                record.timeout_errors += 1
            else:
                record.request_errors += 1
        return (
            finding_list,
            (scenario.title, scenario.goal_type.value, had_finding),
            record,
        )

    def _conv_to_finding(
        self,
        scenario: AttackScenario,
        conv: "object",  # GuidedConversation — avoid circular import at module level
        affected: str,
    ) -> "list[Finding]":
        """Convert a completed GuidedConversation into Finding objects."""
        from nuguard.redteam.models.guided_conversation import GuidedConversation
        assert isinstance(conv, GuidedConversation)

        if not conv.succeeded:
            return []

        sev = severity_scorer.score_finding(conv.goal_type)
        transcript = conv.format_transcript(max_chars_per_turn=300)
        finding_id = _finding_id(f"guided-{scenario.title}")
        sbom_path_descriptions = [
            self._node_label.get(nid, nid) for nid in conv.sbom_path
        ]

        # Build attack_steps from turn records for the JSON report
        attack_steps = [
            {
                "turn": t.turn,
                "tactic": t.tactic_used,
                "attacker_message": t.attacker_message[:400],
                "agent_response": t.agent_response[:400],
                "progress_score": t.progress_score,
                "progress_reasoning": t.progress_reasoning,
                "milestone_reached": t.milestone_reached,
            }
            for t in conv.turns
        ]

        return [
            Finding(
                finding_id=finding_id,
                title=f"Guided: {scenario.title}",
                severity=sev,
                description=(
                    f"Guided adversarial conversation achieved the goal: "
                    f"{conv.goal_description}  "
                    f"Completed in {len(conv.turns)} turns "
                    f"(final progress={conv.final_progress:.2f})."
                ),
                affected_component=affected,
                remediation=remediation_generator.generate(conv.goal_type, scenario.title),
                goal_type=conv.goal_type.value,
                chain_id=f"guided-{conv.conversation_id}",
                sbom_path=conv.sbom_path,
                sbom_path_descriptions=sbom_path_descriptions,
                owasp_asi_ref=conv.owasp_asi_ref or compliance_mapper.owasp_asi_ref(conv.goal_type),
                owasp_llm_ref=conv.owasp_llm_ref or compliance_mapper.owasp_llm_ref(conv.goal_type),
                mitre_atlas_technique=conv.mitre_atlas_technique,
                evidence=transcript,
                attack_steps=attack_steps,
            )
        ]

    def _build_step_details(self, step_results: list[StepResult]) -> list[dict]:
        """Build a list of per-step detail dicts from executor results.

        Each dict contains the step input (payload or HTTP request) and output
        (response, status code, tool calls) plus a success flag.
        """
        details: list[dict] = []
        for sr in step_results:
            step = sr.step
            detail: dict = {
                "step_type": step.step_type,
                "description": step.description,
                "succeeded": sr.success_signal_found,
            }
            if step.target_path:
                detail["method"] = step.http_method
                detail["target_path"] = step.target_path
                if step.http_body:
                    detail["request_body"] = step.http_body
                if step.http_params:
                    detail["params"] = step.http_params
                if sr.http_status_code is not None:
                    detail["status_code"] = sr.http_status_code
            else:
                detail["payload"] = step.payload
            if sr.response:
                raw = sr.response
                detail["response"] = raw[:2000] + (" …[truncated]" if len(raw) > 2000 else "")
            else:
                detail["response"] = ""
            if sr.tool_calls:
                detail["tool_calls"] = [
                    tc.get("name", tc.get("type", str(tc))) for tc in sr.tool_calls
                ]
            if sr.llm_eval_evidence:
                detail["llm_eval_evidence"] = sr.llm_eval_evidence
                detail["llm_eval_confidence"] = sr.llm_eval_confidence
            details.append(detail)
        return details

    @staticmethod
    def _step_evidence_summary(step_details: list[dict]) -> str:
        """One-line summary of attack steps and their responses for evidence fields."""
        parts = []
        for i, step in enumerate(step_details, 1):
            stype = step.get("step_type", "?")
            ok = "✅" if step.get("succeeded") else "❌"
            target = step.get("target_path")
            method = step.get("method", "POST")
            resp = (step.get("response") or "").strip()
            prefix = f"{method} {target}" if target else ""
            if resp:
                snippet = resp[:120].replace("\n", " ")
                if len(resp) > 120:
                    snippet += "…"
                label = f"{prefix}: {snippet!r}" if prefix else snippet[:120 + len(prefix)]
                parts.append(f"Step {i} ({stype} {ok}): {label}")
            else:
                status = step.get("status_code")
                status_str = f" → HTTP {status}" if status is not None else " → no response"
                parts.append(f"Step {i} ({stype} {ok}): {prefix}{status_str}" if prefix else f"Step {i} ({stype} {ok}){status_str}")
        return "; ".join(parts) if parts else "no steps executed"

    def _build_findings(
        self,
        scenario: AttackScenario,
        chain: ExploitChain,
        step_results: list[StepResult],
        step_details: list[dict],
    ) -> list[Finding]:
        """Convert scenario execution results into Finding objects."""
        findings: list[Finding] = []

        all_violations = []
        canary_hits: list[str] = []
        for sr in step_results:
            all_violations.extend(sr.policy_violations)
            canary_hits.extend(sr.canary_hits)

        # Resolve human-readable component labels for this scenario's target nodes
        affected = ", ".join(
            self._node_label.get(nid, nid) for nid in scenario.target_node_ids[:2]
        )
        # Build human-readable sbom_path_descriptions from the chain's node IDs
        sbom_path_descriptions = [
            self._node_label.get(nid, nid) for nid in chain.sbom_path
        ]

        # Canary-based finding
        if canary_hits:
            sev = severity_scorer.score_finding(
                scenario.goal_type,
                canary_verbatim=True,
                phi_exposed=scenario.goal_type == GoalType.DATA_EXFILTRATION,
            )
            canary_title = f"Canary Value Exfiltrated — {scenario.title}"
            findings.append(
                Finding(
                    finding_id=_finding_id(canary_title),
                    title=canary_title,
                    severity=sev,
                    description=(
                        f"Canary value(s) {canary_hits[:3]} were found in the agent response, "
                        f"confirming data exfiltration via scenario: {scenario.title}"
                    ),
                    affected_component=affected,
                    remediation=remediation_generator.generate(
                        scenario.goal_type, scenario.title
                    ),
                    goal_type=scenario.goal_type,
                    chain_id=chain.chain_id,
                    sbom_path=chain.sbom_path,
                    sbom_path_descriptions=sbom_path_descriptions,
                    policy_clauses_violated=chain.policy_clauses,
                    owasp_asi_ref=compliance_mapper.owasp_asi_ref(scenario.goal_type),
                    owasp_llm_ref=compliance_mapper.owasp_llm_ref(scenario.goal_type),
                    evidence=(
                        f"Canary values found: {canary_hits} | "
                        f"Attack steps: {self._step_evidence_summary(step_details)}"
                    ),
                    attack_steps=step_details,
                )
            )

        # Policy violation findings
        step_summary = self._step_evidence_summary(step_details)
        for violation in all_violations:
            sev = severity_scorer.score_finding(scenario.goal_type)
            violation_title = f"{violation.type.replace('_', ' ').title()} — {scenario.title}"
            evidence = (
                f"{violation.evidence} | Attack steps: {step_summary}"
            )
            findings.append(
                Finding(
                    finding_id=_finding_id(violation_title),
                    title=violation_title,
                    severity=sev,
                    description=violation.evidence,
                    affected_component=affected,
                    remediation=remediation_generator.generate(
                        scenario.goal_type,
                        scenario.title,
                        violation_type=violation.type,
                    ),
                    goal_type=scenario.goal_type,
                    chain_id=chain.chain_id,
                    sbom_path=chain.sbom_path,
                    sbom_path_descriptions=sbom_path_descriptions,
                    policy_clauses_violated=[violation.policy_clause],
                    owasp_asi_ref=compliance_mapper.owasp_asi_ref(scenario.goal_type),
                    owasp_llm_ref=compliance_mapper.owasp_llm_ref(scenario.goal_type),
                    evidence=evidence,
                    attack_steps=step_details,
                )
            )

        # If no violations or canary but the scan ran, check for goal-specific signals
        if not findings:
            # Check whether success signals were hit in high-confidence steps
            critical_hits = [
                sr
                for sr in step_results
                if sr.success_signal_found
                and sr.step.step_type in ("INJECT", "INVOKE")
                and sr.step.on_failure == "abort"
            ]
            if critical_hits:
                sev = severity_scorer.score_finding(scenario.goal_type)
                step_summary = self._step_evidence_summary(step_details)
                findings.append(
                    Finding(
                        finding_id=_finding_id(scenario.title),
                        title=scenario.title,
                        severity=sev,
                        description=(
                            f"Attack scenario '{scenario.title}' succeeded: "
                            f"success signals detected in {len(critical_hits)} step(s)."
                        ),
                        affected_component=affected,
                        remediation=remediation_generator.generate(
                            scenario.goal_type, scenario.title
                        ),
                        goal_type=scenario.goal_type,
                        chain_id=chain.chain_id,
                        sbom_path=chain.sbom_path,
                        sbom_path_descriptions=sbom_path_descriptions,
                        owasp_asi_ref=compliance_mapper.owasp_asi_ref(scenario.goal_type),
                        owasp_llm_ref=compliance_mapper.owasp_llm_ref(scenario.goal_type),
                        evidence=f"Attack steps: {step_summary}",
                        attack_steps=step_details,
                    )
                )

        return findings
