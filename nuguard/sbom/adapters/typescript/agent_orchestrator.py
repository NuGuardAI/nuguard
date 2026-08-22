"""Hand-rolled multi-agent orchestration detector (TypeScript).

Real-world apps sometimes implement multi-agent orchestration by hand
rather than via a recognized framework (LangGraph, CrewAI, AutoGen, ...):
an abstract/base class (e.g. ``BaseAgent``) with several concrete
subclasses (``AnalysisAgent``, ``SolverAgent``, ``VerifierAgent``, ...),
instantiated and invoked in sequence from one orchestrating service
(docs/sbom-fix2.md #6). No declaration-shape adapter catches this — it's
plain OOP, not a framework API — so without a dedicated heuristic these
apps fall through to ``extractor/core.py``'s generic placeholder AGENT
node (``extras.source == "auto_enrichment"``, empty evidence), which
merely proves *an* LLM-calling app exists, not that this specific
orchestration pattern was found.

This is TS-only (regex-based, mirroring every other TS adapter in this
package — there's no shared AST base to lean on here, and no concrete gap
was found on the Python side to justify speculative cross-language
scope). Detection is cross-file: a lightweight pre-pass
(``collect_class_hierarchy``, wired via ``extractor/core.py`` the same way
as the DTO-schema/global-prefix pre-passes) indexes every ``class X``/
``class X extends Y`` declaration across the project so the orchestrating
file — which only ever sees the *subclass* names it instantiates, never
the base class's own file — can still cite the base class's real
definition site as evidence.

Deliberately narrower than full "any OOP hierarchy + sequential calls"
detection: it only fires when the base class name itself looks like an
agent class (contains "agent", case-insensitive) — a naming-convention
gate that keeps false-positive risk low, consistent with every other
usage-based TS heuristic in this package (see nestjs_tool_di.py).
"""

from __future__ import annotations

import re
from typing import Any

from ...types import ComponentType
from ..base import ComponentDetection
from ._class_scan import (
    _CLASS_RE,
    _PARAM_RE,
    _find_class_body_span,
    _line_index_at,
    _parse_constructor_params,
)
from ._ts_regex import TSFrameworkAdapter

_CONFIDENCE = 0.55

_CLASS_DECL_RE = re.compile(
    r"\b(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?\b"
)
_INJECTABLE_RE = re.compile(r"@Injectable\(\)")
_NEW_SUBCLASS_RE = re.compile(r"\bnew\s+(\w+)\s*\(")
_METHOD_CALL_NEARBY_RE = re.compile(r"\.\w+\s*\(")
_AGENT_NAME_RE = re.compile(r"agent", re.IGNORECASE)


def collect_class_hierarchy(
    content: str, file_path: str
) -> tuple[dict[str, str], dict[str, tuple[str, int]]]:
    """Return ``({subclass: base_class}, {class_name: (file_path, line)})``
    for every ``class``/``abstract class`` declaration in *content*.

    Single-file scope — used both locally and by ``extractor/core.py``'s
    cross-file pre-pass so an orchestrator in one file can cite a base
    class declared in a different file as evidence.
    """
    bases: dict[str, str] = {}
    locations: dict[str, tuple[str, int]] = {}
    for i, line in enumerate(content.splitlines()):
        m = _CLASS_DECL_RE.search(line)
        if not m:
            continue
        name, base = m.group(1), m.group(2)
        locations[name] = (file_path, i + 1)
        if base:
            bases[name] = base
    return bases, locations


class AgentOrchestratorTSAdapter(TSFrameworkAdapter):
    """Detects hand-rolled sequential multi-agent orchestration in TS."""

    name = "agent_orchestrator_ts"
    priority = 60
    handles_imports: list[str] = []  # runs on every TS file (see can_handle override)

    def __init__(self) -> None:
        self._subclass_bases: dict[str, str] = {}
        self._class_locations: dict[str, tuple[str, int]] = {}

    def can_handle(self, imports_present: set[str]) -> bool:
        # No fixed package signature — the pattern is plain class syntax.
        # Cheap to run on every TS file; extract() itself no-ops fast when
        # the cross-file class-hierarchy index has nothing agent-shaped.
        return True

    def set_global_class_hierarchy(
        self,
        subclass_bases: dict[str, str],
        class_locations: dict[str, tuple[str, int]],
    ) -> None:
        """Provide the cross-file class-hierarchy index — see
        extractor/core.py's pre-pass (mirrors set_global_model_schemas)."""
        self._subclass_bases = subclass_bases
        self._class_locations = class_locations

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if not content or not content.strip() or not self._subclass_bases:
            return []

        lines = content.splitlines()
        detected: list[ComponentDetection] = []
        seen_canonical: set[str] = set()

        # Two independent ways an agent subclass gets used: direct
        # `new XAgent(...)` instantiation, or (the more common real-world
        # NestJS pattern) constructor-injected as `private readonly x:
        # XAgent` and invoked later as `this.x.execute(...)`.
        hits_by_base: dict[str, list[tuple[int, str]]] = {}
        for base_name, hits in _new_instantiation_hits(content, lines, self._subclass_bases).items():
            hits_by_base.setdefault(base_name, []).extend(hits)
        for base_name, hits in _di_injection_hits(lines, self._subclass_bases).items():
            hits_by_base.setdefault(base_name, []).extend(hits)

        for base_name, hits in hits_by_base.items():
            hits.sort(key=lambda h: h[0])
            distinct_subclasses = {name for _, name in hits}
            if len(distinct_subclasses) < 2:
                continue  # need >=2 distinct agent subclasses sequenced together

            first_line_idx = hits[0][0]
            orchestrator_name = _enclosing_class_name(lines, first_line_idx) or file_path

            canonical = f"agent:orchestrator:{orchestrator_name}"
            if canonical in seen_canonical:
                continue
            seen_canonical.add(canonical)

            meta: dict[str, Any] = {
                "detection_basis": "sequential_orchestration_heuristic",
                "base_class": base_name,
                "subclasses": sorted(distinct_subclasses),
            }

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=canonical,
                    display_name=orchestrator_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE,
                    metadata=meta,
                    file_path=file_path,
                    line=first_line_idx + 1,
                    snippet=lines[first_line_idx].strip()[:160],
                    evidence_kind="regex",
                )
            )

            base_loc = self._class_locations.get(base_name)
            if base_loc is not None and base_loc != (file_path, first_line_idx + 1):
                base_file, base_line = base_loc
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.AGENT,
                        canonical_name=canonical,
                        display_name=orchestrator_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=_CONFIDENCE,
                        metadata=meta,
                        file_path=base_file,
                        line=base_line,
                        snippet=f"abstract class {base_name} {{ ... }}",
                        evidence_kind="regex",
                    )
                )

        return detected


def _new_instantiation_hits(
    content: str, lines: list[str], subclass_bases: dict[str, str]
) -> dict[str, list[tuple[int, str]]]:
    """``new XAgent(...)`` sites whose class extends a known, agent-named
    base, each followed by call-shaped usage nearby (chained or within the
    next couple of lines)."""
    hits_by_base: dict[str, list[tuple[int, str]]] = {}
    for m in _NEW_SUBCLASS_RE.finditer(content):
        subclass_name = m.group(1)
        base_name = subclass_bases.get(subclass_name)
        if base_name is None or not _AGENT_NAME_RE.search(base_name):
            continue
        line_idx = _line_index_at(content, m.start())
        window = "\n".join(lines[line_idx : min(line_idx + 3, len(lines))])
        call_site_offset = m.end() - sum(len(ln) + 1 for ln in lines[:line_idx])
        if not _METHOD_CALL_NEARBY_RE.search(window[call_site_offset:]):
            continue
        hits_by_base.setdefault(base_name, []).append((line_idx, subclass_name))
    return hits_by_base


def _di_injection_hits(
    lines: list[str], subclass_bases: dict[str, str]
) -> dict[str, list[tuple[int, str]]]:
    """Constructor-injected agent subclasses invoked later in the class body.

    The far more common real-world NestJS pattern (see nestjs_tool_di.py's
    same constructor-injection scan): ``private readonly analysisAgent:
    AnalysisAgent`` in the constructor, then ``this.analysisAgent.execute(...)``
    somewhere in a method — evidence points at the invocation, not the
    constructor's injection line.
    """
    hits_by_base: dict[str, list[tuple[int, str]]] = {}
    for idx, line in enumerate(lines):
        if not _INJECTABLE_RE.search(line):
            continue
        class_idx: int | None = None
        for k in range(idx, min(idx + 5, len(lines))):
            if _CLASS_RE.search(lines[k]):
                class_idx = k
                break
        if class_idx is None:
            continue

        body_start, body_end = _find_class_body_span(lines, class_idx)
        params = _parse_constructor_params(lines, body_start, body_end)
        if not params:
            continue

        for _, raw_param in params:
            pm = _PARAM_RE.search(raw_param)
            if not pm:
                continue
            type_name = pm.group(1)
            base_name = subclass_bases.get(type_name)
            if base_name is None or not _AGENT_NAME_RE.search(base_name):
                continue
            name_m = re.search(r"(\w+)\s*:\s*" + re.escape(type_name) + r"\b", raw_param)
            if not name_m:
                continue
            prop_name = name_m.group(1)
            usage_re = re.compile(r"\bthis\." + re.escape(prop_name) + r"\.\w+\s*\(")
            for j in range(body_start, body_end + 1):
                if usage_re.search(lines[j]):
                    hits_by_base.setdefault(base_name, []).append((j, type_name))
                    break

    return hits_by_base


def _enclosing_class_name(lines: list[str], line_idx: int) -> str | None:
    """Scan backward from *line_idx* for the nearest ``class X`` declaration."""
    for k in range(line_idx, -1, -1):
        m = _CLASS_RE.search(lines[k])
        if m:
            return m.group(1)
    return None
