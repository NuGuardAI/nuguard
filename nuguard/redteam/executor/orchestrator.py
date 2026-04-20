"""RedteamOrchestrator — ties SBOM → scenarios → executor → findings together."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig
    from nuguard.common.llm_client import LLMClient
    from nuguard.config import RedteamFindingTriggers
    from nuguard.redteam.target.log_reader import BufferLogReader, FileLogReader

from nuguard.common.console import print_turn as _common_print_turn
from nuguard.models.exploit_chain import ExploitChain, GoalType
from nuguard.models.finding import Finding
from nuguard.models.policy import CognitivePolicy
from nuguard.redteam.policy_engine.evaluator import PolicyViolation
from nuguard.redteam.risk_engine import (
    compliance_mapper,
    remediation_generator,
    severity_scorer,
)
from nuguard.redteam.scenarios.generator import ScenarioGenerator
from nuguard.redteam.scenarios.scenario_types import AttackScenario
from nuguard.redteam.target.action_logger import ActionLogger
from nuguard.redteam.target.canary import CanaryConfig, CanaryScanner
from nuguard.redteam.target.client import TargetUnavailableError
from nuguard.sbom.models import AiSbomDocument, NodeType

from .executor import AttackExecutor, StepResult
from .guided_executor import GuidedAttackExecutor

_log = logging.getLogger(__name__)


def _normalize_scenario_token(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _dedup_findings(findings: list[Finding]) -> list[Finding]:
    """Collapse near-duplicate findings, keeping the highest-severity instance.

    Two findings are considered duplicates when they share the same
    ``(goal_type, affected_component, success_indicator)`` triple.  The one
    with the higher severity (lower index in the Severity enum order) wins;
    ties are broken by taking the one with the longer evidence string.
    """
    from nuguard.models.finding import Severity

    _SEV_ORDER = [
        Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO
    ]

    def _sev_rank(f: Finding) -> int:
        try:
            return _SEV_ORDER.index(f.severity)
        except ValueError:
            return len(_SEV_ORDER)

    seen: dict[tuple, Finding] = {}
    for f in findings:
        key = (
            f.goal_type or "",
            (f.affected_component or "").lower(),
            f.success_indicator or "",
        )
        if key not in seen:
            seen[key] = f
        else:
            existing = seen[key]
            # Prefer higher severity; on tie, prefer longer evidence
            if _sev_rank(f) < _sev_rank(existing):
                seen[key] = f
            elif _sev_rank(f) == _sev_rank(existing):
                ev_len = len(f.evidence or "")
                ex_len = len(existing.evidence or "")
                if ev_len > ex_len:
                    seen[key] = f

    result = list(seen.values())
    removed = len(findings) - len(result)
    if removed:
        _log.info("Finding dedup: collapsed %d duplicate(s) → %d findings", removed, len(result))
    return result


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
    # Timing and turn counts — populated after execution
    duration_s: float = 0.0      # wall-clock seconds for the full scenario
    turns_used: int = 0          # turns/steps actually executed
    turns_budget: int = 0        # max turns/steps available (from scenario definition)


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


def _dedup_scenarios_by_opener(scenarios: list[AttackScenario]) -> list[AttackScenario]:
    """Discard scenarios with duplicate opener fingerprints before any HTTP calls are made.

    v4 Layer 6: multiple scenario builders sometimes emit structurally identical
    scenarios targeting the same agent with the same first message.  Sending
    them all wastes HTTP calls and makes run logs unreadable.

    Fingerprint logic:
    - Guided conversations: ``(goal_type + sbom_path + goal_description[:100])``
    - Static chains: ``(goal_type + scenario_type + target_node_ids + steps[0].payload[:100])``

    The target node IDs and goal/type are included for static chains so that
    scenarios that share a generic warmup turn (e.g. all "Restricted Topic Probe"
    chains open with the same rapport message) are only deduplicated when they
    truly address the same agent *and* the same attack class — not across different
    topics or different agents.

    The first occurrence (highest impact_score after pre-sort) is kept; duplicates
    are dropped with an INFO log.
    """
    seen: set[str] = set()
    deduped: list[AttackScenario] = []
    dropped = 0
    for scenario in scenarios:
        if scenario.guided_conversation is not None:
            conv = scenario.guided_conversation
            raw = (
                conv.goal_type.value
                + "|".join(sorted(conv.sbom_path))
                + conv.goal_description[:100]
            )
        elif scenario.chain is not None and scenario.chain.steps:
            # Include goal_type, scenario_type, title, and target node IDs as the
            # fingerprint for static chains.  Using the title (which encodes the
            # specific topic/action being tested) prevents scenarios for different
            # restricted topics or HITL triggers from being collapsed together even
            # when they share an identical generic warmup opener.  The last-step
            # payload is appended to catch truly duplicate attack payloads that
            # happen to have different titles due to generator bugs.
            targets = "|".join(sorted(scenario.target_node_ids))
            last_payload = scenario.chain.steps[-1].payload[:80] if scenario.chain.steps else ""
            raw = (
                scenario.goal_type.value
                + scenario.scenario_type.value
                + targets
                + scenario.title[:80]
                + last_payload
            )
        else:
            deduped.append(scenario)
            continue
        key = hashlib.md5(raw.encode()).hexdigest()  # noqa: S324 — not security-sensitive
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        deduped.append(scenario)
    if dropped:
        _log.info("Scenario dedup: dropped %d duplicate opener(s) → %d remaining", dropped, len(deduped))
    return deduped


def _print_redteam_turn(
    scenario_title: str,
    turn_idx: int,
    url: str,
    request: str,
    response: str,
    *,
    succeeded: bool,
    goal_type: str,
    tactic: str | None = None,
    http_status: int | None = None,
    step_type: str | None = None,
) -> None:
    """Print a single redteam turn's request/response to the console."""
    if step_type == "WARMUP":
        outcome_colour = "dim"
        outcome_label = "warmup"
    else:
        outcome_colour = "green" if succeeded else "red"
        outcome_label = "HIT" if succeeded else "miss"
    result_lines: list[str] = []
    status_str = f"  HTTP {http_status}" if http_status else ""
    tactic_str = f"  tactic={tactic}" if tactic else ""
    result_lines.append(
        f"  [dim]goal:[/dim] {goal_type}"
        f"{tactic_str}{status_str}"
        f"  result=[{outcome_colour}]{outcome_label}[/{outcome_colour}]"
    )
    _common_print_turn(
        module="redteam",
        scenario_name=scenario_title,
        turn_idx=turn_idx,
        url=url,
        request=request,
        response=response,
        result_lines=result_lines,
    )


def _discover_chat_config(
    sbom: AiSbomDocument,
    chat_path: str,
    chat_payload_key: str,
    chat_payload_list: bool,
) -> tuple[str, str, bool, str | None]:
    """Auto-discover chat endpoint config from SBOM API_ENDPOINT node metadata.

    Looks for a POST endpoint whose metadata has ``chat_payload_key`` set.
    Falls back to framework-convention inference (e.g. LangGraph ``phrases``/
    list semantics) when the node lacks an explicit payload key.  Falls back
    to the provided defaults if nothing is found.

    Returns ``(chat_path, chat_payload_key, chat_payload_list, response_text_key)``.
    ``response_text_key`` is ``None`` when not determinable from the SBOM.
    """
    # Detect which AI frameworks are present in the SBOM.
    summary = getattr(sbom, "summary", None)
    sbom_frameworks: set[str] = set()
    if summary is not None:
        raw = getattr(summary, "frameworks", None)
        if isinstance(raw, (list, tuple)):
            sbom_frameworks = {str(f).lower() for f in raw if f}
    has_langgraph = bool(sbom_frameworks & {"langgraph"})

    candidates: list[tuple[int, str, str, bool, str, str | None]] = []
    for node in sbom.nodes:
        if node.component_type != NodeType.API_ENDPOINT:
            continue
        meta = node.metadata
        if not meta:
            continue
        if meta.method and meta.method.upper() != "POST":
            continue

        discovered_path = meta.endpoint or chat_path
        endpoint_l = discovered_path.lower()
        source = (meta.extras or {}).get("source")

        # ── Resolve payload key ────────────────────────────────────────────
        inferred_response_key: str | None = meta.response_text_key or None
        if meta.chat_payload_key:
            payload_key = meta.chat_payload_key
            payload_list = bool(meta.chat_payload_list)
        elif has_langgraph and any(
            tok in endpoint_l
            for tok in ("/run_langgraph", "/run_graph", "/langgraph/run")
        ):
            # LangGraph convention: POST {"phrases": ["..."]} → response["prognosis"]
            payload_key = "phrases"
            payload_list = True
            if inferred_response_key is None:
                inferred_response_key = "prognosis"
        else:
            # No payload info and no matching framework convention — skip this node.
            continue

        # ── Scoring ────────────────────────────────────────────────────────
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

        # LangGraph run endpoint is always the primary agent interface.
        if "run_langgraph" in endpoint_l or "run_graph" in endpoint_l:
            score += 3

        if endpoint_l.startswith("/api/"):
            score += 1
        if inferred_response_key:
            score += 1

        # Penalise nodes that had no explicit payload key (inferred).
        if not meta.chat_payload_key:
            score -= 1

        # Penalise nodes confirmed dead by the live probe — GET 404 means the
        # route doesn't exist at all on the deployed target; POST 405 strongly
        # suggests the path is handled by a different mechanism (e.g. static file
        # serving on Azure SWA, not the API backend).
        extras = meta.extras or {}
        if extras.get("probe_get_404"):
            score -= 8
        if extras.get("probe_post_405"):
            score -= 6

        candidates.append(
            (score, discovered_path, payload_key, payload_list, node.name, inferred_response_key)
        )

    if candidates:
        best = max(candidates, key=lambda item: item[0])
        _log.info(
            "SBOM auto-discovered chat config: path=%s key=%s list=%s response_key=%s (node=%s)",
            best[1], best[2], best[3], best[5], best[4],
        )
        return best[1], best[2], best[3], best[5]

    return chat_path, chat_payload_key, chat_payload_list, None


def _enrich_policy_from_controls(
    policy: "CognitivePolicy", controls: list
) -> "CognitivePolicy":
    """Return a copy of *policy* with restricted_topics / restricted_actions
    extended by the boundary_prompt text from compiled controls.

    This ensures the ScenarioGenerator uses richer, control-specific prompts
    rather than falling back to generic templates.
    """
    extra_topics = [
        p
        for c in controls
        if c.control_type == "topic_restriction"
        for p in (c.boundary_prompts or [])
    ]
    extra_actions = [
        p
        for c in controls
        if c.control_type == "action_restriction"
        for p in (c.boundary_prompts or [])
    ]
    return policy.model_copy(
        update={
            "restricted_topics": list(policy.restricted_topics) + extra_topics,
            "restricted_actions": list(policy.restricted_actions) + extra_actions,
        }
    )


def _policy_from_controls(controls: list) -> "CognitivePolicy":
    """Build a minimal CognitivePolicy from compiled controls when no .md policy exists."""
    restricted_topics = [
        c.description for c in controls if c.control_type == "topic_restriction"
    ]
    restricted_actions = [
        c.description for c in controls if c.control_type == "action_restriction"
    ]
    hitl_triggers = [
        c.description for c in controls if c.control_type == "hitl"
    ]
    return CognitivePolicy(
        restricted_topics=restricted_topics,
        restricted_actions=restricted_actions,
        hitl_triggers=hitl_triggers,
    )


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
        policy_controls: list | None = None,
        canary_config: CanaryConfig | None = None,
        profile: str = "ci",
        min_impact_score: float = 0.0,
        log_path: Path | None = None,
        chat_path: str = "/chat",
        chat_payload_key: str = "message",
        chat_payload_list: bool = False,
        chat_response_key: str | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_timeout: float = 120.0,
        redteam_llm: "LLMClient | None" = None,
        eval_llm: "LLMClient | None" = None,
        prompt_cache_dir: "Path | None" = None,
        app_log_reader: "FileLogReader | BufferLogReader | None" = None,
        guided_conversations: bool = True,
        guided_max_turns: int = 12,
        guided_concurrency: int = 3,
        guided_mutation_mode: str = "hard",
        tree_breadth: int = 0,
        tree_max_depth: int = 0,
        extra_headers: dict[str, str] | None = None,
        strict_outcome: bool = False,
        scenario_filter: list[str] | None = None,
        auth_config: "AuthConfig | None" = None,
        finding_triggers: "RedteamFindingTriggers | None" = None,
        verbose: bool = False,
        credentials: dict[str, str] | None = None,
    ) -> None:
        self._sbom = sbom
        self._target_url = target_url
        self._policy = policy
        self._policy_controls = policy_controls  # compiled PolicyControl list
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
        mode = guided_mutation_mode if guided_mutation_mode in {"soft", "hard"} else "hard"
        self._guided_mutation_mode: Literal["soft", "hard"] = cast(
            Literal["soft", "hard"], mode
        )
        # TAP (tree of attacks) config — auto-resolves from profile when 0
        self._tree_breadth = tree_breadth
        self._tree_max_depth = tree_max_depth
        # Structured auth config — when provided, takes precedence over extra_headers
        self._auth_config = auth_config
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
        self._finding_triggers = finding_triggers
        self._verbose = verbose
        self._credentials: dict[str, str] = credentials or {}
        # Auto-discover from SBOM; fall back to provided values
        self._chat_path, self._chat_payload_key, self._chat_payload_list, _discovered_response_key = (
            _discover_chat_config(sbom, chat_path, chat_payload_key, chat_payload_list)
        )
        # Prefer explicit caller-supplied response key; fall back to SBOM-discovered one.
        self._chat_response_key = chat_response_key or _discovered_response_key
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

    def _trigger_enabled(self, name: str) -> bool:
        """Return whether a finding trigger is enabled (defaults preserve legacy behavior)."""
        defaults = {
            "canary_hits": True,
            "policy_violations": True,
            "critical_success_hits": True,
            "any_inject_success": False,
        }
        if self._finding_triggers is None:
            return defaults.get(name, False)
        return bool(getattr(self._finding_triggers, name, defaults.get(name, False)))

    def _publish_scenarios(self, scenarios: list[AttackScenario]) -> None:
        """Emit per-scenario details to the log so the planned test surface is auditable.

        Runs after profile / impact / name filtering so the published list exactly
        matches what the orchestrator will execute.  Emits:
          - A one-line breakdown by execution mode (guided vs static) and scenario type
          - One INFO line per scenario with title, mode, goal, targets, impact, turn budget
        Keeps each line compact enough to scan in `agentic-test-*.log` tailing output.
        """
        if not scenarios:
            return

        # Mode / type breakdown
        guided_count = sum(1 for s in scenarios if s.guided_conversation is not None)
        static_count = len(scenarios) - guided_count
        type_counts: dict[str, int] = {}
        for s in scenarios:
            key = s.scenario_type.value
            type_counts[key] = type_counts.get(key, 0) + 1
        type_summary = ", ".join(
            f"{k}={v}" for k, v in sorted(type_counts.items(), key=lambda kv: -kv[1])
        )
        _log.info(
            "Published %d scenarios (guided=%d, static=%d) | %s",
            len(scenarios), guided_count, static_count, type_summary,
        )

        for idx, scenario in enumerate(scenarios, start=1):
            targets = ", ".join(
                self._node_label.get(nid, nid)
                for nid in scenario.target_node_ids[:3]
            ) or "-"
            if len(scenario.target_node_ids) > 3:
                targets += f" (+{len(scenario.target_node_ids) - 3})"

            if scenario.guided_conversation is not None:
                mode = "guided"
                budget = f"{scenario.guided_conversation.max_turns}t"
            elif scenario.chain is not None:
                mode = "static"
                budget = f"{len(scenario.chain.steps)}s"
            else:
                mode = "noop"
                budget = "-"

            _log.info(
                "  [%02d] %s | mode=%s | goal=%s | type=%s | targets=%s | impact=%.1f | budget=%s",
                idx,
                scenario.title,
                mode,
                scenario.goal_type.value,
                scenario.scenario_type.value,
                targets,
                scenario.impact_score,
                budget,
            )

    async def run(self) -> list[Finding]:
        """Run the full scan and return a list of findings."""
        _log.info(
            "Starting red-team scan against %s (profile=%s)",
            self._target_url,
            self._profile,
        )

        # 0. Endpoint auto-discovery — when chat_path was not explicitly configured,
        #    probe SBOM POST endpoints live to find one that accepts chat requests.
        if not self._chat_path:
            await self._maybe_probe_endpoints()

        # 0b. Auth bootstrap — resolve effective auth and verify every credential
        #    before running any scenario. Raises TargetUnavailableError on network
        #    failure; raises AuthError when the default credential is rejected.
        import uuid as _uuid

        from nuguard.common.auth_runtime import bootstrap_auth_runtime, resolve_auth_runtime
        from nuguard.common.errors import AuthError

        auth_runtime = resolve_auth_runtime(
            auth_config=self._auth_config,
            headers_override=self._extra_headers if self._auth_config is None else None,
        )
        bootstrapper, health_report = await bootstrap_auth_runtime(
            target_url=self._target_url,
            endpoint=self._chat_path,
            auth_config=auth_runtime.auth_config,
            canary_config=self._canary_config,
            run_id=str(_uuid.uuid4()),
        )
        self.health_report = health_report
        for line in health_report.summary_lines():
            _log.info("bootstrap %s", line)
        # Abort on default credential auth failure — scenarios would produce false negatives
        default_check = health_report.checks[0] if health_report.checks else None
        if default_check and default_check.status == "auth_failed":
            raise AuthError(
                f"Auth failed for identity '{default_check.identity}' "
                f"(HTTP {default_check.http_status_code}): {default_check.error_detail}",
                status_code=default_check.http_status_code or 0,
                identity=default_check.identity,
                detail=default_check.error_detail,
            )
        bootstrap_headers = bootstrapper.session.headers()
        effective_headers = dict(self._extra_headers)
        # Login-flow/bootstrapped session headers must override static defaults.
        if bootstrap_headers:
            effective_headers.update(bootstrap_headers)

        # 1. Generate scenarios from SBOM + policy.
        # When compiled controls are available, enrich the policy with their
        # boundary_prompts so the scenario generator uses the richer, LLM-crafted
        # (or rule-based) prompts instead of generic templates.
        effective_policy = self._policy
        if self._policy_controls and effective_policy is not None:
            effective_policy = _enrich_policy_from_controls(
                effective_policy, self._policy_controls
            )
        elif self._policy_controls and effective_policy is None:
            effective_policy = _policy_from_controls(self._policy_controls)

        # Guided conversations require an LLM — only generate when one is configured.
        _with_guided = self._guided_conversations and bool(self._redteam_llm)
        generator = ScenarioGenerator(self._sbom, effective_policy)
        all_scenarios = generator.generate(with_guided=_with_guided)

        # 2. Filter by profile and impact score (before enrichment — avoids wasting LLM calls)
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

        # 3. LLM payload enrichment (opt-in — only enrich scenarios that will run)
        _llm_payloads: dict = {}
        if self._redteam_llm and scenarios:
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
            ).enrich_all(scenarios, _cache, _cache_key)
            self.prompt_cache_path = _cache.path_for(_cache_key)
            self.prompt_cache_hit = _cache_existed and bool(_llm_payloads)
            self.llm_enriched_scenarios = len(_llm_payloads)
            self.llm_variants_total = sum(len(v) for v in _llm_payloads.values())
            # Build title → variant count for report display
            self.llm_scenario_variants = {
                s.title: len(_llm_payloads[s.scenario_id])
                for s in scenarios
                if s.scenario_id in _llm_payloads
            }
            scenarios = _inject_llm_payloads(scenarios, _llm_payloads)
            _log.info(
                "LLM payload enrichment: %d/%d scenarios enriched (%d total variants, cache=%s)",
                self.llm_enriched_scenarios, len(scenarios),
                self.llm_variants_total,
                "hit" if self.prompt_cache_hit else "miss",
            )

        # v4 Layer 6: deduplicate scenarios with identical openers before any HTTP calls.
        # This avoids sending structurally identical first messages to the same agent
        # (a common artifact when multiple builders target the same node with the same goal).
        scenarios = _dedup_scenarios_by_opener(scenarios)

        self.scenarios_run = len(scenarios)
        if self.llm_enriched_scenarios:
            self.llm_enriched_executed = sum(
                1 for s in scenarios if s.scenario_id in _llm_payloads
            )
        _log.info("Running %d scenarios", self.scenarios_run)
        self._publish_scenarios(scenarios)

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

        from nuguard.common.target_client_builder import build_target_app_client

        client = build_target_app_client(
            target_url=self._target_url,
            endpoint=self._chat_path,
            payload_key=self._chat_payload_key,
            payload_list=self._chat_payload_list,
            payload_format="json",
            response_key=self._chat_response_key,
            timeout=self._request_timeout,
            auth_headers=effective_headers or None,
            sbom=self._sbom,
            adk_cfg=None,
            # chat_path was already resolved by _discover_chat_config in __init__,
            # so treat endpoint/payload as explicitly set to skip re-discovery.
            explicitly_set=frozenset({"target_endpoint", "chat_payload_key", "chat_response_key"}),
        )

        async with (
            PoisonPayloadServer(app_name=app_name or "application") as poison_server,
            client,
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

            _warmup_app_domain, _warmup_allowed_topics = self._build_happy_path_context()
            executor = AttackExecutor(
                client=client,
                policy=self._policy,
                canary=canary_scanner,
                logger=logger,
                eval_llm=self._eval_llm,
                mutation_llm=self._redteam_llm,
                app_log_reader=self._app_log_reader,
                auth_session=bootstrapper.session,
                app_domain=_warmup_app_domain,
                allowed_topics=_warmup_allowed_topics,
            )

            # Build GuidedAttackExecutor when LLM is configured and guided is enabled
            guided_executor: GuidedAttackExecutor | None = None
            if self._redteam_llm and self._guided_conversations:
                from nuguard.redteam.llm_engine.conversation_director import ConversationDirector
                # Resolve target context from SBOM summary for all guided convs
                _target_ctx = self._build_target_context()
                # ConversationDirector is instantiated per-scenario in _run_guided_scenario;
                # the executor just holds the shared client/canary/logger/log_reader.
                # Resolve TAP breadth/depth from profile when not explicitly set
                _is_full = self._profile == "full"
                _tap_breadth = self._tree_breadth or (3 if _is_full else 2)
                _tap_depth = self._tree_max_depth or (3 if _is_full else 2)
                # Build evaluator for TAP branch scoring
                from nuguard.redteam.llm_engine.response_evaluator import (
                    LLMResponseEvaluator,  # noqa: PLC0415
                )
                _tap_evaluator = LLMResponseEvaluator(
                    self._eval_llm or self._redteam_llm  # type: ignore[arg-type]
                )
                guided_executor = GuidedAttackExecutor(
                    client=client,
                    director=ConversationDirector(  # placeholder — overridden per scenario
                        llm=self._redteam_llm,
                        eval_llm=self._eval_llm or self._redteam_llm,
                        goal_type=GoalType.PROMPT_DRIVEN_THREAT,
                        goal_description="",
                        max_turns=self._guided_max_turns,
                        target_context=_target_ctx,
                        mutation_mode=self._guided_mutation_mode,
                    ),
                    logger=logger,
                    canary=canary_scanner,
                    app_log_reader=self._app_log_reader,
                    credentials=self._credentials or None,
                    sbom=self._sbom,
                    tree_breadth=_tap_breadth,
                    tree_max_depth=_tap_depth,
                    evaluator=_tap_evaluator,
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

        findings = _dedup_findings(findings)
        _log.info("Scan complete: %d findings (after dedup)", len(findings))

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
            self.llm_remediations = await summary_gen.remediation_batch(findings, node_by_id)
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
            scenario_idx: int = 0,
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
                    _t0 = time.perf_counter()
                    # Route: guided conversation vs. static chain
                    if scenario.guided_conversation is not None and guided_executor is not None:
                        new_findings, exec_tuple, record = await self._run_guided_scenario(
                            scenario, guided_executor, affected, variation_idx=scenario_idx
                        )
                        record.duration_s = time.perf_counter() - _t0
                        record.turns_used = len(record.steps)
                        record.turns_budget = scenario.guided_conversation.max_turns
                        return new_findings, exec_tuple, record

                    if scenario.chain is None:
                        return [], (scenario.title, scenario.goal_type.value, False), _skipped_record("failed")
                    chain, step_results = await executor.run(scenario.chain)
                    if self._verbose:
                        target_url = self._target_url + self._chat_path
                        for step_idx, sr in enumerate(step_results, 1):
                            request_text = (
                                sr.step.payload
                                if not sr.step.target_path
                                else f"{sr.step.http_method or 'POST'} {sr.step.target_path}"
                            )
                            _print_redteam_turn(
                                scenario_title=scenario.title,
                                turn_idx=step_idx,
                                url=target_url,
                                request=request_text,
                                response=sr.response,
                                succeeded=sr.success_signal_found,
                                goal_type=scenario.goal_type.value,
                                http_status=sr.http_status_code,
                                step_type=sr.step.step_type,
                            )
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
                        duration_s=time.perf_counter() - _t0,
                        turns_used=len(step_details),
                        turns_budget=len(scenario.chain.steps),
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
        results = await asyncio.gather(*(_run_one(s, idx) for idx, s in enumerate(active)))

        findings: list[Finding] = []
        executed: list[tuple[str, str, bool]] = []
        records: list[ScenarioRecord] = []
        for new_findings, exec_tuple, record in results:
            findings.extend(new_findings)
            executed.append(exec_tuple)
            records.append(record)
        return findings, executed, records

    def _build_happy_path_context(self) -> tuple[str, list[str]]:
        """Return ``(app_domain, allowed_topics)`` for the turn-1 happy-path opener.

        ``app_domain`` is a short human-readable phrase describing what the app
        does, built from SBOM summary (application name + use case).  It is
        used only for the benign first message of a guided conversation and
        deliberately omits adversarial context.

        ``allowed_topics`` comes straight from the cognitive policy when
        available so the opener stays strictly within declared scope.
        """
        app_name = ""
        use_case = ""
        if self._sbom.summary:
            app_name = (getattr(self._sbom.summary, "application_name", "") or "").strip()
            use_case = (getattr(self._sbom.summary, "use_case", "") or "").strip()
        if app_name and use_case:
            app_domain = f"{app_name} — {use_case[:160]}"
        else:
            app_domain = app_name or use_case[:160]
        allowed_topics: list[str] = []
        if self._policy:
            allowed_topics = [t for t in (self._policy.allowed_topics or []) if t]
        return app_domain, allowed_topics

    def _build_target_context(self) -> str:
        """Build a one-paragraph context string about the target for the ConversationDirector."""
        parts: list[str] = []
        if self._sbom.summary:
            s = self._sbom.summary
            if getattr(s, "application_name", None):
                parts.append(f"Application: {getattr(s, 'application_name')}")
            if getattr(s, "use_case", None):
                parts.append(f"Purpose: {s.use_case[:120]}")
            if getattr(s, "frameworks_detected", None):
                parts.append(f"Frameworks: {', '.join(list(getattr(s, 'frameworks_detected'))[:4])}")
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
        variation_idx: int = 0,
    ) -> tuple[list[Finding], tuple[str, str, bool], ScenarioRecord]:
        """Execute a guided conversation scenario and convert to findings + record."""
        from nuguard.redteam.llm_engine.conversation_director import ConversationDirector
        from nuguard.redteam.target.session import AttackSession

        conv = scenario.guided_conversation
        assert conv is not None  # guard — caller already checked

        # Clamp per-scenario max_turns to the orchestrator-wide cap from config
        # (redteam.guided_max_turns).  Each scenario builder bakes in its own
        # preferred cap (8-12) — this lets operators shrink them globally
        # without touching each builder, while still honouring builders that
        # chose a lower value.
        if self._guided_max_turns and conv.max_turns > self._guided_max_turns:
            _log.debug(
                "[guided] clamping conv.max_turns %d → %d (scenario=%s)",
                conv.max_turns, self._guided_max_turns, scenario.title,
            )
            conv.max_turns = self._guided_max_turns

        # Override the director with a scenario-specific one so goal / context are correct
        target_context = self._build_target_context()
        app_domain, allowed_topics = self._build_happy_path_context()
        director = ConversationDirector(
            llm=self._redteam_llm,  # type: ignore[arg-type]
            eval_llm=self._eval_llm or self._redteam_llm,  # type: ignore[arg-type]
            goal_type=conv.goal_type,
            goal_description=conv.goal_description,
            max_turns=conv.max_turns,
            target_context=target_context,
            mutation_mode=self._guided_mutation_mode,
            app_domain=app_domain,
            allowed_topics=allowed_topics,
            variation_idx=variation_idx,
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
                "succeeded": t.progress_score >= director.SUCCESS_SCORE,
                "payload": t.attacker_message[:500],
                "response": t.agent_response[:500],
                "progress_score": t.progress_score,
                "reasoning": t.reasoning or t.progress_reasoning,
                "evidence_quote": t.evidence_quote,
                "success_indicator": t.success_indicator,
                "failure_classification": t.failure_classification,
                "tactic_used": t.tactic_used,
                "handled_by_agent_id": t.handled_by_agent_id,
                "tools_used_ids": t.tools_used_ids,
                "handoff_path": t.handoff_path,
                "attribution_source": t.attribution_source,
            }
            for t in populated_conv.turns
        ]

        if self._verbose:
            target_url = self._target_url + self._chat_path
            for t in populated_conv.turns:
                _print_redteam_turn(
                    scenario_title=scenario.title,
                    turn_idx=t.turn,
                    url=target_url,
                    request=t.attacker_message,
                    response=t.agent_response,
                    succeeded=t.progress_score >= director.SUCCESS_SCORE,
                    goal_type=scenario.goal_type.value,
                    tactic=t.tactic_used,
                )

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
            resp = str(turn_detail.get("response", ""))
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

        # Guided success findings align with critical success signal behavior.
        if not self._trigger_enabled("critical_success_hits"):
            return []

        sev = severity_scorer.score_finding(conv.goal_type)
        # Evidence centres on the *breakthrough* turn (the one where the goal
        # was achieved) rather than starting at Turn 1 — which is the warmup
        # turn and carries no exploit signal.  Downstream Markdown truncation
        # therefore lands on the actual exploit proof first.
        transcript = conv.format_evidence(
            breakthrough_chars=1200,
            context_chars=220,
        )
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
                "reasoning": t.reasoning or t.progress_reasoning,
                "evidence_quote": t.evidence_quote,
                "success_indicator": t.success_indicator,
                "failure_classification": t.failure_classification,
                "milestone_reached": t.milestone_reached,
                "handled_by_agent_id": t.handled_by_agent_id,
                "tools_used_ids": t.tools_used_ids,
                "handoff_path": t.handoff_path,
                "attribution_source": t.attribution_source,
            }
            for t in conv.turns
        ]

        # Use observed attribution from breakthrough turn for affected_component
        key_turn = conv.breakthrough_turn()
        observed_agent = (
            key_turn.handled_by_agent_id
            if key_turn and key_turn.handled_by_agent_id
            else None
        )
        # Fall back to the scenario-declared affected string when attribution is unknown
        effective_affected = observed_agent or affected

        # Build scores dict from the breakthrough turn
        scores_dict: dict[str, int | None] = {}
        bt_reasoning = ""
        bt_evidence_quote = ""
        bt_success_indicator: str | None = None
        if key_turn:
            scores_dict = {
                "goal_progress": key_turn.progress_score,
                "policy_compliance": None,
                "data_handling": None,
                "escalation_compliance": None,
            }
            bt_reasoning = key_turn.reasoning or key_turn.progress_reasoning
            bt_evidence_quote = key_turn.evidence_quote
            bt_success_indicator = key_turn.success_indicator

        return [
            Finding(
                finding_id=finding_id,
                title=f"Guided: {scenario.title}",
                severity=sev,
                description=(
                    f"Guided adversarial conversation achieved the goal: "
                    f"{conv.goal_description}  "
                    f"Completed in {len(conv.turns)} turns "
                    f"(final progress={conv.final_progress}/5)."
                ),
                affected_component=effective_affected,
                remediation=remediation_generator.generate(conv.goal_type, scenario.title),
                goal_type=conv.goal_type.value,
                chain_id=f"guided-{conv.conversation_id}",
                sbom_path=conv.sbom_path,
                sbom_path_descriptions=sbom_path_descriptions,
                owasp_asi_ref=conv.owasp_asi_ref or compliance_mapper.owasp_asi_ref(conv.goal_type),
                owasp_llm_ref=conv.owasp_llm_ref or compliance_mapper.owasp_llm_ref(conv.goal_type),
                mitre_atlas_technique=conv.mitre_atlas_technique,
                evidence=transcript,
                reasoning=bt_reasoning,
                evidence_quote=bt_evidence_quote,
                success_indicator=bt_success_indicator,
                scores=scores_dict,
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
    def _format_trigger_step(step_idx: int, sr: StepResult) -> str:
        """Render the step that *triggered* a violation, attacker input first.

        Used by policy-violation evidence so reports point at the specific
        turn the bad behaviour happened on — not a flat summary of every
        step in the chain.  Includes the full payload and a generous
        response excerpt because this is the step the human is meant to
        read and act on.
        """
        step = sr.step
        ok = "✅" if sr.success_signal_found else "❌"
        if step.target_path:
            header = (
                f"Triggering step {step_idx} "
                f"({step.step_type} {ok}): {step.http_method} {step.target_path}"
            )
        else:
            header = f"Triggering step {step_idx} ({step.step_type} {ok})"
        payload = (step.payload or "").strip()
        response = (sr.response or "").strip()
        if len(payload) > 800:
            payload = payload[:800] + "…"
        if len(response) > 800:
            response = response[:800] + "…"
        lines = [header]
        if payload:
            lines.append(f"  Attacker: {payload}")
        if response:
            lines.append(f"  Agent:    {response}")
        if sr.http_status_code is not None:
            lines.append(f"  HTTP:     {sr.http_status_code}")
        return "\n".join(lines)

    @staticmethod
    def _step_evidence_summary(step_details: list[dict]) -> str:
        """One-line summary of attack steps and their responses for evidence fields."""
        parts = []
        for i, step in enumerate(step_details, 1):
            stype = step.get("step_type", "?")
            # WARMUP turns are non-adversarial engagement primers — neutral glyph
            # instead of success/failure so they do not look like failed attacks.
            if stype == "WARMUP":
                ok = "·"
            else:
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

        # Track which step each violation came from so evidence can point at
        # the *specific* triggering turn instead of a flat cross-step summary.
        violations_with_step: list[tuple[int, StepResult, PolicyViolation]] = []
        canary_hits: list[str] = []
        for step_idx, sr in enumerate(step_results, start=1):
            for v in sr.policy_violations:
                violations_with_step.append((step_idx, sr, v))
            canary_hits.extend(sr.canary_hits)
        _all_violations = [v for (_, _, v) in violations_with_step]  # noqa: F841

        # Resolve human-readable component labels for this scenario's target nodes
        affected = ", ".join(
            self._node_label.get(nid, nid) for nid in scenario.target_node_ids[:2]
        )
        # Build human-readable sbom_path_descriptions from the chain's node IDs
        sbom_path_descriptions = [
            self._node_label.get(nid, nid) for nid in chain.sbom_path
        ]

        # Canary-based finding
        if canary_hits and self._trigger_enabled("canary_hits"):
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
        if self._trigger_enabled("policy_violations"):
            for step_idx, sr, violation in violations_with_step:
                sev = severity_scorer.score_finding(scenario.goal_type)
                violation_title = f"{violation.type.replace('_', ' ').title()} — {scenario.title}"
                # Evidence centres on the specific step that triggered the
                # violation — the attacker message sent and the response
                # excerpt — so reports show *where* the bad behaviour happened
                # rather than a flat summary of every step in the chain.
                trigger_block = self._format_trigger_step(step_idx, sr)
                evidence = (
                    f"{violation.evidence}\n\n"
                    f"{trigger_block}\n\n"
                    f"All steps: {step_summary}"
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
            if critical_hits and self._trigger_enabled("critical_success_hits"):
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

        # Lowest-precedence fallback: INJECT success when no stronger trigger fired.
        if not findings and self._trigger_enabled("any_inject_success"):
            inject_hits = [
                sr
                for sr in step_results
                if sr.success_signal_found and sr.step.step_type == "INJECT"
            ]
            if inject_hits:
                sev = severity_scorer.score_finding(scenario.goal_type)
                findings.append(
                    Finding(
                        finding_id=_finding_id(f"inject-success-{scenario.title}"),
                        title=f"Inject Success Signal — {scenario.title}",
                        severity=sev,
                        description=(
                            f"INJECT steps succeeded in scenario '{scenario.title}' "
                            f"without higher-confidence canary/policy/critical triggers."
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

    async def _maybe_probe_endpoints(self) -> None:
        """Live-probe SBOM endpoints when no explicit chat path is configured.

        Updates ``self._chat_path``, ``self._chat_payload_key``, and
        ``self._chat_payload_list`` in-place when a working endpoint is found.
        Only runs when ``self._chat_path`` is empty or the generic default '/chat'.
        """
        from nuguard.common.endpoint_probe import probe_chat_endpoints  # noqa: PLC0415

        if self._chat_path:
            # Already have an explicit path — skip probing
            return

        auth_headers: dict[str, str] = {}
        if self._extra_headers:
            auth_headers.update(self._extra_headers)

        _log.info(
            "redteam: target_endpoint not configured — probing SBOM endpoints at %s",
            self._target_url,
        )
        result = await probe_chat_endpoints(
            target_url=self._target_url,
            sbom=self._sbom,
            auth_headers=auth_headers or None,
            timeout=15.0,
            known_payload_key=(
                self._chat_payload_key
                if self._chat_payload_key != "message"
                else None
            ),
            known_payload_list=self._chat_payload_list,
            known_response_key=self._chat_response_key,
        )
        if result:
            path, pay_key, pay_list = result
            _log.info(
                "redteam: discovered endpoint %s (payload_key=%r list=%s)",
                path, pay_key, pay_list,
            )
            self._chat_path = path
            self._chat_payload_key = pay_key
            self._chat_payload_list = pay_list
        else:
            _log.warning(
                "redteam: endpoint probe found nothing — keeping default %r",
                self._chat_path or "/chat",
            )
            if not self._chat_path:
                self._chat_path = "/chat"
