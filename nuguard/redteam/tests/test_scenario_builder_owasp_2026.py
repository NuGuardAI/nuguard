"""Per-chain OWASP refs set by scenario builders must be canonical 2026 refs.

The literals used to mix 2023, 2025 and 2026 numbering (e.g. data exfiltration was
``LLM06``, which is Excessive Agency in 2025 and Unbounded Consumption in 2026) and
carried free-text labels. Canonical form matches ``common/control_mappings/owasp.py``:
``"LLM02:2026"`` / ``"ASI03"``, several joined with ``", "``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import nuguard.redteam.scenarios as _pkg

_DIR = Path(_pkg.__file__).parent
_LLM = re.compile(r"LLM(0[1-9]|10):2026(, LLM(0[1-9]|10):2026)*")
_ASI = re.compile(r"ASI(0[1-9]|10)(, ASI(0[1-9]|10))*")


def _literals() -> list[tuple[str, int, str, str]]:
    out = []
    for p in sorted(_DIR.glob("*.py")):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (
                isinstance(n, ast.keyword)
                and n.arg in ("owasp_llm_ref", "owasp_asi_ref")
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            ):
                out.append((p.name, n.value.lineno, n.arg, n.value.value))
    return out


def test_every_builder_literal_is_canonical() -> None:
    lits = _literals()
    assert len(lits) > 300  # guard against the scan silently finding nothing
    bad = [
        f"{f}:{ln} {arg}={v!r}"
        for f, ln, arg, v in lits
        if not (_LLM if arg == "owasp_llm_ref" else _ASI).fullmatch(v)
    ]
    assert not bad, "non-canonical OWASP refs:\n" + "\n".join(bad)


def test_family_semantics_follow_2026_numbering() -> None:
    by = {}
    for f, _ln, arg, v in _literals():
        by.setdefault((f, arg), set()).add(v)
    # Sensitive-data exfiltration is LLM02 (never the old LLM06), and no longer ASI10 (Rogue Agents).
    assert "LLM02:2026" in by[("data_exfiltration.py", "owasp_llm_ref")]
    assert not any("LLM06" in v for v in by[("data_exfiltration.py", "owasp_llm_ref")])
    assert "ASI10" not in by[("data_exfiltration.py", "owasp_asi_ref")]
    # Destructive actions: Excessive Agency + Tool Misuse (not Memory Poisoning ASI06).
    assert by[("destructive_actions.py", "owasp_llm_ref")] == {"LLM03:2026"}
    assert by[("destructive_actions.py", "owasp_asi_ref")] == {"ASI02"}
    # Improper output handling is LLM10; vector/RAG is LLM09; system-prompt extraction is LLM08.
    assert by[("output_handling.py", "owasp_llm_ref")] == {"LLM10:2026"}
    assert all("LLM09:2026" in v for v in by[("rag_attacks.py", "owasp_llm_ref")])
    assert "LLM08:2026" in by[("prompt_injection.py", "owasp_llm_ref")]
    # Human-agent trust and memory persistence keep their Agentic homes.
    assert by[("human_trust.py", "owasp_asi_ref")] == {"ASI09"}
    assert by[("memory_persistence.py", "owasp_asi_ref")] == {"ASI06"}
