"""Duplicate detection for gap-fill discovery results.

Today's exact-match dedup is safe only because gap-fill historically ran
almost exclusively on categories with zero existing nodes. Once PROBE gating
(see ``gating.py``) lets gap-fill run on categories that already have some
nodes, exact case-insensitive name matching is both too strict (misses
"GET /users/:id" vs "GET /users/{id}" for the same endpoint) and too loose
(won't catch a materially-renamed restatement of an existing node).

No fuzzy-matching/embedding dependency exists in this codebase, and the
string volumes here (<=50 existing nodes, a handful of new candidates per
category per scan) don't justify adding one — stdlib ``difflib`` is enough.
"""

from __future__ import annotations

import difflib
import re

from ...models import AiSbomDocument, Node
from ...types import ComponentType

_FUZZY_MATCH_THRESHOLD = 0.85

_PATH_PARAM_RE = re.compile(r"(:\w+|\{[^}]+\}|<\w+>)")


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _normalize_endpoint(name: str) -> str:
    """Normalize `METHOD /path` names so path-param spelling differences
    (`:id` vs `{id}` vs `<id>`) don't defeat comparison."""
    return _PATH_PARAM_RE.sub("{param}", name.strip())


class DedupContext:
    """Tracks already-known node identities for one gap-fill run."""

    def __init__(self, doc: AiSbomDocument) -> None:
        self.existing_canonical: set[str] = {
            str(n.metadata.extras.get("canonical_name", n.name)).lower() for n in doc.nodes
        }
        self._existing_by_category: dict[ComponentType, list[Node]] = {}
        for node in doc.nodes:
            self._existing_by_category.setdefault(node.component_type, []).append(node)

    def is_exact_duplicate(self, canonical: str) -> bool:
        return canonical.lower() in self.existing_canonical

    def is_fuzzy_duplicate(
        self,
        canonical: str,
        category: ComponentType,
        evidence_files: list[str] | None = None,
    ) -> bool:
        """Category-aware fuzzy duplicate check — only meaningful to call on
        the PROBE gating path; the ABSENT path never has same-category
        collisions to worry about."""
        existing_nodes = self._existing_by_category.get(category, [])
        if not existing_nodes:
            return False

        if category == ComponentType.API_ENDPOINT:
            norm_candidate = _normalize_endpoint(canonical)
            for existing in existing_nodes:
                existing_canon = str(
                    existing.metadata.extras.get("canonical_name", existing.name)
                )
                if _normalize_endpoint(existing_canon) == norm_candidate:
                    return True

        if category in (ComponentType.PROMPT, ComponentType.TOOL) and evidence_files:
            candidate_files = set(evidence_files)
            for existing in existing_nodes:
                existing_files = set(existing.metadata.extras.get("evidence_files") or [])
                if existing_files and candidate_files and candidate_files <= existing_files:
                    return True

        norm_candidate = _normalize_name(canonical)
        for existing in existing_nodes:
            existing_canon = str(existing.metadata.extras.get("canonical_name", existing.name))
            ratio = difflib.SequenceMatcher(None, norm_candidate, _normalize_name(existing_canon)).ratio()
            if ratio >= _FUZZY_MATCH_THRESHOLD:
                return True
        return False

    def register(self, canonical: str) -> None:
        self.existing_canonical.add(canonical.lower())

    def check_and_register(
        self,
        canonical: str,
        category: ComponentType,
        *,
        fuzzy: bool,
        evidence_files: list[str] | None = None,
    ) -> bool:
        """Return True (and register) if *canonical* is NEW, False if it's a duplicate."""
        if self.is_exact_duplicate(canonical):
            return False
        if fuzzy and self.is_fuzzy_duplicate(canonical, category, evidence_files):
            return False
        self.register(canonical)
        return True
