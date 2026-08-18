"""Unit tests for OWASP LLM/Agentic Top 10 rule citation coverage and wiring.

Covers:
- Every NGA-xxx / NGA-SC-xxx rule_id in _RULE_META has a non-empty NGA_TO_OWASP entry
- Every NGA-xxx / NGA-SC-xxx rule_id also has a NGA_TO_ATLAS entry (no framework left unmapped)
- static_analyzer._raw_to_finding correctly comma-joins refs onto Finding.owasp_llm_ref/owasp_asi_ref
- An explicit owasp_llm_ref already present on the raw finding dict (e.g. semgrep) wins over the
  rule_id table lookup
"""
from __future__ import annotations

from nuguard.analysis._atlas_data import NGA_TO_ATLAS
from nuguard.analysis._owasp_data import NGA_TO_OWASP, RuleOwaspRefs, owasp_refs_for
from nuguard.analysis.plugins.nga_rules import _RULE_META as NGA_RULE_META
from nuguard.analysis.static_analyzer import _raw_to_finding
from nuguard.analysis.supply_chain_scanner import _RULE_META as SC_RULE_META

_ALL_RULE_IDS = [m["rule_id"] for m in NGA_RULE_META] + [m["rule_id"] for m in SC_RULE_META]


class TestCoverage:
    def test_every_rule_has_owasp_refs(self) -> None:
        missing = [rid for rid in _ALL_RULE_IDS if rid not in NGA_TO_OWASP]
        assert missing == [], f"Rules missing OWASP citations: {missing}"

    def test_every_owasp_entry_has_at_least_one_ref(self) -> None:
        empty = [
            rid for rid in _ALL_RULE_IDS
            if not NGA_TO_OWASP[rid].owasp_llm and not NGA_TO_OWASP[rid].owasp_agentic
        ]
        assert empty == [], f"Rules with an empty OWASP citation entry: {empty}"

    def test_every_rule_has_atlas_refs(self) -> None:
        missing = [rid for rid in _ALL_RULE_IDS if rid not in NGA_TO_ATLAS]
        assert missing == [], f"Rules missing MITRE ATLAS citations: {missing}"

    def test_no_rule_ids_are_duplicated_across_families(self) -> None:
        assert len(_ALL_RULE_IDS) == len(set(_ALL_RULE_IDS))

    def test_owasp_refs_for_unmapped_id_returns_empty(self) -> None:
        refs = owasp_refs_for("NGA-DOES-NOT-EXIST")
        assert refs == RuleOwaspRefs()


class TestFindingWiring:
    def test_single_llm_ref_populated(self) -> None:
        raw = {"rule_id": "NGA-001", "severity": "CRITICAL", "title": "t", "description": "d"}
        finding = _raw_to_finding(raw, "nga")
        assert finding.owasp_llm_ref == "LLM02:2026"
        assert finding.owasp_asi_ref is None

    def test_multiple_refs_comma_joined(self) -> None:
        raw = {"rule_id": "NGA-020", "severity": "MEDIUM", "title": "t", "description": "d"}
        finding = _raw_to_finding(raw, "nga")
        assert finding.owasp_llm_ref == "LLM01:2026"
        assert finding.owasp_asi_ref == "ASI01, ASI07"

    def test_explicit_raw_owasp_llm_ref_wins_over_table(self) -> None:
        raw = {
            "rule_id": "semgrep-custom-check",
            "severity": "MEDIUM",
            "title": "t",
            "description": "d",
            "owasp_llm_ref": "LLM01: Prompt Injection",
        }
        finding = _raw_to_finding(raw, "semgrep")
        assert finding.owasp_llm_ref == "LLM01: Prompt Injection"

    def test_unmapped_rule_id_leaves_refs_none(self) -> None:
        raw = {"rule_id": "GHSA-unrelated", "severity": "HIGH", "title": "t", "description": "d"}
        finding = _raw_to_finding(raw, "osv")
        assert finding.owasp_llm_ref is None
        assert finding.owasp_asi_ref is None
