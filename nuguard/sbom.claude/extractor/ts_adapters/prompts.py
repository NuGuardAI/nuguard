"""TypeScript prompts adapter.

Detects system prompts and injection-risk patterns in TypeScript/JavaScript
source files.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from nuguard.models.sbom import (
    Edge,
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
)
from nuguard.sbom.extractor.ts_parser import TSParseResult

_PROMPT_CONTEXT_KEYWORDS = frozenset(
    {"prompt", "instruction", "system", "template", "message"}
)

_PROMPT_CONTENT_TRIGGERS = [
    "you are",
    "your task",
    "as an ai",
    "return json",
    "output format",
    "system:",
    "instructions:",
    "you must",
    "you should",
    "your role",
]

# User-input markers for injection risk scoring
_USER_INPUT_MARKERS = [
    "req.body",
    "req.query",
    "userInput",
    "message.content",
    "searchParams",
    "formData",
]

_PARAM_REF_MARKERS = ["props.", "params.", "args."]

_TEMPLATE_VAR_RE = re.compile(r"\$\{[^}]+\}|\{[^}]+\}")


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _evidence(
    kind: EvidenceKind,
    confidence: float,
    detail: str,
    path: Path,
    line: int | None = None,
) -> Evidence:
    return Evidence(
        kind=kind,
        confidence=confidence,
        detail=detail,
        location=EvidenceLocation(path=str(path), line=line),
    )


def _is_likely_prompt(text: str, context: str) -> bool:
    """Return True when a string looks like a prompt."""
    if len(text) < 40:
        return False
    ctx_lower = context.lower()
    if any(kw in ctx_lower for kw in _PROMPT_CONTEXT_KEYWORDS):
        return True
    text_lower = text.lower()
    return any(trigger in text_lower for trigger in _PROMPT_CONTENT_TRIGGERS)


def injection_risk_score(text: str) -> float:
    """Score a prompt string for injection risk (0.0 – 1.0)."""
    # User-input references: highest risk
    if any(marker in text for marker in _USER_INPUT_MARKERS):
        return 1.0
    # Function parameters referenced
    if any(marker in text for marker in _PARAM_REF_MARKERS):
        return 0.7
    # Template variables only
    if _TEMPLATE_VAR_RE.search(text):
        return 0.3
    return 0.0


class PromptsTSAdapter:
    """Detect system prompts and injection-risk patterns in TS/JS files."""

    # Handles all TS/JS files (no module trigger required)
    TRIGGER_MODULES: frozenset[str] = frozenset()

    def can_handle(self, result: TSParseResult) -> bool:
        return True  # Always runs

    def extract(self, file_path: Path, result: TSParseResult) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        seen: set[str] = set()

        # From parsed string literals
        for lit in result.string_literals:
            if not _is_likely_prompt(lit.value, lit.context):
                continue
            name = lit.value[:40]
            if name in seen:
                continue
            seen.add(name)
            risk = injection_risk_score(lit.value)
            nid = _stable_id(name, NodeType.PROMPT)
            nodes.append(
                Node(
                    id=nid,
                    name=name,
                    component_type=NodeType.PROMPT,
                    confidence=0.75,
                    metadata=NodeMetadata(
                        extras={
                            "is_template": lit.is_template,
                            "injection_risk": risk,
                        }
                    ),
                    evidence=[
                        _evidence(
                            EvidenceKind.REGEX,
                            0.75,
                            f"TS prompt string literal (injection_risk={risk:.1f})",
                            file_path,
                            lit.line,
                        )
                    ],
                )
            )

        # From calls: new SystemMessage("...") or { role: "system", content: "..." }
        for call in result.calls:
            name_bare = call.name.replace("new ", "")
            if name_bare in ("SystemMessage",):
                content = call.args[0].strip().strip('"\'`') if call.args else ""
                if len(content) >= 20:
                    prompt_name = content[:40]
                    if prompt_name not in seen:
                        seen.add(prompt_name)
                        nid = _stable_id(prompt_name, NodeType.PROMPT)
                        nodes.append(
                            Node(
                                id=nid,
                                name=prompt_name,
                                component_type=NodeType.PROMPT,
                                confidence=0.85,
                                metadata=NodeMetadata(),
                                evidence=[
                                    _evidence(
                                        EvidenceKind.AST,
                                        0.85,
                                        "TS SystemMessage prompt",
                                        file_path,
                                        call.line,
                                    )
                                ],
                            )
                        )

            # { role: "system", content: "..." }
            if call.kwargs.get("role") == "system":
                content = call.kwargs.get("content", "")
                if len(content) >= 20:
                    prompt_name = content[:40]
                    if prompt_name not in seen:
                        seen.add(prompt_name)
                        nid = _stable_id(prompt_name, NodeType.PROMPT)
                        nodes.append(
                            Node(
                                id=nid,
                                name=prompt_name,
                                component_type=NodeType.PROMPT,
                                confidence=0.85,
                                metadata=NodeMetadata(
                                    extras={"injection_risk": injection_risk_score(content)}
                                ),
                                evidence=[
                                    _evidence(
                                        EvidenceKind.AST,
                                        0.85,
                                        "TS system role message",
                                        file_path,
                                        call.line,
                                    )
                                ],
                            )
                        )

        return nodes, []
