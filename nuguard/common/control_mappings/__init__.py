"""Shared OWASP GenAI/Agentic Top 10 (2026) and MITRE ATLAS control mappings.

The single source of truth for framework citations across analysis/, behavior/,
and redteam/, so every ``Finding`` cites the same short-code format regardless of
which package produced it. See ``owasp.py`` and ``atlas.py`` for the lookup tables.
"""
from __future__ import annotations

from nuguard.common.control_mappings.atlas import (
    ATLAS_BASE_URL,
    ATLAS_VERSION,
    BA_RULE_TO_ATLAS,
    BEHAVIOR_FINDING_TYPE_TO_ATLAS,
    GOAL_TYPE_TO_ATLAS,
    MITIGATIONS,
    NATIVE_CHECKS,
    NGA_TO_ATLAS,
    TACTICS,
    TECHNIQUES,
    atlas_refs_for_ba_rule,
    atlas_refs_for_finding_type,
    atlas_refs_for_goal,
    atlas_technique_label,
)
from nuguard.common.control_mappings.owasp import (
    BA_RULE_TO_OWASP,
    BEHAVIOR_FINDING_TYPE_TO_OWASP,
    GOAL_TYPE_TO_OWASP,
    NGA_TO_OWASP,
    RuleOwaspRefs,
    owasp_refs_for_ba_rule,
    owasp_refs_for_finding_type,
    owasp_refs_for_goal,
    owasp_refs_for_rule,
)

__all__ = [
    "ATLAS_BASE_URL",
    "ATLAS_VERSION",
    "BA_RULE_TO_ATLAS",
    "BA_RULE_TO_OWASP",
    "BEHAVIOR_FINDING_TYPE_TO_ATLAS",
    "BEHAVIOR_FINDING_TYPE_TO_OWASP",
    "GOAL_TYPE_TO_ATLAS",
    "GOAL_TYPE_TO_OWASP",
    "MITIGATIONS",
    "NATIVE_CHECKS",
    "NGA_TO_ATLAS",
    "NGA_TO_OWASP",
    "RuleOwaspRefs",
    "TACTICS",
    "TECHNIQUES",
    "atlas_refs_for_ba_rule",
    "atlas_refs_for_finding_type",
    "atlas_refs_for_goal",
    "atlas_technique_label",
    "owasp_refs_for_ba_rule",
    "owasp_refs_for_finding_type",
    "owasp_refs_for_goal",
    "owasp_refs_for_rule",
]
