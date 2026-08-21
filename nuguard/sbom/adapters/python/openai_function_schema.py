"""Generic OpenAI-function-schema TOOL adapter.

Detects raw OpenAI-style tool/function schema dict literals built by hand in
Python source, e.g.::

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "...",
                "parameters": {...},
            },
        },
    ]

This is a common pattern for apps that call ``chat.completions.create(tools=[...])``
directly without an agent framework. The JSON-file counterpart of this shape is
``OpenAIToolsJSONAdapter`` in ``adapters/json_adapters.py``; this adapter covers
the same schema when it is built inline as a Python dict/list literal instead of
loaded from a ``tools.json``-style file.

Also attempts to locate a same-file dispatcher (``if tool_name == "x": ...`` or a
``{"x": handler}`` dispatch table) keyed by the detected tool name, so the emitted
node points at the actual implementation rather than just the schema declaration.
"""

from __future__ import annotations

import ast
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint

_CONFIDENCE = 0.85


def _dict_str_keys(node: ast.expr | None) -> dict[str, ast.expr] | None:
    """Return ``{str_key: value_node}`` for an all-string-key ``ast.Dict``.

    Returns ``None`` if *node* isn't a dict literal, or contains a non-string
    key or a ``**spread`` entry (both make shape-matching unreliable).
    """
    if not isinstance(node, ast.Dict):
        return None
    result: dict[str, ast.expr] = {}
    for key, value in zip(node.keys, node.values):
        if key is None or value is None:
            return None
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        result[key.value] = value
    return result


def _const_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_or_none(node: ast.expr | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _find_dispatcher(tree: ast.AST, tool_name: str) -> tuple[int, str] | None:
    """Best-effort search for a dispatch site keyed by *tool_name*.

    Matches ``if <var> == "<tool_name>":`` comparisons and
    ``{"<tool_name>": handler, ...}`` dispatch-table dict entries.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            left, right = node.left, node.comparators[0]
            for a, b in ((left, right), (right, left)):
                if _const_str(a) == tool_name and isinstance(b, ast.Name):
                    return node.lineno, f"if {b.id} == {tool_name!r}:"
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if _const_str(key) != tool_name:
                    continue
                if isinstance(value, ast.Name):
                    return node.lineno, f"{tool_name!r}: {value.id}"
                if isinstance(value, ast.Attribute):
                    return node.lineno, f"{tool_name!r}: {value.attr}"
    return None


class OpenAIFunctionSchemaAdapter(FrameworkAdapter):
    """Detects hand-built OpenAI-style tool/function schema dict literals."""

    name = "openai_function_schema"
    priority = 55
    # Not gated by handles_imports/can_handle(): the LLM SDK import commonly
    # lives in the *caller* (wherever chat.completions.create(tools=...) is
    # invoked), not in the module that defines the tool schema dicts — e.g.
    # a plain "tools.py" with zero openai/litellm imports. The dict-literal
    # shape matched below (a "type": "function" dict with a nested
    # "function" dict carrying "name"/"parameters") is specific enough on
    # its own to avoid false positives without an import signal.
    handles_imports: list[str] = []

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
        # Cheap prefilter so files with no chance of matching skip the
        # ast.parse() cost below (this adapter runs on every Python file).
        if '"type"' not in content and "'type'" not in content:
            return []
        if '"function"' not in content and "'function'" not in content:
            return []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        detected: list[ComponentDetection] = []
        framework_canon = f"framework:{self.name}"
        seen_names: set[str] = set()

        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            outer = _dict_str_keys(node)
            if outer is None:
                continue
            if _const_str(outer.get("type")) != "function":
                continue
            fn_fields = _dict_str_keys(outer.get("function"))
            if fn_fields is None:
                continue
            tool_name = _const_str(fn_fields.get("name"))
            if not tool_name or tool_name in seen_names:
                continue
            seen_names.add(tool_name)

            description = _const_str(fn_fields.get("description")) or ""
            parameters = _literal_or_none(fn_fields.get("parameters"))

            line = node.lineno
            snippet = f'{{"type": "function", "function": {{"name": {tool_name!r}, ...}}}}'
            dispatcher = _find_dispatcher(tree, tool_name)
            if dispatcher is not None:
                line, snippet = dispatcher

            tool_canon = canonicalize_text(f"openai_function_schema:{tool_name}")
            metadata: dict[str, Any] = {
                "framework": "openai_function_schema",
                "description": description[:200] if description else None,
            }
            if parameters is not None:
                metadata["parameters"] = parameters

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=tool_canon,
                    display_name=tool_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE,
                    metadata=metadata,
                    file_path=file_path,
                    line=line,
                    snippet=snippet,
                    evidence_kind="ast",
                    relationships=[
                        RelationshipHint(
                            source_canonical=framework_canon,
                            source_type=ComponentType.FRAMEWORK,
                            target_canonical=tool_canon,
                            target_type=ComponentType.TOOL,
                            relationship_type="CALLS",
                        )
                    ],
                )
            )

        if detected:
            detected.insert(0, self._framework_node(file_path))
        return detected
