"""Cognitive Policy ↔ SBOM static cross-checker.

Compares a parsed CognitivePolicy against an AiSbomDocument and returns a
PolicyCheckResult describing both gaps and satisfied controls with evidence.
The checker never raises — missing nodes produce gaps rather than exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nuguard.common.logging import get_logger
from nuguard.models.policy import CognitivePolicy
from nuguard.sbom.models import AiSbomDocument, Node, RateLimitDetail
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
        "DATASTORE nodes in the SBOM should carry data_classification, classified table, "
        "classified field, or sensitive field metadata.",
    ),
    "CHECK-004": (
        "Rate Limit Instrumentation",
        "Policy defines rate limits for API endpoints. "
        "API_ENDPOINT nodes in the SBOM should carry rate_limited and/or "
        "rate_limit_detail metadata so the limits can be verified against the "
        "deployed configuration.",
    ),
    "CHECK-005": (
        "Auth Node for HITL",
        "Policy defines HITL triggers. Human-in-the-loop enforcement typically "
        "requires an authentication mechanism; the SBOM should contain AUTH nodes.",
    ),
    "CHECK-006": (
        "High-Privilege Scope Without Guardrail",
        "PRIVILEGE nodes with admin, db_write, or filesystem_write scope grant "
        "elevated capabilities that should be protected by GUARDRAIL nodes. "
        "Without guardrail enforcement, privileged operations are reachable via "
        "prompt injection or policy bypass.",
    ),
    "CHECK-007": (
        "Unauthenticated Tool Access to PII Datastore",
        "TOOL nodes with no_auth_required=True can be invoked without authentication. "
        "When PII-classified DATASTORE nodes exist and policy restricts PII disclosure, "
        "these tools create a path to access sensitive data without identity verification.",
    ),
    "CHECK-008": (
        "Allowed-Topic Agent Grounding",
        "Policy defines allowed topics. The AGENT node description and system PROMPT "
        "nodes should contain explicit topic-scoping instructions so the model refuses "
        "out-of-scope requests.",
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
    # Concrete remediation recommendation
    remediation: str = ""


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
    input_tokens_used: int = 0
    output_tokens_used: int = 0

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


def _as_list(value: Any) -> list[Any]:
    """Return *value* as a list, preserving existing list-like values."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _metadata_value(node: Node, field_name: str) -> Any:
    """Return a typed SBOM metadata value, falling back to legacy extras."""
    value = getattr(node.metadata, field_name, None)
    if value is not None:
        return value
    return node.metadata.extras.get(field_name)


def _data_classification_evidence(node: Node) -> list[str]:
    """Return SBOM 1.5 datastore classification evidence strings for *node*."""
    evidence: list[str] = []

    classifications = _as_list(_metadata_value(node, "data_classification"))
    if classifications:
        evidence.append(
            "data_classification="
            f"{', '.join(str(item) for item in classifications)}"
        )

    classified_tables = _as_list(_metadata_value(node, "classified_tables"))
    if classified_tables:
        evidence.append(
            "classified_tables="
            f"{', '.join(str(item) for item in classified_tables)}"
        )

    classified_fields = _metadata_value(node, "classified_fields")
    if isinstance(classified_fields, dict) and classified_fields:
        rendered_fields = []
        for table, fields in classified_fields.items():
            field_names = ", ".join(str(field) for field in _as_list(fields))
            rendered_fields.append(f"{table}: [{field_names}]")
        evidence.append("classified_fields=" + "; ".join(rendered_fields))

    for field_name in ("pii_fields", "phi_fields", "pfi_fields"):
        fields = _as_list(_metadata_value(node, field_name))
        if fields:
            evidence.append(
                f"{field_name}="
                f"{', '.join(str(field) for field in fields)}"
            )

    return evidence


def _rate_limit_detail_summary(detail: RateLimitDetail | dict[str, Any]) -> str:
    """Render a compact summary of structured rate-limit detail."""
    if isinstance(detail, RateLimitDetail):
        data = detail.model_dump(exclude_none=True)
    elif isinstance(detail, dict):
        data = {k: v for k, v in detail.items() if v is not None}
    else:
        data = {}
    if not data:
        return "{}"
    return ", ".join(f"{key}={value!r}" for key, value in sorted(data.items()))


def _rate_limit_evidence(node: Node) -> list[str]:
    """Return SBOM 1.5 API rate-limit evidence strings for *node*."""
    evidence: list[str] = []

    rate_limited = _metadata_value(node, "rate_limited")
    if rate_limited is True:
        evidence.append("rate_limited=True")

    rate_limit_detail = _metadata_value(node, "rate_limit_detail")
    if rate_limit_detail:
        evidence.append(
            "rate_limit_detail={"
            f"{_rate_limit_detail_summary(rate_limit_detail)}"
            "}"
        )

    legacy_rate_limit = node.metadata.extras.get("rate_limit")
    if legacy_rate_limit is not None:
        evidence.append(f"legacy rate_limit={legacy_rate_limit!r}")

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
                        lack classification or sensitive-field metadata.
    CHECK-004 (low)     rate_limits present but API_ENDPOINT nodes have no
                        rate_limited or rate_limit_detail metadata.
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
                remediation=(
                    "Add an InputGuardrail or OutputGuardrail component to your agent "
                    "framework. In LangChain/LangGraph, implement a callback handler that "
                    "intercepts dispute, fraud, and high-value transfer requests and routes "
                    "them to a human queue. In ADK or OpenAI Agents SDK, configure a "
                    "guardrail tool that triggers escalation. After adding the component, "
                    "re-run 'nuguard sbom generate' so the GUARDRAIL node appears in the SBOM."
                ),
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
                        remediation=(
                            f"Ensure a TOOL node in the SBOM corresponds to the restricted "
                            f"action {action!r}. Options: (1) rename the tool so its name "
                            "contains a keyword from the action description; "
                            "(2) update the policy restricted_action text to match the "
                            "existing tool name; or (3) add explicit authorization checks "
                            "(e.g. session-owner assertion) inside the tool implementation "
                            "and re-run 'nuguard sbom generate' to refresh the SBOM."
                        ),
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
                remediation=(
                    "Register the tools that enforce your restricted actions so they "
                    "appear as TOOL nodes in the SBOM. Each restricted action should map "
                    "to a named tool with authorization checks in its implementation. "
                    "Re-run 'nuguard sbom generate' after adding the tools."
                ),
            ))

    # ---- CHECK-003: Data Classification Metadata ----------------------------
    if policy.data_classification:
        if datastore_nodes:
            for ds in datastore_nodes:
                data_evidence = _data_classification_evidence(ds)
                if data_evidence:
                    result.passed.append(PolicyControl(
                        check_id="CHECK-003",
                        name=_check_name("CHECK-003"),
                        description=_check_description("CHECK-003"),
                        policy_section="data_classification",
                        evidence=[
                            f"DATASTORE {ds.name!r} has classification metadata: {item}"
                            for item in data_evidence
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
                            f"DATASTORE node {ds.name!r} has no classification, "
                            "classified-field, PII, PHI, or PFI metadata."
                        ),
                        policy_section="data_classification",
                        sbom_component=ds.name,
                        severity="medium",
                        searched=[
                            "Checked data_classification, classified_tables, "
                            "classified_fields, pii_fields, phi_fields, and "
                            f"pfi_fields on DATASTORE {ds.name!r}: empty"
                        ],
                        remediation=(
                            f"Annotate the {ds.name!r} datastore in source code with "
                            "data-classification markers (e.g. column-level 'pii=True' "
                            "attributes in SQLAlchemy models, or NuGuard-comment annotations "
                            "on the connection string). Re-run 'nuguard sbom generate' so "
                            "pii_fields, phi_fields, or data_classification metadata is "
                            "captured in the SBOM node."
                        ),
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
                remediation=(
                    "Ensure data access objects (SQLAlchemy sessions, Postgres connections, "
                    "Redis clients, etc.) are imported and used within the scanned source "
                    "tree. Re-run 'nuguard sbom generate' so DATASTORE nodes are detected "
                    "and their PII/PHI field annotations are captured."
                ),
            ))

    # ---- CHECK-004: Rate Limit Instrumentation ------------------------------
    if policy.rate_limits:
        if api_nodes:
            for ep in api_nodes:
                rate_limit_evidence = _rate_limit_evidence(ep)
                if rate_limit_evidence:
                    result.passed.append(PolicyControl(
                        check_id="CHECK-004",
                        name=_check_name("CHECK-004"),
                        description=_check_description("CHECK-004"),
                        policy_section="rate_limits",
                        evidence=[
                            f"API_ENDPOINT {ep.name!r} has {item}"
                            for item in rate_limit_evidence
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
                            f"{ep.name!r} has no rate_limited or rate_limit_detail "
                            "metadata in the SBOM."
                        ),
                        policy_section="rate_limits",
                        sbom_component=ep.name,
                        severity="low",
                        searched=[
                            "Checked rate_limited, rate_limit_detail, and legacy "
                            f"rate_limit metadata on API_ENDPOINT {ep.name!r}: not set"
                        ],
                        remediation=(
                            f"Instrument {ep.name!r} with rate limiting. "
                            "Options: (a) Add 'slowapi' or 'flask-limiter' decorators "
                            "with the policy-defined limits; (b) configure Azure API "
                            "Management / AWS API Gateway throttling policies; "
                            "(c) add a 'rate_limited: true' annotation comment above the "
                            "route handler so NuGuard captures it in the SBOM. "
                            "Re-run 'nuguard sbom generate' after the change."
                        ),
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
                remediation=(
                    "Ensure route/handler functions are present in the scanned source "
                    "tree and decorated with the appropriate HTTP framework decorators "
                    "(@app.route, @router.get, etc.) so NuGuard can detect them as "
                    "API_ENDPOINT nodes. Re-run 'nuguard sbom generate'."
                ),
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
                remediation=(
                    "Add an authentication component (e.g. JWT middleware, OAuth2 handler, "
                    "session manager) to the application so NuGuard can detect it as an "
                    "AUTH node. Without identity verification, HITL triggers cannot reliably "
                    "associate an escalation request with a specific customer. "
                    "Re-run 'nuguard sbom generate' after adding the auth layer."
                ),
            ))

    # ---- CHECK-006: High-Privilege Scope Without Guardrail ------------------
    _HIGH_RISK_SCOPES = {"admin", "db_write", "filesystem_write"}
    privilege_nodes = _nodes_of_type(doc, ComponentType.PRIVILEGE)
    high_risk_priv = [
        n for n in privilege_nodes
        if (n.metadata.privilege_scope or "").lower() in _HIGH_RISK_SCOPES
    ]
    if high_risk_priv:
        if guardrail_nodes:
            result.passed.append(PolicyControl(
                check_id="CHECK-006",
                name=_check_name("CHECK-006"),
                description=_check_description("CHECK-006"),
                policy_section="restricted_actions",
                evidence=[
                    f"GUARDRAIL node {g.name!r} protects agent with PRIVILEGE nodes: "
                    + ", ".join(f"{p.name!r} (scope={p.metadata.privilege_scope})"
                                for p in high_risk_priv)
                    for g in guardrail_nodes
                ],
                evidence_source="sbom_node",
            ))
        else:
            scope_list = ", ".join(
                f"{n.name!r} (scope={n.metadata.privilege_scope})" for n in high_risk_priv
            )
            result.gaps.append(PolicyGap(
                check_id="CHECK-006",
                name=_check_name("CHECK-006"),
                description=_check_description("CHECK-006"),
                message=(
                    f"High-risk PRIVILEGE nodes detected ({scope_list}) but no "
                    "GUARDRAIL nodes exist to intercept privileged operations before "
                    "they execute. An attacker using prompt injection could invoke these "
                    "capabilities without policy enforcement."
                ),
                policy_section="restricted_actions",
                severity="high",
                searched=[
                    f"PRIVILEGE nodes with high-risk scope: {scope_list}",
                    "GUARDRAIL nodes: none found",
                ],
                remediation=(
                    "Add a guardrail component (InputGuardrail / OutputGuardrail) that "
                    "specifically intercepts tool calls targeting admin, db_write, or "
                    "filesystem_write operations and verifies intent and authorization "
                    "before execution. In LangGraph, wrap the privileged tools in a "
                    "ToolNode with a pre-call authorization check. Re-run "
                    "'nuguard sbom generate' after adding the guardrail."
                ),
            ))

    # ---- CHECK-007: Unauthenticated Tool Access to PII Datastore ------------
    _pii_policy_keywords = {"pii", "account", "transaction", "personal", "sensitive"}
    policy_restricts_pii = any(
        any(kw in t.lower() for kw in _pii_policy_keywords)
        for t in (policy.restricted_topics + policy.data_classification)
    )
    if policy_restricts_pii:
        pii_datastores = [
            n for n in datastore_nodes
            if n.metadata.data_classification
            or n.metadata.pii_fields
            or n.metadata.phi_fields
        ]
        no_auth_tools = [n for n in tool_nodes if n.metadata.no_auth_required]
        if pii_datastores and no_auth_tools:
            result.gaps.append(PolicyGap(
                check_id="CHECK-007",
                name=_check_name("CHECK-007"),
                description=_check_description("CHECK-007"),
                message=(
                    f"{len(no_auth_tools)} TOOL node(s) have no_auth_required=True "
                    f"while {len(pii_datastores)} DATASTORE node(s) carry PII/PHI "
                    "classification. These tools can be invoked without identity "
                    "verification, creating an unauthenticated path to sensitive data."
                ),
                policy_section="restricted_topics",
                severity="high",
                searched=[
                    "TOOL nodes with no_auth_required=True: "
                    + ", ".join(t.name for t in no_auth_tools[:5])
                    + (" …" if len(no_auth_tools) > 5 else ""),
                    "PII/PHI DATASTORE nodes: "
                    + ", ".join(
                        f"{d.name!r} (pii_fields={list(d.metadata.pii_fields or [])[:3]})"
                        for d in pii_datastores
                    ),
                ],
                remediation=(
                    "Add authentication guards to each tool that accesses PII datastores. "
                    "In the tool implementation, verify the caller's identity token before "
                    "querying sensitive tables. Remove 'no_auth_required=True' annotations "
                    "from tools that touch the following datastores: "
                    + ", ".join(d.name for d in pii_datastores)
                    + ". Consider adding an AUTH node (JWT/OAuth2) and referencing it from "
                    "these tools so the SBOM reflects the authentication boundary."
                ),
            ))
        elif pii_datastores and not no_auth_tools:
            result.passed.append(PolicyControl(
                check_id="CHECK-007",
                name=_check_name("CHECK-007"),
                description=_check_description("CHECK-007"),
                policy_section="restricted_topics",
                evidence=[
                    f"All TOOL nodes require authentication; PII DATASTORE "
                    f"{d.name!r} is protected."
                    for d in pii_datastores
                ],
                evidence_source="sbom_node",
            ))

    # ---- CHECK-008: Allowed-Topic Agent Grounding ---------------------------
    if policy.allowed_topics:
        agent_nodes = _nodes_of_type(doc, ComponentType.AGENT)
        prompt_nodes = _nodes_of_type(doc, ComponentType.PROMPT)
        topic_keywords: list[str] = []
        for topic in policy.allowed_topics:
            topic_keywords.extend(w for w in topic.lower().split() if len(w) > 4)

        grounding_evidence: list[str] = []
        for a in agent_nodes:
            desc = (a.metadata.description or "").lower()
            if any(kw in desc for kw in topic_keywords):
                grounding_evidence.append(
                    f"AGENT {a.name!r} description contains topic-aligned content"
                )
        for p in prompt_nodes:
            desc = (p.metadata.description or "").lower()
            if any(kw in desc for kw in topic_keywords):
                grounding_evidence.append(
                    f"PROMPT {p.name!r} contains topic-aligned content"
                )

        if grounding_evidence:
            result.passed.append(PolicyControl(
                check_id="CHECK-008",
                name=_check_name("CHECK-008"),
                description=_check_description("CHECK-008"),
                policy_section="allowed_topics",
                evidence=grounding_evidence[:5],
                evidence_source="prompt",
            ))
        else:
            result.gaps.append(PolicyGap(
                check_id="CHECK-008",
                name=_check_name("CHECK-008"),
                description=_check_description("CHECK-008"),
                message=(
                    f"Policy defines {len(policy.allowed_topics)} allowed topic(s) but "
                    "no AGENT or PROMPT node description contains topic-scoping language. "
                    "Without explicit grounding instructions the model may answer "
                    "out-of-scope questions."
                ),
                policy_section="allowed_topics",
                severity="medium",
                searched=[
                    f"Checked {len(agent_nodes)} AGENT node(s) and "
                    f"{len(prompt_nodes)} PROMPT node(s) for topic keywords: "
                    + ", ".join(topic_keywords[:8])
                    + (" …" if len(topic_keywords) > 8 else "")
                ],
                remediation=(
                    "Add explicit topic-restriction language to the agent's system prompt. "
                    "Example: 'You are a banking assistant for Pinnacle Bank. You only help "
                    "with: account inquiries, fund transfers, bill payments, loan status, "
                    "credit card inquiries, and branch/ATM location. Politely refuse "
                    "requests outside this scope.' Re-run 'nuguard sbom generate' so the "
                    "updated prompt content appears in PROMPT node descriptions."
                ),
            ))

    return result
