"""Cognitive Policy ↔ SBOM static cross-checker.

Compares a parsed CognitivePolicy against an AiSbomDocument and returns a
list of PolicyGap dataclass instances describing structural mismatches.  The
checker never raises — missing nodes produce gaps rather than exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nuguard.common.logging import get_logger
from nuguard.models.policy import CognitivePolicy
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.types import ComponentType

_log = get_logger(__name__)


@dataclass
class PolicyGap:
    """A single gap between a policy directive and SBOM evidence."""

    check_id: str
    message: str
    policy_section: str
    sbom_component: str = ""
    severity: str = "medium"  # "critical" | "high" | "medium" | "low"


def _nodes_of_type(doc: AiSbomDocument, *types: ComponentType) -> list:
    """Return all nodes whose component_type is in *types*."""
    return [n for n in doc.nodes if n.component_type in types]


def _fuzzy_match(needle: str, haystack: str) -> bool:
    """Return True when *needle* is a substring of *haystack* or vice-versa (case-insensitive)."""
    n = needle.lower()
    h = haystack.lower()
    return n in h or h in n


def check_policy_against_sbom(
    policy: CognitivePolicy, doc: AiSbomDocument
) -> list[PolicyGap]:
    """Cross-check a CognitivePolicy against an AiSbomDocument.

    Checks
    ------
    CHECK-001 (high)    hitl_triggers present but no GUARDRAIL node in SBOM.
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
        List of PolicyGap instances (may be empty).
    """
    gaps: list[PolicyGap] = []

    guardrail_nodes = _nodes_of_type(doc, ComponentType.GUARDRAIL)
    auth_nodes = _nodes_of_type(doc, ComponentType.AUTH)
    tool_nodes = _nodes_of_type(doc, ComponentType.TOOL)
    datastore_nodes = _nodes_of_type(doc, ComponentType.DATASTORE)
    api_nodes = _nodes_of_type(doc, ComponentType.API_ENDPOINT)

    # ---- CHECK-001 --------------------------------------------------------
    if policy.hitl_triggers and not guardrail_nodes:
        gaps.append(
            PolicyGap(
                check_id="CHECK-001",
                message=(
                    "The policy defines HITL triggers but the SBOM contains no "
                    "GUARDRAIL nodes that could enforce them."
                ),
                policy_section="hitl_triggers",
                sbom_component="",
                severity="high",
            )
        )

    # ---- CHECK-002 --------------------------------------------------------
    if policy.restricted_actions and tool_nodes:
        tool_names = [n.name for n in tool_nodes]
        for action in policy.restricted_actions:
            matched = any(_fuzzy_match(action, name) for name in tool_names)
            if not matched:
                gaps.append(
                    PolicyGap(
                        check_id="CHECK-002",
                        message=(
                            f"Restricted action {action!r} does not match any TOOL node "
                            "name in the SBOM. Verify the action name is correct."
                        ),
                        policy_section="restricted_actions",
                        sbom_component="",
                        severity="medium",
                    )
                )
    elif policy.restricted_actions and not tool_nodes:
        # No tools at all — report once
        gaps.append(
            PolicyGap(
                check_id="CHECK-002",
                message=(
                    "Policy defines restricted_actions but the SBOM contains no "
                    "TOOL nodes to enforce them against."
                ),
                policy_section="restricted_actions",
                sbom_component="",
                severity="medium",
            )
        )

    # ---- CHECK-003 --------------------------------------------------------
    if policy.data_classification and datastore_nodes:
        for ds in datastore_nodes:
            dc = ds.metadata.data_classification or []
            extras_dc = ds.metadata.extras.get("data_classification") or []
            all_dc = list(dc) + list(extras_dc)
            if not all_dc:
                gaps.append(
                    PolicyGap(
                        check_id="CHECK-003",
                        message=(
                            f"Policy declares data classification requirements but "
                            f"DATASTORE node {ds.name!r} has no pii/data-classification metadata."
                        ),
                        policy_section="data_classification",
                        sbom_component=ds.name,
                        severity="medium",
                    )
                )
    elif policy.data_classification and not datastore_nodes:
        gaps.append(
            PolicyGap(
                check_id="CHECK-003",
                message=(
                    "Policy defines data_classification requirements but the SBOM "
                    "contains no DATASTORE nodes."
                ),
                policy_section="data_classification",
                sbom_component="",
                severity="medium",
            )
        )

    # ---- CHECK-004 --------------------------------------------------------
    if policy.rate_limits and api_nodes:
        for ep in api_nodes:
            rate_limit = ep.metadata.extras.get("rate_limit")
            if rate_limit is None:
                gaps.append(
                    PolicyGap(
                        check_id="CHECK-004",
                        message=(
                            f"Policy defines rate_limits but API_ENDPOINT node "
                            f"{ep.name!r} has no rate_limit attribute in the SBOM."
                        ),
                        policy_section="rate_limits",
                        sbom_component=ep.name,
                        severity="low",
                    )
                )
    elif policy.rate_limits and not api_nodes:
        gaps.append(
            PolicyGap(
                check_id="CHECK-004",
                message=(
                    "Policy defines rate_limits but the SBOM contains no "
                    "API_ENDPOINT nodes."
                ),
                policy_section="rate_limits",
                sbom_component="",
                severity="low",
            )
        )

    # ---- CHECK-005 --------------------------------------------------------
    if policy.hitl_triggers and not auth_nodes:
        gaps.append(
            PolicyGap(
                check_id="CHECK-005",
                message=(
                    "Policy defines HITL triggers but the SBOM contains no AUTH "
                    "nodes. Human-in-the-loop enforcement typically requires an "
                    "authentication mechanism."
                ),
                policy_section="hitl_triggers",
                sbom_component="",
                severity="medium",
            )
        )

    _log.debug("check_policy_against_sbom found %d gaps", len(gaps))
    return gaps
