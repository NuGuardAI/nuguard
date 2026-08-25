"""Generic external-sanitization → GUARDRAIL heuristic.

Framework-native GUARDRAIL detection only fires for ``guardrails-ai``
(``guardrails_ai.py``) or the OpenAI Agents SDK (``openai_agents.py``)
guardrail decorators/classes. Many apps instead hand-roll a
``sanitize_*``/``redact_*``/``scrub_*``/``filter_*_for_external``-named
function and call it immediately before sending data to an outbound
HTTP/tool call — this adapter flags that pattern as a low-confidence
GUARDRAIL candidate.

Two adjacency shapes are matched:

1. Back-to-back statements in the same block::

       clean = sanitize_output(raw)
       requests.post(url, json=clean)

2. The sanitize call nested directly as an outbound call's argument::

       requests.post(url, json=sanitize_output(raw))

When the outbound call targets a function defined in the *same* file, that
function is also emitted as a TOOL node so a ``PROTECTS`` edge can be
attached self-consistently (mirrors the AUTH → API_ENDPOINT ``PROTECTS``
pattern in ``mcp_server.py``). Known third-party HTTP client calls
(``requests.post``, ``httpx.get``, ...) are recorded as evidence on the
GUARDRAIL node instead of inventing a speculative TOOL node for them.

This is a heuristic, not a framework-native detection — confidence stays in
the 0.5-0.6 band, well below the 0.85-0.92 range used by the framework-native
adapters, and ``metadata["detection_kind"] = "heuristic"`` marks it so it's
clearly distinguishable from framework-native GUARDRAIL detections in output.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint

_CONFIDENCE = 0.55

_GUARDRAIL_NAME_RE = re.compile(
    r"^(?:sanitize|redact|scrub)_\w+$|^filter_\w+_for_external$", re.IGNORECASE
)

_OUTBOUND_METHOD_NAMES = {"post", "put", "get", "patch", "delete", "request"}
_OUTBOUND_FUNC_NAMES = {"urlopen"}


def _call_func_name(call: ast.expr) -> str | None:
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_guardrail_call(call: ast.expr) -> str | None:
    name = _call_func_name(call)
    if name and _GUARDRAIL_NAME_RE.match(name):
        return name
    return None


def _is_direct_outbound_call(call: ast.expr) -> bool:
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in _OUTBOUND_METHOD_NAMES:
        return True
    if isinstance(func, ast.Name) and func.id in _OUTBOUND_FUNC_NAMES:
        return True
    return False


def _collect_wrapper_function_names(tree: ast.AST) -> set[str]:
    """Locally-defined functions whose body makes a direct outbound call."""
    wrappers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(_is_direct_outbound_call(call) for call in ast.walk(node) if isinstance(call, ast.Call)):
            wrappers.add(node.name)
    return wrappers


def _is_outbound_call(call: ast.expr, wrapper_names: set[str]) -> bool:
    if not isinstance(call, ast.Call):
        return False
    if _is_direct_outbound_call(call):
        return True
    func = call.func
    return isinstance(func, ast.Name) and func.id in wrapper_names


def _stmt_top_level_call(stmt: ast.stmt) -> ast.Call | None:
    """Return the top-level call in an expression statement or assignment RHS."""
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return stmt.value
    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
        return stmt.value
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.value, ast.Call):
        return stmt.value
    return None


def _iter_stmt_lists(tree: ast.AST) -> list[list[ast.stmt]]:
    """Return every statement-list block in the tree (function/if/for/... bodies)."""
    blocks: list[list[ast.stmt]] = []
    for node in ast.walk(tree):
        for field_name in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field_name, None)
            if isinstance(stmts, list) and stmts and isinstance(stmts[0], ast.stmt):
                blocks.append(stmts)
    return blocks


def _collect_local_function_lines(tree: ast.AST) -> dict[str, int]:
    return {
        node.name: node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class GuardrailHeuristicAdapter(FrameworkAdapter):
    """Flags sanitize/redact/scrub-named functions used before an outbound call."""

    name = "guardrail_heuristic"
    priority = 145
    handles_imports = [
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "urllib.request",
        "urllib3",
    ]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if not content or not content.strip():
            return []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        local_functions = _collect_local_function_lines(tree)
        wrapper_names = _collect_wrapper_function_names(tree)
        # name -> (line, outbound_snippet, protects_tool_name | None)
        found: dict[str, tuple[int, str, str | None]] = {}

        # Shape 1: back-to-back statements in the same block.
        for stmts in _iter_stmt_lists(tree):
            for i in range(len(stmts) - 1):
                call = _stmt_top_level_call(stmts[i])
                guard_name = _is_guardrail_call(call) if call is not None else None
                if not guard_name or guard_name in found:
                    continue
                next_call = _stmt_top_level_call(stmts[i + 1])
                if next_call is None or not _is_outbound_call(next_call, wrapper_names):
                    continue
                next_name = _call_func_name(next_call)
                protects = next_name if next_name in local_functions else None
                found[guard_name] = (
                    stmts[i].lineno,
                    f"{guard_name}(...); {next_name}(...)",
                    protects,
                )

        # Shape 2: sanitize call nested as an outbound call's argument.
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _is_outbound_call(node, wrapper_names)):
                continue
            outbound_name = _call_func_name(node)
            for arg in (*node.args, *(kw.value for kw in node.keywords)):
                guard_name = _is_guardrail_call(arg)
                if not guard_name or guard_name in found:
                    continue
                protects = outbound_name if outbound_name in local_functions else None
                found[guard_name] = (
                    node.lineno,
                    f"{outbound_name}(..., {guard_name}(...))",
                    protects,
                )

        if not found:
            return []

        detected: list[ComponentDetection] = []
        for guard_name, (line, snippet, protects_tool_name) in found.items():
            guard_canon = canonicalize_text(f"guardrail_heuristic:{file_path}:{guard_name}")
            relationships: list[RelationshipHint] = []

            if protects_tool_name is not None:
                tool_canon = canonicalize_text(
                    f"guardrail_heuristic:tool:{file_path}:{protects_tool_name}"
                )
                relationships.append(
                    RelationshipHint(
                        source_canonical=guard_canon,
                        source_type=ComponentType.GUARDRAIL,
                        target_canonical=tool_canon,
                        target_type=ComponentType.TOOL,
                        relationship_type="PROTECTS",
                    )
                )
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=tool_canon,
                        display_name=protects_tool_name.replace("_", " ").title(),
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=_CONFIDENCE,
                        metadata={
                            "framework": "guardrail_heuristic",
                            "detection_kind": "heuristic",
                        },
                        file_path=file_path,
                        line=local_functions[protects_tool_name],
                        snippet=f"def {protects_tool_name}(...): ...",
                        evidence_kind="ast",
                    )
                )

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=guard_canon,
                    display_name=guard_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE,
                    metadata={
                        "framework": "guardrail_heuristic",
                        "detection_kind": "heuristic",
                    },
                    file_path=file_path,
                    line=line,
                    snippet=snippet,
                    evidence_kind="ast",
                    relationships=relationships,
                )
            )

        return detected
