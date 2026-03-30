"""Cognitive Policy ↔ SBOM static cross-checker.

Compares a parsed CognitivePolicy against an AiSbomDocument and returns a
PolicyCheckResult describing both gaps and satisfied controls with evidence.
The checker never raises — missing nodes produce gaps rather than exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nuguard.common.logging import get_logger
from nuguard.models.policy import CognitivePolicy
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.types import ComponentType

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Check metadata — name and description for every check ID
# ---------------------------------------------------------------------------

_CHECK_INFO: dict[str, tuple[str, str]] = {
    "CHECK-001": (
        "HITL Enforcement",
        "Policy defines human-in-the-loop triggers. "
        "The SBOM should contain GUARDRAIL nodes (e.g. InputGuardrail, OutputGuardrail) "
        "or PROMPT nodes with explicit escalation instructions to enforce them.",
    ),
    "CHECK-002": (
        "Restricted Action Coverage",
        "Each policy-restricted action should correspond to a TOOL node in the SBOM "
        "so the restriction can be enforced and audited at the tool-call boundary.",
    ),
    "CHECK-003": (
        "Data Classification Metadata",
        "Policy declares data classification requirements (PHI, PII, internal). "
        "DATASTORE nodes in the SBOM should carry matching data_classification metadata.",
    ),
    "CHECK-004": (
        "Rate Limit Instrumentation",
        "Policy defines rate limits for API endpoints. "
        "API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so "
        "the limits can be verified against the deployed configuration.",
    ),
    "CHECK-005": (
        "Auth Node for HITL",
        "Policy defines HITL triggers. Human-in-the-loop enforcement typically "
        "requires an authentication mechanism; the SBOM should contain AUTH nodes.",
    ),
}


def _check_name(check_id: str) -> str:
    return _CHECK_INFO.get(check_id, ("Unknown check", ""))[0]


def _check_description(check_id: str) -> str:
    return _CHECK_INFO.get(check_id, ("", "No description available."))[1]


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

@dataclass
class PolicyGap:
    """A single gap between a policy directive and SBOM evidence."""

    check_id: str
    name: str
    description: str
    message: str
    policy_section: str
    sbom_component: str = ""
    severity: str = "medium"  # "critical" | "high" | "medium" | "low"
    # Evidence searched but not found (shown in verbose mode)
    searched: list[str] = field(default_factory=list)
    # Partial evidence found in PROMPT nodes (downgrade hint)
    prompt_evidence: list[str] = field(default_factory=list)


@dataclass
class PolicyControl:
    """A policy control that is satisfied — evidence of compliance."""

    check_id: str
    name: str
    description: str
    policy_section: str
    # Human-readable evidence strings (node names, prompt excerpts, etc.)
    evidence: list[str] = field(default_factory=list)
    # Where the evidence came from: "sbom_node" | "prompt" | "inferred"
    evidence_source: str = "sbom_node"


@dataclass
class PolicyCheckResult:
    """Full result of a policy ↔ SBOM cross-check."""

    gaps: list[PolicyGap] = field(default_factory=list)
    passed: list[PolicyControl] = field(default_factory=list)

    @property
    def all_checks(self) -> list[PolicyGap | PolicyControl]:
        """All checks ordered: passed first, then gaps — for verbose display."""
        return [*self.passed, *self.gaps]  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nodes_of_type(doc: AiSbomDocument, *types: ComponentType) -> list:
    """Return all nodes whose component_type is in *types*."""
    return [n for n in doc.nodes if n.component_type in types]


def _fuzzy_match(needle: str, haystack: str) -> bool:
    """Return True when *needle* is a substring of *haystack* or vice-versa (case-insensitive)."""
    n = needle.lower()
    h = haystack.lower()
    return n in h or h in n


def _prompt_evidence_for(triggers: list[str], doc: AiSbomDocument) -> list[str]:
    """Search PROMPT nodes for text related to *triggers*.

    Returns a list of evidence strings of the form
    ``"<node_name>: '<excerpt>'"`` for any prompt whose content
    contains a word from a trigger phrase.
    """
    prompt_nodes = _nodes_of_type(doc, ComponentType.PROMPT)
    if not prompt_nodes:
        return []

    evidence: list[str] = []
    # Keywords extracted from each trigger
    trigger_keywords: set[str] = set()
    for t in triggers:
        for word in t.lower().split():
            if len(word) > 4:  # skip short stop-words
                trigger_keywords.add(word.rstrip(".,;:"))

    for node in prompt_nodes:
        content: str = (
            node.metadata.extras.get("content", "")
            or node.metadata.extras.get("system_prompt", "")
            or node.metadata.system_prompt_excerpt
            or ""
        )
        if not content:
            continue
        content_lower = content.lower()
        matched_keywords = [kw for kw in trigger_keywords if kw in content_lower]
        if matched_keywords:
            excerpt = content[:120].replace("\n", " ").strip()
            evidence.append(
                f"{node.name!r}: '…{excerpt}…' "
                f"[matched: {', '.join(matched_keywords[:3])}]"
            )
    return evidence


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_policy_against_sbom(
    policy: CognitivePolicy, doc: AiSbomDocument
) -> PolicyCheckResult:
    """Cross-check a CognitivePolicy against an AiSbomDocument.

    Checks
    ------
    CHECK-001 (high)    hitl_triggers present → looks for GUARDRAIL nodes;
                        falls back to PROMPT node evidence when no GUARDRAIL found.
    CHECK-002 (medium)  restricted_action names a tool not found in SBOM.
    CHECK-003 (medium)  data_classification entries present but DATASTORE nodes
                        lack PII metadata.
    CHECK-004 (low)     rate_limits present but API_ENDPOINT nodes have no
                        rate_limit attribute.
    CHECK-005 (medium)  hitl_triggers present but no AUTH node in SBOM.

    Args:
        policy: Parsed CognitivePolicy.
        doc: AI-SBOM document to check against.

    Returns:
        PolicyCheckResult with both gaps and passing controls (with evidence).
    """
    result = PolicyCheckResult()

    guardrail_nodes = _nodes_of_type(doc, ComponentType.GUARDRAIL)
    auth_nodes = _nodes_of_type(doc, ComponentType.AUTH)
    tool_nodes = _nodes_of_type(doc, ComponentType.TOOL)
    datastore_nodes = _nodes_of_type(doc, ComponentType.DATASTORE)
    api_nodes = _nodes_of_type(doc, ComponentType.API_ENDPOINT)

    # ---- CHECK-001: HITL Enforcement ----------------------------------------
    if policy.hitl_triggers:
        if guardrail_nodes:
            result.passed.append(PolicyControl(
                check_id="CHECK-001",
                name=_check_name("CHECK-001"),
                description=_check_description("CHECK-001"),
                policy_section="hitl_triggers",
                evidence=[
                    f"GUARDRAIL node: {n.name!r} "
                    f"(confidence={n.confidence:.2f}, "
                    f"file={n.evidence[0].location.path if n.evidence else 'unknown'})"
                    for n in guardrail_nodes
                ],
                evidence_source="sbom_node",
            ))
        else:
            # No guardrail — check prompt nodes for partial evidence
            prompt_ev = _prompt_evidence_for(policy.hitl_triggers, doc)
            searched = [
                f"GUARDRAIL nodes: none found in SBOM ({len(doc.nodes)} total nodes)",
            ]
            if prompt_ev:
                searched.append(
                    f"PROMPT nodes: found {len(prompt_ev)} prompt(s) with related content"
                )
            result.gaps.append(PolicyGap(
                check_id="CHECK-001",
                name=_check_name("CHECK-001"),
                description=_check_description("CHECK-001"),
                message=(
                    "The policy defines HITL triggers but the SBOM contains no "
                    "GUARDRAIL nodes that could enforce them."
                    + (
                        f" Found {len(prompt_ev)} PROMPT node(s) with related content "
                        "(prompt-level instructions are weaker than guardrail enforcement)."
                        if prompt_ev else ""
                    )
                ),
                policy_section="hitl_triggers",
                severity="high" if not prompt_ev else "medium",
                searched=searched,
                prompt_evidence=prompt_ev,
            ))

    # ---- CHECK-002: Restricted Action Coverage ------------------------------
    if policy.restricted_actions:
        if tool_nodes:
            tool_names = [n.name for n in tool_nodes]
            for action in policy.restricted_actions:
                matched_tools = [
                    n for n in tool_nodes if _fuzzy_match(action, n.name)
                ]
                if matched_tools:
                    result.passed.append(PolicyControl(
                        check_id="CHECK-002",
                        name=_check_name("CHECK-002"),
                        description=_check_description("CHECK-002"),
                        policy_section="restricted_actions",
                        evidence=[
                            f"Action {action!r} matched TOOL node {n.name!r} "
                            f"(file={n.evidence[0].location.path if n.evidence else 'unknown'})"
                            for n in matched_tools
                        ],
                        evidence_source="sbom_node",
                    ))
                else:
                    result.gaps.append(PolicyGap(
                        check_id="CHECK-002",
                        name=_check_name("CHECK-002"),
                        description=_check_description("CHECK-002"),
                        message=(
                            f"Restricted action {action!r} does not match any TOOL node "
                            "name in the SBOM. Verify the action name is correct."
                        ),
                        policy_section="restricted_actions",
                        severity="medium",
                        searched=[f"Checked {len(tool_names)} TOOL nodes: {', '.join(tool_names[:5])}"
                                  + (" …" if len(tool_names) > 5 else "")],
                    ))
        else:
            result.gaps.append(PolicyGap(
                check_id="CHECK-002",
                name=_check_name("CHECK-002"),
                description=_check_description("CHECK-002"),
                message=(
                    "Policy defines restricted_actions but the SBOM contains no "
                    "TOOL nodes to enforce them against."
                ),
                policy_section="restricted_actions",
                severity="medium",
                searched=["TOOL nodes: none found in SBOM"],
            ))

    # ---- CHECK-003: Data Classification Metadata ----------------------------
    if policy.data_classification:
        if datastore_nodes:
            for ds in datastore_nodes:
                dc = list(ds.metadata.data_classification or [])
                extras_dc = list(ds.metadata.extras.get("data_classification") or [])
                all_dc = dc + extras_dc
                if all_dc:
                    result.passed.append(PolicyControl(
                        check_id="CHECK-003",
                        name=_check_name("CHECK-003"),
                        description=_check_description("CHECK-003"),
                        policy_section="data_classification",
                        evidence=[
                            f"DATASTORE {ds.name!r} has classification metadata: "
                            f"{', '.join(str(x) for x in all_dc)}"
                        ],
                        evidence_source="sbom_node",
                    ))
                else:
                    result.gaps.append(PolicyGap(
                        check_id="CHECK-003",
                        name=_check_name("CHECK-003"),
                        description=_check_description("CHECK-003"),
                        message=(
                            f"Policy declares data classification requirements but "
                            f"DATASTORE node {ds.name!r} has no pii/data-classification metadata."
                        ),
                        policy_section="data_classification",
                        sbom_component=ds.name,
                        severity="medium",
                        searched=[f"Checked data_classification field on DATASTORE {ds.name!r}: empty"],
                    ))
        else:
            result.gaps.append(PolicyGap(
                check_id="CHECK-003",
                name=_check_name("CHECK-003"),
                description=_check_description("CHECK-003"),
                message=(
                    "Policy defines data_classification requirements but the SBOM "
                    "contains no DATASTORE nodes."
                ),
                policy_section="data_classification",
                severity="medium",
                searched=["DATASTORE nodes: none found in SBOM"],
            ))

    # ---- CHECK-004: Rate Limit Instrumentation ------------------------------
    if policy.rate_limits:
        if api_nodes:
            for ep in api_nodes:
                rate_limit = ep.metadata.extras.get("rate_limit")
                if rate_limit is not None:
                    result.passed.append(PolicyControl(
                        check_id="CHECK-004",
                        name=_check_name("CHECK-004"),
                        description=_check_description("CHECK-004"),
                        policy_section="rate_limits",
                        evidence=[
                            f"API_ENDPOINT {ep.name!r} has rate_limit={rate_limit!r}"
                        ],
                        evidence_source="sbom_node",
                    ))
                else:
                    result.gaps.append(PolicyGap(
                        check_id="CHECK-004",
                        name=_check_name("CHECK-004"),
                        description=_check_description("CHECK-004"),
                        message=(
                            f"Policy defines rate_limits but API_ENDPOINT node "
                            f"{ep.name!r} has no rate_limit attribute in the SBOM."
                        ),
                        policy_section="rate_limits",
                        sbom_component=ep.name,
                        severity="low",
                        searched=[f"Checked rate_limit attribute on API_ENDPOINT {ep.name!r}: not set"],
                    ))
        else:
            result.gaps.append(PolicyGap(
                check_id="CHECK-004",
                name=_check_name("CHECK-004"),
                description=_check_description("CHECK-004"),
                message=(
                    "Policy defines rate_limits but the SBOM contains no "
                    "API_ENDPOINT nodes."
                ),
                policy_section="rate_limits",
                severity="low",
                searched=["API_ENDPOINT nodes: none found in SBOM"],
            ))

    # ---- CHECK-005: Auth Node for HITL --------------------------------------
    if policy.hitl_triggers:
        if auth_nodes:
            result.passed.append(PolicyControl(
                check_id="CHECK-005",
                name=_check_name("CHECK-005"),
                description=_check_description("CHECK-005"),
                policy_section="hitl_triggers",
                evidence=[f"AUTH node: {n.name!r}" for n in auth_nodes],
                evidence_source="sbom_node",
            ))
        else:
            result.gaps.append(PolicyGap(
                check_id="CHECK-005",
                name=_check_name("CHECK-005"),
                description=_check_description("CHECK-005"),
                message=(
                    "Policy defines HITL triggers but the SBOM contains no AUTH "
                    "nodes. Human-in-the-loop enforcement typically requires an "
                    "authentication mechanism."
                ),
                policy_section="hitl_triggers",
                severity="medium",
                searched=["AUTH nodes: none found in SBOM"],
            ))

    _log.debug(
        "check_policy_against_sbom: %d gaps, %d passed",
        len(result.gaps),
        len(result.passed),
    )
    return result
