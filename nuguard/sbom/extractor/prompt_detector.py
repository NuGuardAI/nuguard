"""Prompt detector for nuguard SBOM extraction.

Detects system prompts and prompt injection risks in Python source files
by analyzing variable assignments and dictionary literals.
"""

from __future__ import annotations

import ast
import logging
import uuid
from pathlib import Path

from nuguard.models.sbom import Node, NodeMetadata, NodeType

logger = logging.getLogger(__name__)

_CONFIDENCE = 0.85
_MAX_CONTENT_LEN = 500

# Variable names that suggest a system prompt (case-insensitive check)
# A variable is a "system prompt" if its lower-case name contains
# ("system" AND ("prompt" OR "message" OR "instruction"))
_SYSTEM_KEYWORDS_A = {"system"}
_SYSTEM_KEYWORDS_B = {"prompt", "message", "instruction"}

# Variable names that indicate high-risk user-controlled input
_HIGH_RISK_VARS = {
    "user_input",
    "user_query",
    "user_message",
    "user_content",
    "user_text",
    "query",
    "input",
    "message",
    "content",
    "text",
    "body",
    "data",
    "payload",
    "request",
    "req",
}

# Attribute access patterns that indicate request/user data
_HIGH_RISK_ATTRS = {
    "body",
    "data",
    "content",
    "text",
    "json",
    "form",
    "args",
    "params",
    "user",
    "input",
}


def _stable_id(key: str) -> str:
    """Generate a stable UUID5 from a canonical key string."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _is_system_prompt_var(name: str) -> bool:
    """Return True if the variable name looks like a system prompt."""
    lower = name.lower()
    has_system = any(kw in lower for kw in _SYSTEM_KEYWORDS_A)
    has_prompt = any(kw in lower for kw in _SYSTEM_KEYWORDS_B)
    return has_system and has_prompt


def _analyze_fstring(
    node: ast.JoinedStr,
    func_args: set[str],
) -> tuple[float, bool]:
    """Analyze an f-string for injection risk.

    Returns (risk_score, is_template).
    - 1.0 if any interpolated name looks like user input
    - 0.3 if only local/parameter names
    """
    interpolated_names: list[str] = []
    has_attr_access = False

    for value in ast.walk(node):
        if isinstance(value, ast.FormattedValue):
            inner = value.value
            if isinstance(inner, ast.Name):
                interpolated_names.append(inner.id.lower())
            elif isinstance(inner, ast.Attribute):
                # e.g. request.body
                has_attr_access = True
                interpolated_names.append(inner.attr.lower())
                if isinstance(inner.value, ast.Name):
                    interpolated_names.append(inner.value.id.lower())

    if not interpolated_names and not has_attr_access:
        return 0.3, True  # f-string with no interpolations or constant

    # Check for high-risk patterns
    for name in interpolated_names:
        if name in _HIGH_RISK_VARS:
            return 1.0, True
        if name in _HIGH_RISK_ATTRS:
            return 1.0, True

    # Check for attribute access like request.body
    if has_attr_access:
        return 1.0, True

    return 0.3, True


def _extract_string_content(value_node: ast.expr) -> str | None:
    """Extract the string content from a Constant node."""
    if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
        return value_node.value
    return None


def _fstring_as_text(node: ast.JoinedStr) -> str:
    """Reconstruct approximate text of an f-string for content storage."""
    parts: list[str] = []
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            parts.append(part.value)
        elif isinstance(part, ast.FormattedValue):
            inner = part.value
            if isinstance(inner, ast.Name):
                parts.append(f"{{{inner.id}}}")
            elif isinstance(inner, ast.Attribute):
                parts.append(f"{{{ast.unparse(inner)}}}")
            else:
                parts.append("{...}")
    return "".join(parts)


class PromptDetector:
    """Detects system prompt variables and dict-based message patterns."""

    def detect(self, path: Path, source: str) -> list[Node]:
        """Parse *source* and return PROMPT nodes.

        Returns [] on empty source or syntax error.
        """
        if not source or not source.strip():
            return []

        try:
            tree = ast.parse(source)
        except SyntaxError:
            logger.debug("PromptDetector: syntax error in %s", path)
            return []

        nodes: list[Node] = []
        seen_vars: set[str] = set()

        # Walk all assignments in the AST (including inside functions)
        for node in ast.walk(tree):
            # Check assignments: var = "..." or var = f"..."
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    var_name = target.id

                    if var_name in seen_vars:
                        continue

                    if _is_system_prompt_var(var_name):
                        prompt_node = self._make_prompt_node_from_value(
                            path, var_name, node.value, set()
                        )
                        if prompt_node:
                            nodes.append(prompt_node)
                            seen_vars.add(var_name)

                    # Check for dict with role="system"
                    elif isinstance(node.value, ast.List):
                        # messages = [{"role": "system", ...}]
                        for elt in node.value.elts:
                            dict_node = self._check_system_dict(path, var_name, elt)
                            if dict_node and var_name not in seen_vars:
                                nodes.append(dict_node)
                                seen_vars.add(var_name)
                                break

            # AnnAssign: var: type = ...
            elif isinstance(node, ast.AnnAssign):
                if not isinstance(node.target, ast.Name):
                    continue
                var_name = node.target.id

                if var_name in seen_vars:
                    continue

                if node.value and _is_system_prompt_var(var_name):
                    prompt_node = self._make_prompt_node_from_value(
                        path, var_name, node.value, set()
                    )
                    if prompt_node:
                        nodes.append(prompt_node)
                        seen_vars.add(var_name)

        # Second pass: detect dict messages at module level or inside functions
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    var_name = target.id

                    if var_name in seen_vars:
                        continue

                    # Check for list of dicts with role=system
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            dict_node = self._check_system_dict(path, var_name, elt)
                            if dict_node:
                                nodes.append(dict_node)
                                seen_vars.add(var_name)
                                break

        return nodes

    def _make_prompt_node_from_value(
        self,
        path: Path,
        var_name: str,
        value_node: ast.expr,
        func_args: set[str],
    ) -> Node | None:
        """Create a PROMPT node from a variable assignment value."""
        key = f"prompt:{path}:{var_name}"
        node_id = _stable_id(key)

        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            # Static string
            content = value_node.value[:_MAX_CONTENT_LEN]
            risk_score = 0.0
            is_template = False

        elif isinstance(value_node, ast.JoinedStr):
            # f-string
            risk_score, is_template = _analyze_fstring(value_node, func_args)
            content = _fstring_as_text(value_node)[:_MAX_CONTENT_LEN]

        else:
            return None

        return Node(
            id=node_id,
            name=var_name,
            component_type=NodeType.PROMPT,
            confidence=_CONFIDENCE,
            metadata=NodeMetadata(
                extras={
                    "injection_risk_score": risk_score,
                    "is_template": is_template,
                    "content": content,
                },
            ),
        )

    def _check_system_dict(
        self,
        path: Path,
        var_name: str,
        node: ast.expr,
    ) -> Node | None:
        """Check if a node is a dict with role='system' and return a PROMPT node."""
        if not isinstance(node, ast.Dict):
            return None

        # Check for {"role": "system", "content": "..."}
        role_val = None
        content_val = None

        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                if k.value == "role":
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        role_val = v.value
                elif k.value == "content":
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        content_val = v.value

        if role_val != "system":
            return None

        key = f"prompt:{path}:{var_name}:system_dict"
        node_id = _stable_id(key)
        content = (content_val or "")[:_MAX_CONTENT_LEN]

        return Node(
            id=node_id,
            name=var_name,
            component_type=NodeType.PROMPT,
            confidence=_CONFIDENCE,
            metadata=NodeMetadata(
                extras={
                    "injection_risk_score": 0.0,
                    "is_template": False,
                    "content": content,
                },
            ),
        )
