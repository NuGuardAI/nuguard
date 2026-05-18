"""RemediationSynthesizer — SBOM-aware, concrete remediation artefact generator.

Runs as a final pass after all findings (static + dynamic) are collected.
For each finding it looks up the affected SBOM node, reads available metadata
(system prompt content, tool description, privilege scope, guardrail config)
and produces a concrete ``RemediationArtefact`` — either a system prompt patch,
a guardrail spec, or an architectural change recommendation.

The synthesizer is deterministic by default.  When an ``LLMClient`` is
provided it uses it to generate contextual patch text for
``SYSTEM_PROMPT_PATCH`` artefacts; all other artefact types use templates.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from nuguard.behavior.models import RemediationArtefact, RemediationArtefactType

if TYPE_CHECKING:
    from nuguard.behavior.models import BehaviorAnalysisResult
    from nuguard.common.llm_client import LLMClient
    from nuguard.models.policy import CognitivePolicy
    from nuguard.sbom.models import AiSbomDocument, Node

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Privilege strategy lookup table
# ---------------------------------------------------------------------------

_PRIVILEGE_STRATEGY: dict[str, dict[str, Any]] = {
    "db_write": {
        "guardrail": "auth_check+parameterised_query",
        "requires_auth": True,
        "requires_hitl": False,
        "risk": "data corruption or exfiltration via unparameterised database writes",
    },
    "filesystem_write": {
        "guardrail": "auth_check+path_allowlist",
        "requires_auth": True,
        "requires_hitl": False,
        "risk": "arbitrary file write leading to code injection or data destruction",
    },
    "code_execution": {
        "guardrail": "auth_check+sandbox",
        "requires_auth": True,
        "requires_hitl": True,
        "risk": "full system compromise via arbitrary code execution",
    },
    "admin": {
        "guardrail": "auth_check",
        "requires_auth": True,
        "requires_hitl": True,
        "risk": "privilege escalation and account takeover via admin operations",
    },
    "network_out": {
        "guardrail": "url_allowlist",
        "requires_auth": True,
        "requires_hitl": False,
        "risk": "SSRF or data exfiltration via uncontrolled outbound network requests",
    },
    "email_out": {
        "guardrail": "hitl_approval",
        "requires_auth": True,
        "requires_hitl": True,
        "risk": "phishing or spam via unapproved outbound email",
    },
    "social_media_out": {
        "guardrail": "hitl_approval",
        "requires_auth": True,
        "requires_hitl": True,
        "risk": "brand damage or misinformation via unapproved social media posts",
    },
    "rbac": {
        "guardrail": "role_assertion",
        "requires_auth": True,
        "requires_hitl": False,
        "risk": "horizontal privilege escalation via missing role checks",
    },
}

# LLM prompt templates
_LLM_PATCH_SYSTEM = (
    "You are a security engineer fixing an AI agent's system prompt. "
    "Write ONLY the new or replacement section text to fix the policy violation described. "
    "Be specific and actionable. Under 60 words. Do not explain — output the section text only."
)

_LLM_PATCH_USER = (
    "Agent purpose: {agent_purpose}\n"
    "Policy violation: {violation}\n"
    "Existing prompt excerpt:\n---\n{prompt_excerpt}\n---\n"
    "Write the new '## {section}' section to add to the system prompt:"
)

_LLM_PRIVILEGE_PATCH_SYSTEM = (
    "You are a security engineer writing an access-control restriction for an AI agent's system prompt. "
    "Write ONLY the new restriction text. Under 60 words. Output the restriction text only."
)

_LLM_PRIVILEGE_PATCH_USER = (
    "Agent: {agent_name} — {agent_purpose}\n"
    "High-privilege tool: {tool_name} — {tool_desc}\n"
    "Privilege granted: {privilege_scope} ({risk})\n"
    "Write the 'Access Controls' instruction stating when the tool may and may not be called:"
)


# ---------------------------------------------------------------------------
# SBOM lookup helpers
# ---------------------------------------------------------------------------


def _node_type(node: "Node") -> str:
    ct = getattr(node, "component_type", None)
    return (getattr(ct, "value", None) or str(ct) or "").upper()


def _node_meta(node: "Node", key: str) -> Any:
    meta = getattr(node, "metadata", None)
    if meta is None:
        return None
    val = getattr(meta, key, None)
    if val is not None:
        return val
    extras = getattr(meta, "extras", None) or {}
    return extras.get(key)


def _build_lookup_maps(
    sbom: "AiSbomDocument",
) -> tuple[
    dict[str, "Node"],           # node_by_name
    dict[str, "Node"],           # prompt_by_agent: agent_name → closest PROMPT node
    dict[str, list[str]],        # privilege_map: tool_name → list of privilege node names
]:
    """Build fast-lookup structures from the SBOM for the synthesizer."""
    node_by_name: dict[str, Node] = {}
    prompt_by_agent: dict[str, Node] = {}
    privilege_map: dict[str, list[str]] = {}

    nodes_by_id: dict[str, Node] = {}
    for node in sbom.nodes:
        name = str(getattr(node, "name", "") or "")
        node_id = str(getattr(node, "id", ""))
        if name:
            node_by_name[name] = node
        if node_id:
            nodes_by_id[node_id] = node

    # Map CALLS edges: source_id → set of target_ids
    calls_targets: dict[str, set[str]] = {}
    for edge in sbom.edges:
        rt = getattr(edge, "relationship_type", None)
        rel = (getattr(rt, "value", None) or str(rt) or "").upper()
        if rel == "CALLS":
            src = str(getattr(edge, "source", ""))
            tgt = str(getattr(edge, "target", ""))
            calls_targets.setdefault(src, set()).add(tgt)

    # privilege node IDs
    privilege_node_ids: set[str] = set()
    for node in sbom.nodes:
        if _node_type(node) == "PRIVILEGE":
            privilege_node_ids.add(str(getattr(node, "id", "")))

    # Build privilege_map: for each TOOL, find reachable PRIVILEGE nodes (directly called)
    for node in sbom.nodes:
        if _node_type(node) != "TOOL":
            continue
        tool_name = str(getattr(node, "name", "") or "")
        tool_id = str(getattr(node, "id", ""))
        priv_names: list[str] = []
        for tgt_id in calls_targets.get(tool_id, set()):
            if tgt_id in privilege_node_ids:
                priv_node = nodes_by_id.get(tgt_id)
                if priv_node:
                    prn = str(getattr(priv_node, "name", "") or "")
                    if prn:
                        priv_names.append(prn)
        if priv_names and tool_name:
            privilege_map[tool_name] = priv_names

    # Build prompt_by_agent: agent CALLS/USES → PROMPT nodes
    for node in sbom.nodes:
        if _node_type(node) != "AGENT":
            continue
        agent_name = str(getattr(node, "name", "") or "")
        agent_id = str(getattr(node, "id", ""))
        for tgt_id in calls_targets.get(agent_id, set()):
            tgt_node = nodes_by_id.get(tgt_id)
            if tgt_node and _node_type(tgt_node) == "PROMPT":
                prompt_by_agent[agent_name] = tgt_node
                break

    return node_by_name, prompt_by_agent, privilege_map


def _prompt_content(prompt_node: "Node | None") -> str:
    """Extract text content from a PROMPT node."""
    if prompt_node is None:
        return ""
    meta = getattr(prompt_node, "metadata", None)
    if meta is None:
        return ""
    extras = getattr(meta, "extras", None) or {}
    return str(extras.get("content", "") or "")


def _prompt_location(prompt_node: "Node | None") -> str:
    """Return a source-file location hint for a PROMPT node."""
    if prompt_node is None:
        return ""
    evs = getattr(prompt_node, "evidence", []) or []
    for ev in evs:
        loc = getattr(ev, "location", None)
        if loc:
            path = getattr(loc, "path", "")
            line = getattr(loc, "line", None)
            return f"{path}:{line}" if line else path
    return ""


# ---------------------------------------------------------------------------
# Finding classifier
# ---------------------------------------------------------------------------

_SENSITIVE_DATA_PATTERNS = re.compile(
    r"\b(password|passwords|pin\b|full.card|card.number|ssn|social.security|"
    r"asking.for|requesting.credentials)\b",
    re.IGNORECASE,
)
_HITL_ESCALATION_PATTERNS = re.compile(
    r"\b(escalat|human.agent|speak.to|talk.to|route.to|hitl|human-in-the-loop|"
    r"escalation.trigger|not.honoured|not honored)\b",
    re.IGNORECASE,
)
_PRIVILEGE_PATTERNS = re.compile(
    r"\b(high.privilege|unauthenticated.agent|privilege.escal|ba.005)\b",
    re.IGNORECASE,
)
_RESTRICTED_ACTION_PATTERNS = re.compile(
    r"\b(restricted.action|ba.003|ba.006|untrusted.mcp|write.access|calls.edge.*restricted)\b",
    re.IGNORECASE,
)


# Redteam ``GoalType`` → synthesizer dtype.  Redteam findings carry a
# ``goal_type`` field that directly maps to a remediation handler without
# needing pattern-based classification.
_GOAL_TYPE_DTYPE: dict[str, str] = {
    "prompt_driven_threat": "blocked_topics_missing",
    "data_exfiltration": "data_leak",
    "privilege_escalation": "privilege_escalation",
    "tool_abuse": "risky_tool",
    "policy_violation": "policy_violation_generic",
    "mcp_toxic_flow": "restricted_action_reachable",
    "api_attack": "privilege_escalation",
}


def _classify_finding(finding: dict) -> str:
    """Return a string category for the finding."""
    # Redteam shortcut: if the finding carries a ``goal_type`` (set by the
    # RedteamOrchestrator when it builds Finding objects), trust it.  This
    # keeps redteam findings from being misclassified by the behavior-module
    # heuristic classifier below, which was tuned for ``BA-*`` finding IDs.
    goal_type = str(finding.get("goal_type", "") or "").strip().lower()
    if goal_type:
        mapped = _GOAL_TYPE_DTYPE.get(goal_type)
        if mapped:
            return mapped

    title = str(finding.get("title", "")).lower()
    desc = str(finding.get("description", "")).lower()
    fid = str(finding.get("finding_id", "")).lower()
    text = f"{title} {desc} {fid}"

    if "ba-005" in fid or _PRIVILEGE_PATTERNS.search(text):
        return "privilege_escalation"
    if "ba-003" in fid or "ba-006" in fid or _RESTRICTED_ACTION_PATTERNS.search(text):
        return "restricted_action_reachable"
    if "ba-008" in fid or ("hitl" in text and "gate" in text and "missing" in text):
        return "hitl_gate_missing"
    if "ba-007" in fid or "blocked_topics" in text:
        return "blocked_topics_missing"
    if "ba-004" in fid or ("pii" in text and "datastore" in text):
        return "data_leak"
    if "ba-002" in fid or ("risky.tool" in text or "no.guardrail" in text):
        return "risky_tool"
    if "ba-001" in fid or "restricted.topic" in text:
        return "blocked_topics_missing"
    if _SENSITIVE_DATA_PATTERNS.search(text):
        return "sensitive_data_request"
    if _HITL_ESCALATION_PATTERNS.search(text):
        return "hitl_not_honoured"
    if "data_leak" in text or "data leak" in text or "data.handling" in text:
        return "data_leak"
    if "intent_misalignment" in text or "intent misalignment" in text:
        return "intent_misalignment"
    if "policy_violation" in text or "policy violation" in text:
        return "policy_violation_generic"
    return "generic"


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------


def _merge_artefacts(artefacts: list[RemediationArtefact]) -> list[RemediationArtefact]:
    """Merge artefacts that target the same (component, artefact_type).

    SYSTEM_PROMPT_PATCH artefacts for the same component are merged into one,
    concatenating their patch_text blocks.  Other types are kept as-is.
    """
    groups: dict[tuple[str, str], list[RemediationArtefact]] = {}
    for a in artefacts:
        key = (a.component, a.artefact_type.value)
        groups.setdefault(key, []).append(a)

    merged: list[RemediationArtefact] = []
    for (comp, atype), group in groups.items():
        if atype != RemediationArtefactType.SYSTEM_PROMPT_PATCH or len(group) == 1:
            merged.extend(group)
            continue
        # Merge patch texts
        finding_ids: list[str] = []
        patch_parts: list[str] = []
        for a in group:
            finding_ids.extend(a.finding_ids)
            if a.patch_text:
                patch_parts.append(a.patch_text)
        base = group[0]
        merged.append(RemediationArtefact(
            finding_ids=finding_ids,
            component=base.component,
            component_type=base.component_type,
            artefact_type=base.artefact_type,
            priority=base.priority,
            patch_location=base.patch_location,
            patch_section="Security Rules",
            patch_text="\n\n".join(patch_parts),
            rationale=f"Merged {len(group)} system prompt patches for {comp}",
        ))

    # Sort by priority
    _prio = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    merged.sort(key=lambda a: _prio.get(a.priority, 99))
    return merged


# ---------------------------------------------------------------------------
# Main synthesizer
# ---------------------------------------------------------------------------


class RemediationSynthesizer:
    """SBOM-aware remediation synthesizer.

    Converts raw findings into concrete, node-specific remediation artefacts
    with actionable patch text, guardrail specs, or architectural guidance.

    Args:
        sbom: AI-SBOM document (provides node metadata and prompt content).
        policy: Parsed CognitivePolicy (provides restricted topics/actions).
        llm_client: Optional LLM client for contextual patch generation.
    """

    def __init__(
        self,
        sbom: "AiSbomDocument | None" = None,
        policy: "CognitivePolicy | None" = None,
        llm_client: "LLMClient | None" = None,
    ) -> None:
        self._sbom = sbom
        self._policy = policy
        self._llm = llm_client

        self._node_by_name: dict[str, Node] = {}
        self._prompt_by_agent: dict[str, Node] = {}
        self._privilege_map: dict[str, list[str]] = {}

        if sbom is not None:
            self._node_by_name, self._prompt_by_agent, self._privilege_map = (
                _build_lookup_maps(sbom)
            )

    def synthesize(self, result: "BehaviorAnalysisResult") -> list[RemediationArtefact]:
        """Return concrete remediation artefacts for all findings in *result*.

        Args:
            result: Complete BehaviorAnalysisResult with static and dynamic findings.

        Returns:
            Sorted, deduplicated list of RemediationArtefact objects.
        """
        all_findings = list(result.static_findings) + list(result.dynamic_findings)
        return self.synthesize_findings(all_findings, result=result)

    async def synthesize_async(self, result: "BehaviorAnalysisResult") -> list[RemediationArtefact]:
        """Async version of synthesize() — runs all per-finding synthesis in parallel.

        Preferred over synthesize() when called from an async context (e.g. analyzer.py)
        because it properly awaits _llm_patch_async / _llm_privilege_patch_async instead
        of silently skipping them (the sync shims return "" when a loop is running).
        """
        all_findings = list(result.static_findings) + list(result.dynamic_findings)
        return await self.synthesize_findings_async(all_findings, result=result)

    async def synthesize_findings_async(
        self,
        findings: list[dict],
        *,
        result: "BehaviorAnalysisResult | None" = None,
    ) -> list[RemediationArtefact]:
        """Async version of synthesize_findings() — parallel per-finding synthesis.

        Fires all _synthesize_one_async() coroutines concurrently with asyncio.gather,
        including the LLM patch calls inside each. Falls back gracefully on exceptions.
        """
        import asyncio

        batches = await asyncio.gather(
            *(self._synthesize_one_async(f, result) for f in findings),
            return_exceptions=True,
        )
        artefacts: list[RemediationArtefact] = []
        seen_keys: set[str] = set()
        for batch in batches:
            if isinstance(batch, BaseException):
                _log.debug("synthesize_findings_async: one finding failed: %s", batch)
                continue
            for art in batch:
                key = f"{art.component}:{art.artefact_type.value}:{(art.patch_section or art.guardrail_name or art.change_description or '')[:60]}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    artefacts.append(art)
        return _merge_artefacts(artefacts)

    async def _synthesize_one_async(
        self,
        finding: dict,
        result: "BehaviorAnalysisResult | None" = None,
    ) -> list[RemediationArtefact]:
        """Async version of _synthesize_one — awaits LLM patch calls directly."""
        component = str(finding.get("affected_component", "unknown"))
        finding_id = str(finding.get("finding_id", ""))
        severity = str(finding.get("severity", "medium")).lower()
        priority = severity if severity in ("critical", "high", "medium", "low") else "medium"
        node = self._node_by_name.get(component)
        dtype = _classify_finding(finding)

        _log.debug("RemediationSynthesizer(async): finding=%s dtype=%s component=%s", finding_id, dtype, component)

        if dtype == "data_handling":
            return await self._patch_data_handling_async(component, node, finding, finding_id, priority)
        if dtype == "privilege_escalation":
            return await self._remediate_privilege_escalation_async(component, node, finding, finding_id, priority)
        # All other dtypes produce no LLM calls — delegate to the sync path
        return self._synthesize_one(finding, result)

    async def _patch_data_handling_async(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        prompt_node = self._prompt_by_agent.get(component)
        location = _prompt_location(prompt_node)
        existing = _prompt_content(prompt_node)
        patch_text = await self._llm_patch_async(
            section="Data Handling Rules",
            violation=finding.get("description", "Agent asks for sensitive credentials"),
            prompt_excerpt=existing[:400],
            intent_purpose=getattr(getattr(self, "_intent", None), "app_purpose", ""),
        ) or (
            "## Data Handling Rules\n"
            "- NEVER ask the user for their password, PIN, or full card number.\n"
            "- If authentication is required, direct the user to the secure login portal.\n"
            "- You may confirm the last 4 digits of an account number but never the full number.\n"
            "- Do not store, log, or repeat any credential the user provides."
        )
        return [RemediationArtefact(
            finding_ids=[finding_id],
            component=component,
            component_type=_node_type(node) if node else "AGENT",
            artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
            priority=priority,
            patch_location=location or None,
            patch_section="Data Handling Rules",
            patch_text=patch_text,
            rationale=finding.get("description", "Agent violates data handling rules."),
        )]

    async def _remediate_privilege_escalation_async(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        # Resolve the privilege path — same logic as the sync version
        priv_names = self._privilege_map.get(component, [])
        tool_name = str(finding.get("tool_name", "")) or (priv_names[0].split(":")[-1] if priv_names else "high-privilege-tool")
        tool_desc = str(finding.get("tool_description", "") or finding.get("description", ""))[:200]
        priv_scope = ""
        if priv_names:
            priv_scope = priv_names[0].split(":")[-1].strip()

        strategy = _PRIVILEGE_STRATEGY.get(priv_scope, {
            "guardrail": "auth_check",
            "requires_auth": True,
            "requires_hitl": False,
            "risk": "unauthorised access to a high-privilege operation",
        })
        requires_hitl: bool = bool(strategy.get("requires_hitl", False))
        risk: str = str(strategy.get("risk", "privilege escalation"))

        hitl_note = " Require manager HITL approval before executing." if requires_hitl else ""
        patch_text = await self._llm_privilege_patch_async(
            agent_name=component,
            tool_name=tool_name,
            tool_desc=tool_desc,
            privilege_scope=priv_scope or "high-privilege",
            risk=risk,
        ) or (
            f"## Access Controls — {tool_name}\n"
            f"Before calling {tool_name}(), confirm that the customer is authenticated.\n"
            f"If no active session is detected, direct the user to log in before proceeding.\n"
            f"Never invoke {tool_name}() for an unauthenticated caller.{hitl_note}"
        )
        # Delegate artefact construction to the sync path after we have the patch text
        # by temporarily injecting the result into a finding copy the sync path will use.
        # Simpler: just construct the artefacts directly here (mirrors sync version).
        return await self._build_privilege_artefacts_async(
            component, node, finding, finding_id, priority, tool_name, patch_text, strategy, priv_scope
        )

    async def _build_privilege_artefacts_async(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
        tool_name: str,
        patch_text: str,
        strategy: dict,
        priv_scope: str,
    ) -> list[RemediationArtefact]:
        """Construct privilege-escalation artefacts (mirrors sync _remediate_privilege_escalation)."""
        requires_auth: bool = bool(strategy.get("requires_auth", True))
        requires_hitl: bool = bool(strategy.get("requires_hitl", False))
        risk: str = str(strategy.get("risk", "privilege escalation"))
        guardrail_type: str = str(strategy.get("guardrail", "auth_check"))
        hitl_note = " Require manager HITL approval before executing." if requires_hitl else ""

        artefacts: list[RemediationArtefact] = [
            RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type=_node_type(node) if node else "AGENT",
                artefact_type=RemediationArtefactType.ARCHITECTURAL_CHANGE,
                priority="critical",
                change_description=(
                    f"Add AUTH node protecting '{component}' → '{tool_name}' "
                    f"(privilege: {priv_scope or 'high-privilege'})"
                ),
                change_detail=(
                    f"Agent '{component}' can reach high-privilege tool '{tool_name}' "
                    f"({risk}) without authentication.\n\n"
                    f"Required changes:\n"
                    f"1. Add an AUTH node (type: bearer/basic/oauth2) to the SBOM.\n"
                    f"2. Add a PROTECTS edge: AUTH → '{component}'.\n"
                    f"3. Add a PROTECTS edge: AUTH → '{tool_name}'.\n"
                    f"4. The application must verify a valid session token before any "
                    f"'{tool_name}' invocation."
                ),
                rationale=finding.get("description", f"Privilege escalation via {tool_name}."),
            ),
        ]
        if requires_auth:
            artefacts.append(RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type=_node_type(node) if node else "AGENT",
                artefact_type=RemediationArtefactType.INPUT_GUARDRAIL,
                priority=priority,
                guardrail_name=guardrail_type,
                guardrail_type=guardrail_type,
                guardrail_trigger="pre-tool-call",
                guardrail_action="BLOCK",
                guardrail_message=f"Authentication required before invoking {tool_name}.",
                rationale=f"Guardrail required before any {tool_name} invocation.{hitl_note}",
            ))
        if patch_text:
            artefacts.append(RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type=_node_type(node) if node else "AGENT",
                artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
                priority=priority,
                patch_section="Access Controls",
                patch_text=patch_text,
                rationale=f"System prompt must enforce authentication before {tool_name}.",
            ))
        return artefacts

    def synthesize_findings(
        self,
        findings: list[dict],
        *,
        result: "BehaviorAnalysisResult | None" = None,
    ) -> list[RemediationArtefact]:
        """Return remediation artefacts for a plain list of finding dicts.

        Callers outside the behavior module (e.g. the redteam CLI) should
        use this entry point — it bypasses the ``BehaviorAnalysisResult``
        wrapper but reuses the same SBOM-aware, per-node synthesis logic.

        Each finding dict should carry at least ``finding_id``, ``title``,
        ``description``, ``severity``, and ``affected_component``.  Redteam
        findings should also include ``goal_type`` so the classifier can
        route them directly without heuristics.
        """
        artefacts: list[RemediationArtefact] = []
        seen_keys: set[str] = set()
        for finding in findings:
            produced = self._synthesize_one(finding, result)
            for art in produced:
                key = f"{art.component}:{art.artefact_type.value}:{(art.patch_section or art.guardrail_name or art.change_description or '')[:60]}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    artefacts.append(art)

        return _merge_artefacts(artefacts)

    def _synthesize_one(
        self,
        finding: dict,
        result: "BehaviorAnalysisResult | None" = None,
    ) -> list[RemediationArtefact]:
        """Return 0–2 artefacts for a single finding."""
        component = str(finding.get("affected_component", "unknown"))
        finding_id = str(finding.get("finding_id", ""))
        severity = str(finding.get("severity", "medium")).lower()
        priority = severity if severity in ("critical", "high", "medium", "low") else "medium"
        node = self._node_by_name.get(component)
        dtype = _classify_finding(finding)

        _log.debug("RemediationSynthesizer: finding=%s dtype=%s component=%s", finding_id, dtype, component)

        if dtype == "privilege_escalation":
            return self._remediate_privilege_escalation(component, node, finding, finding_id, priority)
        if dtype == "restricted_action_reachable":
            return self._remediate_restricted_action(component, node, finding, finding_id, priority)
        if dtype == "hitl_gate_missing":
            return self._spec_hitl_guardrail(component, node, finding, finding_id, priority)
        if dtype == "blocked_topics_missing":
            return self._patch_blocked_topics(component, node, finding, finding_id, priority)
        if dtype == "data_leak":
            return self._add_output_redactor(component, node, finding, finding_id, priority)
        if dtype == "sensitive_data_request":
            return self._patch_data_handling(component, node, finding, finding_id, priority)
        if dtype == "hitl_not_honoured":
            return self._add_hitl_guardrail(component, node, finding, finding_id, priority)
        if dtype == "intent_misalignment":
            return self._patch_tool_invocation(component, node, finding, finding_id, priority)
        if dtype == "risky_tool":
            return self._add_input_guardrail_risky(component, node, finding, finding_id, priority)
        if dtype == "policy_violation_generic":
            return self._patch_generic_violation(component, node, finding, finding_id, priority)
        # generic — produce a minimal system_prompt_patch
        return self._patch_generic_violation(component, node, finding, finding_id, priority)

    # ------------------------------------------------------------------
    # 1. Sensitive data request — agent asks for password / PII
    # ------------------------------------------------------------------

    def _patch_data_handling(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        prompt_node = self._prompt_by_agent.get(component)
        location = _prompt_location(prompt_node)
        existing = _prompt_content(prompt_node)

        patch_text = self._llm_patch(
            section="Data Handling Rules",
            violation=finding.get("description", "Agent asks for sensitive credentials"),
            prompt_excerpt=existing[:400],
            intent_purpose=getattr(getattr(self, "_intent", None), "app_purpose", ""),
        ) or (
            "## Data Handling Rules\n"
            "- NEVER ask the user for their password, PIN, or full card number.\n"
            "- If authentication is required, direct the user to the secure login portal.\n"
            "- You may confirm the last 4 digits of an account number but never the full number.\n"
            "- Do not store, log, or repeat any credential the user provides."
        )

        return [RemediationArtefact(
            finding_ids=[finding_id],
            component=component,
            component_type=_node_type(node) if node else "AGENT",
            artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
            priority=priority,
            patch_location=location or None,
            patch_section="Data Handling Rules",
            patch_text=patch_text,
            rationale=finding.get("description", "Agent violates data handling rules."),
        )]

    # ------------------------------------------------------------------
    # 2. HITL trigger not honoured at runtime
    # ------------------------------------------------------------------

    def _add_hitl_guardrail(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        desc = finding.get("description", "")
        # Find escalation tool name from SBOM
        escalation_tool = "escalate_to_human_agent"
        if self._sbom:
            for n in self._sbom.nodes:
                if _node_type(n) == "TOOL":
                    nm = str(getattr(n, "name", "") or "")
                    if "escalat" in nm.lower() or "human" in nm.lower():
                        escalation_tool = nm
                        break

        artefacts = [
            RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type=_node_type(node) if node else "AGENT",
                artefact_type=RemediationArtefactType.INPUT_GUARDRAIL,
                priority=priority,
                guardrail_name="human_escalation_guard",
                guardrail_type="regex",
                guardrail_trigger=(
                    r"\b(speak|talk|connect|transfer)\s+(to\s+)?(a\s+)?"
                    r"(human|agent|person|representative|manager|supervisor)\b"
                ),
                guardrail_action="ROUTE",
                guardrail_message=f"Let me connect you with a team member. → {escalation_tool}()",
                rationale=desc or "HITL escalation trigger not honoured.",
            ),
            RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type=_node_type(node) if node else "AGENT",
                artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
                priority=priority,
                patch_section="Escalation Rules",
                patch_text=(
                    f"## Escalation Rules\n"
                    f"If the user says they want to speak with a human, agent, or representative, "
                    f"immediately call {escalation_tool}() without further conversation."
                ),
                rationale=desc or "Agent does not escalate when user requests a human.",
            ),
        ]
        return artefacts

    # ------------------------------------------------------------------
    # 3. HITL gate missing (structural — BA-008)
    # ------------------------------------------------------------------

    def _spec_hitl_guardrail(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        desc = finding.get("description", "")
        title = finding.get("title", "")
        # Extract trigger text from title: "No HITL gate detected for trigger: '...'"
        trigger_match = re.search(r"trigger:\s*'(.+?)'", title)
        trigger_text = trigger_match.group(1) if trigger_match else desc[:100]
        # Derive a compact regex from the trigger text (use key words)
        words = [w for w in re.findall(r"\b\w{4,}\b", trigger_text.lower()) if w not in
                 ("that", "this", "with", "when", "from", "have", "will", "should")]
        pattern = r"\b(" + "|".join(words[:5]) + r")\b" if words else r"\b(escalate|human)\b"

        return [
            RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type="system",
                artefact_type=RemediationArtefactType.ARCHITECTURAL_CHANGE,
                priority=priority,
                change_description=f"Add GUARDRAIL node for HITL trigger: {trigger_text[:80]}",
                change_detail=(
                    f"Add a GUARDRAIL node (type: GUARDRAIL) to the SBOM.\n"
                    f"Connect it with a PROTECTS edge to the affected agent.\n\n"
                    f"Guardrail specification:\n"
                    f"  Name: hitl_guard_{uuid.uuid4().hex[:6]}\n"
                    f"  Type: input_classifier\n"
                    f"  Pattern: {pattern}\n"
                    f"  Action: ROUTE → escalate_to_human_agent()\n"
                    f"  Fallback message: \"Let me connect you with a team member who can help.\""
                ),
                rationale=desc,
            ),
        ]

    # ------------------------------------------------------------------
    # 4. Blocked topics gap (BA-007)
    # ------------------------------------------------------------------

    def _patch_blocked_topics(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        desc = finding.get("description", "")
        # Extract the missing topic list from description
        topics_match = re.search(r"\[(.+?)\]", desc, re.DOTALL)
        if topics_match:
            raw = topics_match.group(1)
            topics = [t.strip().strip("'\"") for t in raw.split(",") if t.strip()]
        elif self._policy:
            topics = list(self._policy.restricted_topics)
        else:
            topics = []

        topic_lines = "\n".join(f'- "{t}"' for t in topics[:8])
        patch_text = (
            "## Out of Scope\n"
            "Do NOT discuss or assist with any of the following topics:\n"
            f"{topic_lines}\n"
            'If asked about these, respond: "I can only assist with topics related to this service."'
        )

        artefacts = [
            RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type=_node_type(node) if node else "AGENT",
                artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
                priority=priority,
                patch_section="Out of Scope",
                patch_text=patch_text,
                rationale=desc,
            ),
        ]
        # Optional input guardrail for topic blocking
        if topics:
            short_topics = [t[:40] for t in topics[:4]]
            artefacts.append(RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type=_node_type(node) if node else "AGENT",
                artefact_type=RemediationArtefactType.INPUT_GUARDRAIL,
                priority="medium",
                guardrail_name=f"topic_block_{component.lower().replace(' ', '_')[:20]}",
                guardrail_type="topic_classifier",
                guardrail_trigger=", ".join(short_topics),
                guardrail_action="BLOCK",
                guardrail_message="I'm sorry, that's outside my area of expertise.",
                rationale=f"Block restricted topics at the input layer for {component}.",
            ))
        return artefacts

    # ------------------------------------------------------------------
    # 5. Data leak — output guardrail (BA-004)
    # ------------------------------------------------------------------

    def _add_output_redactor(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        desc = finding.get("description", "")
        # Pull classified field names from SBOM node metadata if available
        fields: list[str] = []
        if node:
            pii = _node_meta(node, "pii_fields") or []
            if isinstance(pii, list):
                fields.extend(str(f) for f in pii[:8])
            phi = _node_meta(node, "phi_fields") or []
            if isinstance(phi, list):
                fields.extend(str(f) for f in phi[:4])
            pfi = _node_meta(node, "pfi_fields") or []
            if isinstance(pfi, list):
                fields.extend(str(f) for f in pfi[:4])
            classified = _node_meta(node, "classified_fields") or {}
            if isinstance(classified, dict):
                for tbl_fields in classified.values():
                    if isinstance(tbl_fields, list):
                        fields.extend(str(f) for f in tbl_fields[:4])

        if not fields:
            fields = ["account_number", "routing_number", "ssn", "card_number",
                      "password", "api_key", "token"]

        return [RemediationArtefact(
            finding_ids=[finding_id],
            component=component,
            component_type=_node_type(node) if node else "TOOL",
            artefact_type=RemediationArtefactType.OUTPUT_GUARDRAIL,
            priority=priority,
            guardrail_name=f"output_redactor_{component.lower().replace(' ', '_')[:20]}",
            guardrail_type="field_redactor",
            guardrail_trigger=", ".join(fields),
            guardrail_action="REDACT",
            guardrail_message="[REDACTED]",
            rationale=desc or "Sensitive fields must not appear in agent responses.",
        )]

    # ------------------------------------------------------------------
    # 6. Intent misalignment — tool not invoked / wrong tool
    # ------------------------------------------------------------------

    def _patch_tool_invocation(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        desc = finding.get("description", "")
        tool_desc = ""
        if node:
            tool_desc = str(_node_meta(node, "description") or "")

        # Find parent agent(s) that call this tool
        agent_name = "the agent"
        if self._sbom:
            for n in self._sbom.nodes:
                if _node_type(n) != "AGENT":
                    continue
                for edge in self._sbom.edges:
                    rt = getattr(edge, "relationship_type", None)
                    rel = (getattr(rt, "value", None) or str(rt) or "").upper()
                    if (rel == "CALLS"
                            and str(edge.source) == str(getattr(n, "id", ""))
                            and component in (
                                str(getattr(node, "name", "") if node else ""),
                                str(getattr(node, "id", "") if node else ""),
                            )):
                        agent_name = str(getattr(n, "name", "") or "the agent")
                        break

        patch_text = (
            f"## Tool Invocation — {component}\n"
            f"When the user requests actions handled by '{component}'"
            + (f" ({tool_desc[:80]})" if tool_desc else "")
            + f", call {component}() explicitly and present the result to the user. "
            f"Do not attempt to fulfil this request without invoking the tool."
        )

        return [RemediationArtefact(
            finding_ids=[finding_id],
            component=agent_name,
            component_type="AGENT",
            artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
            priority=priority,
            patch_section=f"Tool Invocation — {component}",
            patch_text=patch_text,
            rationale=desc or f"Agent does not invoke {component} when expected.",
        )]

    # ------------------------------------------------------------------
    # 7. Privilege escalation — unauthenticated agent + high-privilege tool (BA-005)
    # ------------------------------------------------------------------

    def _remediate_privilege_escalation(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        desc = finding.get("description", "")
        # Extract tool name from finding title: "Unauthenticated agent '...' can access high-privilege tool '...'"
        tool_match = re.search(r"tool '([^']+)'", str(finding.get("title", "")))
        tool_name = tool_match.group(1) if tool_match else "the high-privilege tool"
        tool_node = self._node_by_name.get(tool_name)
        tool_desc = str(_node_meta(tool_node, "description") or "") if tool_node else ""

        # Determine privilege scope from the privilege_map
        priv_names = self._privilege_map.get(tool_name, [])
        priv_scope = ""
        priv_node_name = ""
        if priv_names:
            priv_node_name = priv_names[0]
            # Normalise: "privilege:db_write" → "db_write"
            priv_scope = priv_node_name.split(":")[-1].strip()

        strategy = _PRIVILEGE_STRATEGY.get(priv_scope, {
            "guardrail": "auth_check",
            "requires_auth": True,
            "requires_hitl": False,
            "risk": "unauthorised access to a high-privilege operation",
        })
        requires_auth: bool = bool(strategy.get("requires_auth", True))
        requires_hitl: bool = bool(strategy.get("requires_hitl", False))
        risk: str = str(strategy.get("risk", "privilege escalation"))
        guardrail_type: str = str(strategy.get("guardrail", "auth_check"))

        hitl_note = " Require manager HITL approval before executing." if requires_hitl else ""
        patch_text = self._llm_privilege_patch(
            agent_name=component,
            tool_name=tool_name,
            tool_desc=tool_desc,
            privilege_scope=priv_scope or "high-privilege",
            risk=risk,
        ) or (
            f"## Access Controls — {tool_name}\n"
            f"Before calling {tool_name}(), confirm that the customer is authenticated.\n"
            f"If no active session is detected, direct the user to log in before proceeding.\n"
            f"Never invoke {tool_name}() for an unauthenticated caller.{hitl_note}"
        )

        artefacts = [
            RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type=_node_type(node) if node else "AGENT",
                artefact_type=RemediationArtefactType.ARCHITECTURAL_CHANGE,
                priority="critical",
                change_description=(
                    f"Add AUTH node protecting '{component}' → '{tool_name}' "
                    f"(privilege: {priv_scope or 'high-privilege'})"
                ),
                change_detail=(
                    f"Agent '{component}' can reach high-privilege tool '{tool_name}' "
                    f"({risk}) without authentication.\n\n"
                    f"Required changes:\n"
                    f"1. Add an AUTH node (type: bearer/basic/oauth2) to the SBOM.\n"
                    f"2. Add a PROTECTS edge: AUTH → '{component}'.\n"
                    f"3. Add a PROTECTS edge: AUTH → '{tool_name}'.\n"
                    f"4. The application must verify a valid session token before any "
                    f"'{tool_name}' invocation."
                    + ("\n5. Add HITL approval step before executing privileged action." if requires_hitl else "")
                ),
                privilege_scope=priv_scope or None,
                privilege_node=priv_node_name or None,
                requires_auth=requires_auth,
                requires_hitl=requires_hitl,
                rationale=desc,
            ),
            RemediationArtefact(
                finding_ids=[finding_id],
                component=tool_name,
                component_type="TOOL",
                artefact_type=RemediationArtefactType.INPUT_GUARDRAIL,
                priority="critical",
                guardrail_name=f"auth_gate_{tool_name.lower().replace(' ', '_')[:24]}",
                guardrail_type=guardrail_type,
                guardrail_trigger=f"any call to {tool_name}()",
                guardrail_action="BLOCK",
                guardrail_message="Please log in to complete this action.",
                privilege_scope=priv_scope or None,
                privilege_node=priv_node_name or None,
                requires_auth=requires_auth,
                requires_hitl=requires_hitl,
                rationale=f"Block unauthenticated calls to high-privilege tool '{tool_name}'.",
            ),
            RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type=_node_type(node) if node else "AGENT",
                artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
                priority="critical",
                patch_section=f"Access Controls — {tool_name}",
                patch_text=patch_text,
                privilege_scope=priv_scope or None,
                privilege_node=priv_node_name or None,
                requires_auth=requires_auth,
                requires_hitl=requires_hitl,
                rationale=desc,
            ),
        ]
        return artefacts

    # ------------------------------------------------------------------
    # 8. Restricted-action tool reachable (BA-003 + BA-006)
    # ------------------------------------------------------------------

    def _remediate_restricted_action(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        desc = finding.get("description", "")
        title = finding.get("title", "")

        # Extract tool name from title: "Agent '...' can invoke restricted action tool '...'"
        tool_match = re.search(r"tool '([^']+)'", title)
        tool_name = tool_match.group(1) if tool_match else "the restricted tool"
        tool_node = self._node_by_name.get(tool_name)
        tool_desc = str(_node_meta(tool_node, "description") or "") if tool_node else ""

        # Extract the restricted action text from the description
        action_match = re.search(r"restricts action '([^']+)'", desc)
        restricted_action = action_match.group(1) if action_match else desc[:120]

        # Determine if this is a write-capable / high-impact tool
        write_re = re.compile(r"\b(write|update|delete|insert|modify|create|transfer|send|post|pay|charge)\b", re.I)
        is_write = write_re.search(tool_name + " " + tool_desc + " " + restricted_action) is not None

        # Check if this is an MCP / untrusted server scenario
        is_mcp = "untrusted.mcp" in desc.lower() or "mcp server" in desc.lower() or "ba-006" in str(finding.get("finding_id", "")).lower()

        artefacts: list[RemediationArtefact] = []

        if is_mcp:
            artefacts.append(RemediationArtefact(
                finding_ids=[finding_id],
                component=component,
                component_type=_node_type(node) if node else "FRAMEWORK",
                artefact_type=RemediationArtefactType.ARCHITECTURAL_CHANGE,
                priority=priority,
                change_description=f"Restrict untrusted MCP server '{component}' write tool access",
                change_detail=(
                    f"Untrusted MCP server '{component}' has write-capable tool '{tool_name}'.\n"
                    f"Options:\n"
                    f"1. Set trust_level='trusted' only after verifying server identity (TLS + signed manifest).\n"
                    f"2. If server must remain untrusted, remove the CALLS edge to '{tool_name}'.\n"
                    f"3. If write access is required, add a GUARDRAIL PROTECTS edge with path allowlist."
                ),
                edge_to_remove=(component, tool_name),
                rationale=desc,
            ))

        # Confirmation guardrail
        confirmation_msg = (
            f"Are you sure you want me to proceed with '{tool_name}'? (yes/no)"
            if is_write else
            f"Confirm before using '{tool_name}'? (yes/no)"
        )
        artefacts.append(RemediationArtefact(
            finding_ids=[finding_id],
            component=component,
            component_type=_node_type(node) if node else "AGENT",
            artefact_type=RemediationArtefactType.INPUT_GUARDRAIL,
            priority=priority,
            guardrail_name=f"confirm_gate_{tool_name.lower().replace(' ', '_')[:24]}",
            guardrail_type="confirmation_required",
            guardrail_trigger=(
                f"call to {tool_name}() without explicit user confirmation in same turn"
            ),
            guardrail_action="HOLD",
            guardrail_message=confirmation_msg,
            rationale=f"Policy restricts: {restricted_action[:120]}",
        ))

        # System prompt patch
        patch_text = (
            f"## Restricted Action — {tool_name}\n"
            f"The action '{restricted_action[:100]}' is restricted by policy.\n"
            f"Before calling {tool_name}(), you MUST receive explicit confirmation from the user "
            f"in the same conversation turn (e.g. 'yes', 'confirm', 'go ahead').\n"
            f"Do not invoke {tool_name}() based on implied consent."
        )
        artefacts.append(RemediationArtefact(
            finding_ids=[finding_id],
            component=component,
            component_type=_node_type(node) if node else "AGENT",
            artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
            priority=priority,
            patch_section=f"Restricted Action — {tool_name}",
            patch_text=patch_text,
            rationale=desc,
        ))

        return artefacts

    # ------------------------------------------------------------------
    # BA-002: Risky tool (SQL-injectable / SSRF) with no guardrail
    # ------------------------------------------------------------------

    def _add_input_guardrail_risky(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        desc = finding.get("description", "")
        is_sql = "sql" in desc.lower() or "sql_injectable" in desc.lower()
        is_ssrf = "ssrf" in desc.lower()

        if is_sql:
            gt = "parameterised_query"
            trigger = "raw string query construction detected in tool input"
            action = "BLOCK"
            msg = "Input validation failed: parameterised queries are required."
        elif is_ssrf:
            gt = "url_allowlist"
            trigger = "URL/endpoint parameter supplied by untrusted input"
            action = "BLOCK"
            msg = "Input validation failed: only allow-listed URLs are permitted."
        else:
            gt = "input_validator"
            trigger = "potentially unsafe input detected"
            action = "BLOCK"
            msg = "Input validation failed."

        return [RemediationArtefact(
            finding_ids=[finding_id],
            component=component,
            component_type=_node_type(node) if node else "TOOL",
            artefact_type=RemediationArtefactType.INPUT_GUARDRAIL,
            priority=priority,
            guardrail_name=f"input_guard_{component.lower().replace(' ', '_')[:22]}",
            guardrail_type=gt,
            guardrail_trigger=trigger,
            guardrail_action=action,
            guardrail_message=msg,
            rationale=desc,
        )]

    # ------------------------------------------------------------------
    # Generic policy violation
    # ------------------------------------------------------------------

    def _patch_generic_violation(
        self,
        component: str,
        node: "Node | None",
        finding: dict,
        finding_id: str,
        priority: str,
    ) -> list[RemediationArtefact]:
        desc = finding.get("description", "")
        title = finding.get("title", "")
        prompt_node = self._prompt_by_agent.get(component)
        location = _prompt_location(prompt_node)

        patch_text = (
            f"## Policy Compliance\n"
            f"The following behaviour is prohibited: {(desc or title)[:200]}\n"
            f"Ensure all responses comply with the application's stated policy."
        )
        return [RemediationArtefact(
            finding_ids=[finding_id],
            component=component,
            component_type=_node_type(node) if node else "unknown",
            artefact_type=RemediationArtefactType.SYSTEM_PROMPT_PATCH,
            priority=priority,
            patch_location=location or None,
            patch_section="Policy Compliance",
            patch_text=patch_text,
            rationale=desc or title,
        )]

    # ------------------------------------------------------------------
    # LLM patch generation helpers
    # ------------------------------------------------------------------

    async def _llm_patch_async(
        self,
        section: str,
        violation: str,
        prompt_excerpt: str,
        intent_purpose: str,
    ) -> str:
        """Generate contextual system prompt patch text using the LLM (async).

        Returns empty string on failure so caller falls back to template.
        """
        if self._llm is None:
            return ""
        try:
            prompt = _LLM_PATCH_USER.format(
                agent_purpose=intent_purpose or "an AI assistant",
                violation=violation[:300],
                prompt_excerpt=prompt_excerpt[:600],
                section=section,
            )
            result = ""
            async for chunk in self._llm.complete_stream(  # type: ignore[union-attr]
                prompt,
                system=_LLM_PATCH_SYSTEM,
                label="behavior:remediation_patch",
            ):
                result += chunk
            return result.strip()
        except Exception as exc:
            _log.debug("RemediationSynthesizer._llm_patch_async: failed: %s", exc)
            return ""

    def _llm_patch(
        self,
        section: str,
        violation: str,
        prompt_excerpt: str,
        intent_purpose: str,
    ) -> str:
        """Sync shim — only works outside a running event loop (e.g. redteam CLI).

        Callers inside an async context should use synthesize_async() instead
        so that _llm_patch_async() is awaited properly.
        """
        if self._llm is None:
            return ""
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                return ""
            return loop.run_until_complete(
                self._llm_patch_async(section, violation, prompt_excerpt, intent_purpose)
            )
        except Exception as exc:
            _log.debug("RemediationSynthesizer._llm_patch: failed: %s", exc)
            return ""

    async def _llm_privilege_patch_async(
        self,
        agent_name: str,
        tool_name: str,
        tool_desc: str,
        privilege_scope: str,
        risk: str,
    ) -> str:
        """Generate contextual privilege-restriction patch using the LLM (async)."""
        if self._llm is None:
            return ""
        try:
            prompt = _LLM_PRIVILEGE_PATCH_USER.format(
                agent_name=agent_name,
                agent_purpose="",
                tool_name=tool_name,
                tool_desc=tool_desc[:200],
                privilege_scope=privilege_scope,
                risk=risk,
            )
            result = ""
            async for chunk in self._llm.complete_stream(  # type: ignore[union-attr]
                prompt,
                system=_LLM_PRIVILEGE_PATCH_SYSTEM,
                label="behavior:privilege_patch",
            ):
                result += chunk
            return result.strip()
        except Exception as exc:
            _log.debug("RemediationSynthesizer._llm_privilege_patch_async: failed: %s", exc)
            return ""

    def _llm_privilege_patch(
        self,
        agent_name: str,
        tool_name: str,
        tool_desc: str,
        privilege_scope: str,
        risk: str,
    ) -> str:
        """Sync shim — only works outside a running event loop."""
        if self._llm is None:
            return ""
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                return ""
            return loop.run_until_complete(
                self._llm_privilege_patch_async(agent_name, tool_name, tool_desc, privilege_scope, risk)
            )
        except Exception as exc:
            _log.debug("RemediationSynthesizer._llm_privilege_patch: failed: %s", exc)
            return ""
