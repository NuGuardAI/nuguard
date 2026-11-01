"""Catalog OWASP references must use true 2026 numbering (v5 spec §4)."""
from __future__ import annotations

import re

from nuguard.redteam.catalog.registry import SCENARIO_CATALOG

_BY_ID = {s.id: s for s in SCENARIO_CATALOG}
_LLM = re.compile(r"LLM(0[1-9]|10):2026")
_ASI = re.compile(r"ASI(0[1-9]|10)")


def test_all_refs_are_valid_2026_codes() -> None:
    for s in SCENARIO_CATALOG:
        assert s.owasp_llm and s.owasp_agentic or s.id[0] in "LWX", s.id
        assert all(_LLM.fullmatch(r) for r in s.owasp_llm), (s.id, s.owasp_llm)
        assert all(_ASI.fullmatch(r) for r in s.owasp_agentic), (s.id, s.owasp_agentic)


def test_known_2025_misnumberings_are_fixed() -> None:
    # 2025: LLM06 Excessive Agency -> 2026 LLM03
    for sid in ("T01", "T05", "H01", "G01"):
        assert "LLM03:2026" in _BY_ID[sid].owasp_llm and "LLM06:2026" not in _BY_ID[sid].owasp_llm
    # 2025: LLM07 system-prompt leakage -> 2026 LLM08 Hidden Context Exposure
    for sid in ("E05", "E06", "M07"):
        assert "LLM08:2026" in _BY_ID[sid].owasp_llm and "LLM07:2026" not in _BY_ID[sid].owasp_llm
    # 2025: LLM08 vector/embedding -> 2026 LLM09
    for sid in ("R01", "R03", "D05"):
        assert "LLM09:2026" in _BY_ID[sid].owasp_llm
    # 2025: LLM09 misinformation -> 2026 LLM07; 2025 LLM10 unbounded consumption -> 2026 LLM06
    assert "LLM07:2026" in _BY_ID["B01"].owasp_llm
    assert "LLM06:2026" in _BY_ID["B05"].owasp_llm and "LLM10:2026" not in _BY_ID["B05"].owasp_llm
    # 2025: LLM05 improper output -> 2026 LLM10
    assert "LLM10:2026" in _BY_ID["O01"].owasp_llm


def test_agentic_refs_follow_spec_table() -> None:
    assert _BY_ID["T01"].owasp_agentic == ("ASI02",)          # not ASI06 (memory poisoning)
    assert {"ASI02", "ASI10"} <= set(_BY_ID["T07"].owasp_agentic)
    assert "ASI06" in _BY_ID["P01"].owasp_agentic             # memory poisoning stays
    assert "ASI09" in _BY_ID["H01"].owasp_agentic and "ASI09" in _BY_ID["B01"].owasp_agentic
    assert "ASI07" in _BY_ID["G01"].owasp_agentic             # inter-agent comms
    assert "ASI04" in _BY_ID["V01"].owasp_agentic             # agentic supply chain


def test_spec_section_4_representative_coverage() -> None:
    def has(code: str, ids: list[str]) -> None:
        for sid in ids:
            assert code in _BY_ID[sid].owasp_llm, (code, sid)

    has("LLM01:2026", ["J01", "I01", "E01", "M03", "R04"])
    has("LLM02:2026", ["D01", "C01", "N02", "S01", "V01"])
    has("LLM05:2026", ["P01", "R01", "I01"])
    has("LLM06:2026", ["B05", "E03", "J02"])
    has("LLM08:2026", ["E06", "M07", "S03"])
    has("LLM10:2026", ["O01", "C01", "S07"])
