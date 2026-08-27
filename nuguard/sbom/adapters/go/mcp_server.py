"""mcp-go server adapter for NuGuard SBOM extraction.

Detects ``github.com/mark3labs/mcp-go`` usage and emits:

- a ``FRAMEWORK`` node for the mcp-go SDK;
- ``TOOL`` nodes from ``mcp.NewTool(...)`` calls;
- ``FRAMEWORK -[CALLS]-> TOOL`` relationships for ``AddTool(...)`` registrations.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from ...core.go_parser import (
    GoFunctionCall,
    GoInstantiation,
    GoParseResult,
    parse_go,
)
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MCP_GO_MODULE = "github.com/mark3labs/mcp-go"
_NEW_SERVER = "NewMCPServer"
_NEW_TOOL = "NewTool"
_ADD_TOOL = "AddTool"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CALLEE_RE = re.compile(r"^(?P<callee>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*\(")


class MCPGoServerAdapter(GoFrameworkAdapter):
    """Detect mcp-go servers, tools, and tool registrations."""

    name = "mcp_go"
    priority = 30
    handles_imports = [_MCP_GO_MODULE]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = (
            parse_result
            if isinstance(parse_result, GoParseResult)
            else parse_go(content, file_path)
        )
        matched_import = self._matching_import(result)
        if matched_import is None:
            return []

        mcp_packages = self._package_aliases(result, "mcp")
        server_packages = self._package_aliases(result, "server")
        server_variables, server_name, server_version = self._server_details(
            result,
            server_packages,
        )

        framework = self._fw_node(
            file_path,
            matched_import,
            display_name=server_name or "MCP Go",
        )
        framework.metadata.update(
            {
                "framework": "mcp-go",
                "language": "golang",
                "module": _MCP_GO_MODULE,
            }
        )
        if server_name:
            framework.metadata["server_name"] = server_name
        if server_version:
            framework.metadata["server_version"] = server_version

        tools_by_canonical: dict[str, ComponentDetection] = {}
        canonical_by_variable: dict[str, str] = {}

        for instantiation in result.instantiations:
            if not self._is_constructor(instantiation, mcp_packages, _NEW_TOOL):
                continue

            explicit_name = self._resolve(instantiation, 0)
            tool_name = explicit_name or instantiation.assigned_to or ""
            if not tool_name:
                continue

            canonical = self._tool_canonical(tool_name)
            if canonical not in tools_by_canonical:
                metadata: dict[str, Any] = {
                    "framework": "mcp-go",
                    "language": "golang",
                    "module": _MCP_GO_MODULE,
                    "creation_method": instantiation.class_name,
                    "name_source": "argument" if explicit_name else "assignment",
                    "registered": False,
                }
                if instantiation.assigned_to:
                    metadata["assigned_to"] = instantiation.assigned_to
                if server_name:
                    metadata["server_name"] = server_name

                tools_by_canonical[canonical] = ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canonical,
                    display_name=tool_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.93 if explicit_name else 0.78,
                    metadata=metadata,
                    file_path=file_path,
                    line=instantiation.line,
                    snippet=instantiation.source_snippet or f'mcp.NewTool("{tool_name}", ...)',
                    evidence_kind="ast_instantiation",
                )

            if instantiation.assigned_to:
                canonical_by_variable[instantiation.assigned_to] = canonical

        related_tools: set[str] = set()
        for call in result.function_calls:
            if call.function_name != _ADD_TOOL or not call.receiver:
                continue
            # ``AddTool`` is not unique to mcp-go. Require positive evidence
            # that the receiver came from ``server.NewMCPServer(...)``.
            if not self._is_server_receiver(
                call.receiver,
                server_variables,
            ):
                continue

            registration_canonical, fallback_name = self._registration_target(
                call,
                canonical_by_variable,
                mcp_packages,
            )

            if registration_canonical is None and fallback_name:
                registration_canonical = self._tool_canonical(fallback_name)
                tools_by_canonical.setdefault(
                    registration_canonical,
                    self._registration_only_tool(
                        file_path,
                        call,
                        fallback_name,
                        server_name,
                    ),
                )

            if registration_canonical is None:
                continue

            tool = tools_by_canonical.get(registration_canonical)
            if tool is None:
                inline_name = self._inline_name_from_call(call, mcp_packages)
                if not inline_name:
                    continue
                tool = self._registration_only_tool(
                    file_path,
                    call,
                    inline_name,
                    server_name,
                    creation_method="inline NewTool",
                    name_source="argument",
                    confidence=0.90,
                )
                tools_by_canonical[registration_canonical] = tool

            tool.metadata["registered"] = True
            tool.metadata["registration_method"] = _ADD_TOOL
            tool.metadata["server_variable"] = call.receiver
            registration_lines = tool.metadata.setdefault("registration_lines", [])
            if isinstance(registration_lines, list) and call.line not in registration_lines:
                registration_lines.append(call.line)

            if registration_canonical not in related_tools:
                related_tools.add(registration_canonical)
                framework.relationships.append(
                    RelationshipHint(
                        source_canonical=framework.canonical_name,
                        source_type=ComponentType.FRAMEWORK,
                        target_canonical=registration_canonical,
                        target_type=ComponentType.TOOL,
                        relationship_type="CALLS",
                    )
                )

        return [framework, *tools_by_canonical.values()]

    def _package_aliases(
        self,
        result: GoParseResult,
        package_name: str,
    ) -> set[str]:
        aliases: set[str] = set()
        for imported in result.imports:
            if not self._matches_module_path(imported.path, _MCP_GO_MODULE):
                continue
            if imported.path.rsplit("/", 1)[-1] != package_name:
                continue
            if imported.alias == "_":
                continue
            aliases.add("" if imported.alias == "." else imported.alias or package_name)
        return aliases

    @classmethod
    def _server_details(
        cls,
        result: GoParseResult,
        server_packages: set[str],
    ) -> tuple[set[str], str, str]:
        variables: set[str] = set()
        server_name = ""
        server_version = ""

        for instantiation in result.instantiations:
            if not cls._is_constructor(instantiation, server_packages, _NEW_SERVER):
                continue
            if instantiation.assigned_to:
                variables.add(instantiation.assigned_to)
            if not server_name:
                server_name = cls._resolve(instantiation, 0)
            if not server_version:
                server_version = cls._resolve(instantiation, 1)

        return variables, server_name, server_version

    @staticmethod
    def _is_constructor(
        instantiation: GoInstantiation,
        package_aliases: set[str],
        constructor_name: str,
    ) -> bool:
        receiver, separator, function_name = instantiation.class_name.rpartition(".")
        if not separator:
            receiver = ""
            function_name = instantiation.class_name
        return function_name == constructor_name and receiver in package_aliases

    @staticmethod
    def _is_server_receiver(receiver: str, server_variables: set[str]) -> bool:
        return receiver in server_variables or receiver.split(".", 1)[0] in server_variables

    @staticmethod
    def _tool_canonical(tool_name: str) -> str:
        return canonicalize_text(f"mcp_go:tool:{tool_name}")

    def _registration_target(
        self,
        call: GoFunctionCall,
        canonical_by_variable: dict[str, str],
        mcp_packages: set[str],
    ) -> tuple[str | None, str | None]:
        expressions: list[str] = []
        source_expression = _first_argument_expression(call.source_snippet or "")
        if source_expression:
            expressions.append(source_expression)
        if call.positional_args and isinstance(call.positional_args[0], str):
            parsed_expression = call.positional_args[0].removeprefix("$").strip()
            if parsed_expression and parsed_expression not in expressions:
                expressions.append(parsed_expression)

        fallback_name: str | None = None
        for expression in expressions:
            normalized = expression.removeprefix("$").strip()
            if _IDENTIFIER_RE.fullmatch(normalized):
                if normalized in canonical_by_variable:
                    return canonical_by_variable[normalized], None
                fallback_name = fallback_name or normalized
                continue

            inline_name = _inline_new_tool_name(normalized, mcp_packages)
            if inline_name:
                return self._tool_canonical(inline_name), None

        return None, fallback_name

    @staticmethod
    def _inline_name_from_call(
        call: GoFunctionCall,
        mcp_packages: set[str],
    ) -> str:
        expression = _first_argument_expression(call.source_snippet or "")
        return _inline_new_tool_name(expression, mcp_packages)

    def _registration_only_tool(
        self,
        file_path: str,
        call: GoFunctionCall,
        tool_name: str,
        server_name: str,
        *,
        creation_method: str = _ADD_TOOL,
        name_source: str = "registered_variable",
        confidence: float = 0.78,
    ) -> ComponentDetection:
        metadata: dict[str, Any] = {
            "framework": "mcp-go",
            "language": "golang",
            "module": _MCP_GO_MODULE,
            "creation_method": creation_method,
            "name_source": name_source,
            "registered": True,
            "registration_method": _ADD_TOOL,
            "server_variable": call.receiver or "",
            "registration_lines": [call.line],
        }
        if server_name:
            metadata["server_name"] = server_name

        return ComponentDetection(
            component_type=ComponentType.TOOL,
            canonical_name=self._tool_canonical(tool_name),
            display_name=tool_name,
            adapter_name=self.name,
            priority=self.priority,
            confidence=confidence,
            metadata=metadata,
            file_path=file_path,
            line=call.line,
            snippet=call.source_snippet or f"{call.receiver}.AddTool({tool_name}, ...)",
            evidence_kind="ast_call",
        )


def _first_argument_expression(source_snippet: str) -> str:
    """Return the first top-level call argument from a Go source snippet."""
    open_paren = source_snippet.find("(")
    if open_paren < 0:
        return ""

    start = open_paren + 1
    depth = 0
    quote = ""
    escaped = False

    for index in range(start, len(source_snippet)):
        character = source_snippet[index]

        if quote:
            if quote != "`" and escaped:
                escaped = False
                continue
            if quote != "`" and character == "\\":
                escaped = True
                continue
            if character == quote:
                quote = ""
            continue

        if character in {'"', "'", "`"}:
            quote = character
            continue
        if character in "([{":
            depth += 1
            continue
        if character in ")]}":
            if character == ")" and depth == 0:
                return source_snippet[start:index].strip()
            depth = max(0, depth - 1)
            continue
        if character == "," and depth == 0:
            return source_snippet[start:index].strip()

    return source_snippet[start:].strip()


def _inline_new_tool_name(expression: str, package_aliases: set[str]) -> str:
    """Resolve a literal name from an inline ``mcp.NewTool(...)`` expression."""
    normalized = expression.removeprefix("$").strip()
    match = _CALLEE_RE.match(normalized)
    if match is None:
        return ""

    callee = match.group("callee")
    receiver, separator, function_name = callee.rpartition(".")
    if not separator:
        receiver = ""
        function_name = callee
    if function_name != _NEW_TOOL or receiver not in package_aliases:
        return ""

    return _decode_go_string_literal(_first_argument_expression(normalized))


def _decode_go_string_literal(value: str) -> str:
    text = value.strip()
    if len(text) < 2:
        return ""
    if text[0] == text[-1] == "`":
        return text[1:-1].replace("\r", "")
    if text[0] == text[-1] == '"':
        try:
            decoded = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return text[1:-1]
        return decoded if isinstance(decoded, str) else ""
    return ""
