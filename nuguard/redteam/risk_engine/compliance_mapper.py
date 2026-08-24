"""Maps goal types to compliance framework references.

Thin wrapper over the shared ``nuguard.common.control_mappings`` tables so redteam
cites the same 2026 short-code format as analysis/ and behavior/. Individual
scenario builders may set a more specific ``owasp_llm_ref`` / ``owasp_asi_ref`` /
``mitre_atlas_technique`` on the ``ExploitChain`` itself, which callers should
prefer over these goal-type-level fallbacks (see orchestrator._build_findings).
"""
from __future__ import annotations

from nuguard.common.control_mappings.atlas import atlas_refs_for_goal, atlas_technique_label
from nuguard.common.control_mappings.owasp import owasp_refs_for_goal
from nuguard.models.exploit_chain import GoalType


def owasp_llm_ref(goal_type: GoalType) -> str | None:
    """Return the OWASP LLM Top 10 reference for the given goal type."""
    refs = owasp_refs_for_goal(goal_type).owasp_llm
    return ", ".join(refs) if refs else None


def owasp_asi_ref(goal_type: GoalType) -> str | None:
    """Return the OWASP Agentic Top 10 reference for the given goal type."""
    refs = owasp_refs_for_goal(goal_type).owasp_agentic
    return ", ".join(refs) if refs else None


def mitre_atlas_ref(goal_type: GoalType) -> str | None:
    """Return the MITRE ATLAS technique label for the given goal type, or None."""
    refs = atlas_refs_for_goal(goal_type)
    if not refs:
        return None
    return atlas_technique_label(refs[0][0])
