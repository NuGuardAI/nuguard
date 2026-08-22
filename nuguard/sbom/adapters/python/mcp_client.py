"""MCP-client detection adapter (untrusted, user-configured MCP servers).

Complements ``mcp_server.py`` (which detects apps *hosting* an MCP server) by
detecting apps that act as an MCP *client* connecting to zero or more
user-configured, arbitrary MCP server URLs — a distinct, higher-risk shape
since the set of servers is untrusted/attacker-influenceable rather than a
fixed, developer-controlled list.

Two independent signals, either of which is sufficient evidence and both of
which merge into the same MCP_SERVER node via a stable canonical name:

1. A config/data-model class storing MCP server entries — a class (Pydantic
   ``BaseModel``, dataclass, or plain class with annotated fields) whose
   fields include ``url``/``server_url``/``endpoint`` alongside
   ``name``/``enabled``/``allow_sensitive_data``.
2. Use of the ``mcp`` Python SDK's client-side API: ``ClientSession``,
   ``stdio_client``, ``sse_client``, or ``streamablehttp_client``.

The function that drives (2) is additionally emitted as a TOOL node tagged
``trust_level="untrusted"`` and (when resolvable) ``mcp_server_url=...`` —
this is what makes the node picked up automatically by the MCP-toxic-flow /
MCP-attack scenario generators in ``nuguard/redteam/scenarios/generator.py``
(``_mcp_toxic_flow_scenarios`` filters on ``trust_level == "untrusted"``,
``_mcp_attack_scenarios`` filters on ``mcp_server_url``) with no redteam-side
code changes required.
"""

from __future__ import annotations

import ast
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint

_CONFIG_URL_FIELDS = {"url", "server_url", "endpoint"}
_CONFIG_MARKER_FIELDS = {"enabled", "allow_sensitive_data", "name", "server_name"}

_CLIENT_SYMBOLS = {"ClientSession", "stdio_client", "sse_client", "streamablehttp_client"}

_MCP_SERVER_CANON = canonicalize_text("mcp_client:servers")


def _class_field_names(node: ast.ClassDef) -> set[str]:
    fields: set[str] = set()
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fields.add(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    fields.add(target.id)
    return fields


def _looks_like_mcp_server_config(node: ast.ClassDef) -> bool:
    fields = _class_field_names(node)
    return bool(fields & _CONFIG_URL_FIELDS) and bool(fields & _CONFIG_MARKER_FIELDS)


def _imported_client_symbols(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if module != "mcp" and not module.startswith("mcp."):
            continue
        for alias in node.names:
            if alias.name in _CLIENT_SYMBOLS:
                found.add(alias.name)
    return found


def _first_string_call_arg(node: ast.Call) -> str | None:
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


class MCPClientAdapter(FrameworkAdapter):
    """Detects apps that connect to user-configured MCP servers as a client."""

    name = "mcp_client"
    priority = 55
    handles_imports = ["mcp", "mcp.client", "mcp.client.stdio", "mcp.client.sse"]

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

        has_config_model = any(
            isinstance(node, ast.ClassDef) and _looks_like_mcp_server_config(node)
            for node in ast.walk(tree)
        )
        client_symbols = _imported_client_symbols(tree)
        if not has_config_model and not client_symbols:
            return []

        detected: list[ComponentDetection] = [
            ComponentDetection(
                component_type=ComponentType.MCP_SERVER,
                canonical_name=_MCP_SERVER_CANON,
                display_name="User-Configured MCP Servers",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.80 if (has_config_model and client_symbols) else 0.70,
                metadata={
                    "framework": "mcp_client",
                    "trust_boundary": "user-configured/untrusted",
                },
                file_path=file_path,
                line=1,
                snippet=(
                    "user-configured MCP server registry"
                    if has_config_model
                    else "mcp client SDK usage"
                ),
                evidence_kind="ast",
            )
        ]

        if not client_symbols:
            return detected

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            used: set[str] = set()
            url: str | None = None
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                call_name = (
                    call.func.id
                    if isinstance(call.func, ast.Name)
                    else call.func.attr
                    if isinstance(call.func, ast.Attribute)
                    else None
                )
                if call_name in client_symbols:
                    used.add(call_name)
                    if url is None:
                        url = _first_string_call_arg(call)
            if not used:
                continue

            tool_canon = canonicalize_text(f"mcp_client:dispatcher:{file_path}:{node.name}")
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=tool_canon,
                    display_name=node.name.replace("_", " ").title(),
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.80,
                    metadata={
                        "framework": "mcp_client",
                        "trust_level": "untrusted",
                        "mcp_server_url": url or "dynamic",
                    },
                    file_path=file_path,
                    line=node.lineno,
                    snippet=f"def {node.name}(...): uses {', '.join(sorted(used))}",
                    evidence_kind="ast",
                    relationships=[
                        RelationshipHint(
                            source_canonical=tool_canon,
                            source_type=ComponentType.TOOL,
                            target_canonical=_MCP_SERVER_CANON,
                            target_type=ComponentType.MCP_SERVER,
                            relationship_type="CALLS",
                        )
                    ],
                )
            )

        return detected
