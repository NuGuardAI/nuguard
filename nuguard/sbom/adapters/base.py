from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from xelo.types import ComponentType


# ---------------------------------------------------------------------------
# Legacy regex-adapter types (kept for backwards compatibility and non-Python
# file scanning)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterMatch:
    pattern: str
    line: int
    snippet: str


@dataclass(frozen=True)
class AdapterDetection:
    adapter_name: str
    component_type: ComponentType
    priority: int
    canonical_name: str
    metadata: dict[str, Any]
    matches: tuple[AdapterMatch, ...]


class DetectionAdapter:
    name: str
    priority: int

    def detect(self, content: str) -> AdapterDetection | None:
        raise NotImplementedError


class RegexAdapter(DetectionAdapter):
    def __init__(
        self,
        *,
        name: str,
        component_type: ComponentType,
        priority: int,
        patterns: tuple[re.Pattern[str], ...],
        canonical_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        skip_path_parts: frozenset[str] | None = None,
        skip_init_py: bool = False,
        skip_extensions: frozenset[str] | None = None,
    ) -> None:
        self.name = name
        self.component_type = component_type
        self.priority = priority
        self.patterns = patterns
        self.canonical_name = canonical_name
        self.metadata = metadata or {}
        # Optional path-based scope limiter: if set, this adapter is silently
        # skipped for files whose relative-path components overlap with this set
        # (e.g. test dirs) or, if skip_init_py=True, for __init__.py files.
        self.skip_path_parts = skip_path_parts
        self.skip_init_py = skip_init_py
        # Optional extension filter: if set, skip files whose suffix is in this set.
        # Useful for privilege adapters that should not fire on .tsx / .jsx UI files.
        self.skip_extensions = skip_extensions

    def detect(self, content: str) -> AdapterDetection | None:
        all_matches: list[AdapterMatch] = []
        for pattern in self.patterns:
            for match in pattern.finditer(content):
                line = content[: match.start()].count("\n") + 1
                all_matches.append(
                    AdapterMatch(
                        pattern=pattern.pattern,
                        line=line,
                        snippet=match.group(0)[:120],
                    )
                )

        if not all_matches:
            return None

        canonical = self.canonical_name or all_matches[0].snippet.strip().lower().replace(" ", "_")
        return AdapterDetection(
            adapter_name=self.name,
            component_type=self.component_type,
            priority=self.priority,
            canonical_name=canonical,
            metadata=self.metadata,
            matches=tuple(all_matches),
        )


# ---------------------------------------------------------------------------
# Rich AST-aware adapter types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelationshipHint:
    """Deferred relationship between two components, resolved after node creation."""

    source_canonical: str
    source_type: ComponentType
    target_canonical: str
    target_type: ComponentType
    relationship_type: str  # "USES", "CALLS", "ACCESSES", etc.


@dataclass
class ComponentDetection:
    """A single detected AI component, produced by a FrameworkAdapter.

    Richer than ``AdapterDetection``: carries file/line context,
    evidence kind, and pre-computed metadata from AST analysis.
    """

    component_type: ComponentType
    canonical_name: str  # lowercase, stable identifier used for dedup
    display_name: str  # human-readable name for the node
    adapter_name: str
    priority: int
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    line: int = 0
    snippet: str = ""
    evidence_kind: str = "regex"  # "ast_import" | "ast_instantiation" | "ast_call" | "regex"
    # Source tier used by the dedup phase to resolve precedence when the same
    # component is detected from multiple source categories.
    # Values: "code" | "iac" | "docs"  (set automatically by the extractor)
    source_tier: str = "code"
    # Relationships to other components detected in the same pass
    relationships: list[RelationshipHint] = field(default_factory=list)


class FrameworkAdapter:
    """Base class for AST-aware framework adapters.

    Unlike ``RegexAdapter``, a ``FrameworkAdapter`` receives both the raw
    file content and the structured ``ParseResult`` from ``ast_parser.parse()``.
    It returns a list of ``ComponentDetection`` objects rather than a single
    ``AdapterDetection``.

    Subclasses must implement:
      - ``name: str``        — unique adapter identifier
      - ``priority: int``    — lower = higher priority during dedup
      - ``handles_imports``  — list of module prefixes that activate this adapter
      - ``extract()``        — main detection/extraction logic
    """

    name: str = "unknown"
    priority: int = 50
    handles_imports: list[str] = []  # module prefixes that trigger this adapter

    def can_handle(self, imports_present: set[str]) -> bool:
        """Return True if any of the file's imported module prefixes match."""
        for mod in imports_present:
            for prefix in self.handles_imports:
                if mod == prefix or mod.startswith(prefix + "."):
                    return True
        return False

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,  # xelo.ast_parser.ParseResult
    ) -> list[ComponentDetection]:
        """Extract component detections from *file_path*.

        ``parse_result`` is a ``ParseResult`` from ``ast_parser.parse(content)``
        or ``None`` for non-Python files.
        """
        raise NotImplementedError

    def _framework_node(self, file_path: str, line: int = 0) -> ComponentDetection:
        """Emit a FRAMEWORK presence node for this adapter's framework.

        Subclasses should call this at the start of ``extract()`` whenever
        ``can_handle()`` returned True, to guarantee a FRAMEWORK node is always
        emitted even if no higher-level components are detected.
        """
        from xelo.types import ComponentType as _CT

        return ComponentDetection(
            component_type=_CT.FRAMEWORK,
            canonical_name=f"framework:{self.name}",
            display_name=f"framework:{self.name}",
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.95,
            metadata={"framework": self.name},
            file_path=file_path,
            line=line,
            snippet=f"import {self.name}",
            evidence_kind="ast_import",
        )
