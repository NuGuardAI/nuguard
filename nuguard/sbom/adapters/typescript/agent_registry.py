"""Config-driven "agent registry" detector (TypeScript).

Real-world agentic apps sometimes declare their agents as a named-map
configuration object rather than via direct SDK call sites or class
instantiation — e.g.::

    const AGENTS = {
      build: { model: "claude-sonnet", prompt: "...", permission: {...}, tools: [...] },
      plan: { model: "claude-haiku", prompt: "...", tools: [...] },
      general: { model: "claude-opus", system: "...", tools: [...] },
    };

No existing adapter looks for this shape — the Vercel AI SDK adapter
(``vercel_ai_sdk.py``) only matches direct ``streamText``/``generateText``
call sites, so a purely config-driven agent architecture like this is
invisible to extraction entirely (docs/sbom-accuracy-plan.md #3). This is a
common pattern across coding-agent and multi-agent frameworks generally
(config-object or markdown-file-per-agent definitions), not specific to any
one SDK.

TS-only, plain regex/brace-matching over the raw source — mirroring
``agent_orchestrator.py``'s own justification: there's no shared AST base to
lean on here, and the tree-sitter object-literal extractor
(``ts_parser.py``'s ``_ts_extract_object_literals``) only captures *top-level*
``const X = {...}`` literals, not nested per-entry object values, so a
generic AST walk would need to be built from scratch anyway.
"""

from __future__ import annotations

import re
from typing import Any

from ...types import ComponentType
from .._test_paths import looks_like_test_path
from ..base import ComponentDetection
from ._ts_regex import TSFrameworkAdapter

_CONFIDENCE = 0.55
_CONFIDENCE_TEST_PATH = 0.35

# Curated "agent config" field cluster — an entry must declare at least two
# of these to count as an agent definition (avoids flagging unrelated named
# maps that happen to have one coincidentally-overlapping key, e.g. a route
# table with a "model" field for an unrelated ORM entity).
_AGENT_FIELD_NAMES = ("model", "prompt", "system", "permission", "tools")
_AGENT_FIELD_RE = re.compile(
    r"\b(" + "|".join(_AGENT_FIELD_NAMES) + r")\s*:", re.IGNORECASE
)
_MIN_MATCHING_FIELDS = 2

_TOP_LEVEL_CONST_RE = re.compile(
    r"\b(?:export\s+)?const\s+(\w+)\s*(?::\s*[^={]+)?=\s*\{"
)
_KEY_RE = re.compile(r'^\s*["\']?([A-Za-z_$][\w$]*)["\']?\s*:\s*([\s\S]*)$')


def _find_balanced_brace_end(text: str, open_index: int) -> int | None:
    """*open_index* points at a ``{``. Return the index one past its
    matching ``}``, or ``None`` if unbalanced (e.g. truncated snippet)."""
    if open_index >= len(text) or text[open_index] != "{":
        return None
    depth = 0
    in_str: str | None = None
    i = open_index
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ("'", '"', "`"):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _split_top_level_entries(body: str) -> list[str]:
    """Split an object-literal body into top-level ``key: value`` segments,
    ignoring commas nested inside braces/brackets/parens/strings."""
    depth = 0
    in_str: str | None = None
    segments: list[str] = []
    seg_start = 0
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ("'", '"', "`"):
            in_str = ch
        elif ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif ch == "," and depth == 0:
            segments.append(body[seg_start:i])
            seg_start = i + 1
        i += 1
    tail = body[seg_start:].strip()
    if tail:
        segments.append(tail)
    return segments


class AgentRegistryTSAdapter(TSFrameworkAdapter):
    """Detects a named-map "agent registry" configuration object in TS/JS."""

    name = "agent_registry_ts"
    priority = 60
    handles_imports: list[str] = []  # runs on every TS file — no fixed SDK import

    def can_handle(self, imports_present: set[str]) -> bool:
        return True

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if not content or not content.strip():
            return []

        confidence = _CONFIDENCE_TEST_PATH if looks_like_test_path(file_path) else _CONFIDENCE

        detected: list[ComponentDetection] = []
        for m in _TOP_LEVEL_CONST_RE.finditer(content):
            registry_name = m.group(1)
            brace_start = m.end() - 1
            brace_end = _find_balanced_brace_end(content, brace_start)
            if brace_end is None:
                continue
            body = content[brace_start + 1 : brace_end - 1]
            line_start = content.count("\n", 0, brace_start) + 1

            for entry in _split_top_level_entries(body):
                key_match = _KEY_RE.match(entry)
                if not key_match:
                    continue
                entry_key, value_text = key_match.group(1), key_match.group(2).strip()
                if not value_text.startswith("{"):
                    continue
                nested_end = _find_balanced_brace_end(value_text, 0)
                nested_body = value_text[:nested_end] if nested_end else value_text

                matched_fields = sorted(set(_AGENT_FIELD_RE.findall(nested_body)))
                if len(matched_fields) < _MIN_MATCHING_FIELDS:
                    continue

                entry_line = line_start + content[brace_start + 1 : brace_start + 1 + body.index(entry)].count("\n")
                canonical = f"agent:registry:{registry_name}:{entry_key}"

                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.AGENT,
                        canonical_name=canonical,
                        display_name=entry_key,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=confidence,
                        metadata={
                            "detection_basis": "config_agent_registry",
                            "registry_variable": registry_name,
                            "matched_fields": matched_fields,
                        },
                        file_path=file_path,
                        line=entry_line,
                        snippet=nested_body.strip()[:160],
                        evidence_kind="regex",
                    )
                )

        return detected
