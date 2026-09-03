"""``cloudwego/eino`` component-graph framework adapter.

Detects Eino graph/chain construction, standalone tools, and model, prompt,
and tool components registered through graph ``Add*`` and chain ``Append*``
methods. Package receivers are derived from parsed imports, including aliases.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

from ...core.go_parser import (
    GoFunctionCall,
    GoImport,
    GoParseResult,
    parse_go,
)
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/cloudwego/eino"
_COMPOSE_MODULE = f"{_MODULE}/compose"
_PROMPT_MODULE = f"{_MODULE}/components/prompt"
_TOOL_MODULE = f"{_MODULE}/components/tool"

_GRAPH_CONSTRUCTORS = {
    "NewGraph": "graph",
    "NewChain": "chain",
}
_TOOL_CONSTRUCTORS = {
    "NewTool",
    "NewStreamTool",
}
_PROMPT_CONSTRUCTORS = {
    "FromMessages",
    "FromAgenticMessages",
}
_MODEL_KEYS = (
    "Model",
    "ModelName",
    "ModelID",
    "ModelId",
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|`[^`]*`')
_MODEL_OPTION_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?"
    r"With(?:Default)?Model(?:Name|ID|Id)?\s*\(\s*"
    r'(?P<value>"(?:\\.|[^"\\])*"|`[^`]*`)'
)


@dataclass(frozen=True)
class _RegistrationSpec:
    component_type: ComponentType
    relationship_type: str
    orchestration_kind: str
    node_key_index: int | None
    component_index: int


_REGISTRATIONS = {
    "AddChatModelNode": _RegistrationSpec(
        ComponentType.MODEL,
        "USES",
        "graph",
        0,
        1,
    ),
    "AddAgenticModelNode": _RegistrationSpec(
        ComponentType.MODEL,
        "USES",
        "graph",
        0,
        1,
    ),
    "AddChatTemplateNode": _RegistrationSpec(
        ComponentType.PROMPT,
        "USES",
        "graph",
        0,
        1,
    ),
    "AddAgenticChatTemplateNode": _RegistrationSpec(
        ComponentType.PROMPT,
        "USES",
        "graph",
        0,
        1,
    ),
    "AddToolsNode": _RegistrationSpec(
        ComponentType.TOOL,
        "CALLS",
        "graph",
        0,
        1,
    ),
    "AddAgenticToolsNode": _RegistrationSpec(
        ComponentType.TOOL,
        "CALLS",
        "graph",
        0,
        1,
    ),
    "AppendChatModel": _RegistrationSpec(
        ComponentType.MODEL,
        "USES",
        "chain",
        None,
        0,
    ),
    "AppendAgenticModel": _RegistrationSpec(
        ComponentType.MODEL,
        "USES",
        "chain",
        None,
        0,
    ),
    "AppendChatTemplate": _RegistrationSpec(
        ComponentType.PROMPT,
        "USES",
        "chain",
        None,
        0,
    ),
    "AppendAgenticChatTemplate": _RegistrationSpec(
        ComponentType.PROMPT,
        "USES",
        "chain",
        None,
        0,
    ),
    "AppendToolsNode": _RegistrationSpec(
        ComponentType.TOOL,
        "CALLS",
        "chain",
        None,
        0,
    ),
    "AppendAgenticToolsNode": _RegistrationSpec(
        ComponentType.TOOL,
        "CALLS",
        "chain",
        None,
        0,
    ),
}


class EinoAdapter(GoFrameworkAdapter):
    """Detect Eino orchestration components and graph registrations."""

    name = "eino"
    priority = 50
    handles_imports = [_MODULE]

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

        framework = self._fw_node(
            file_path,
            matched_import,
            display_name="Eino",
        )
        framework.metadata.update(
            {
                "framework": "eino",
                "module": _MODULE,
            }
        )

        compose_aliases = _aliases(
            result,
            exact=_COMPOSE_MODULE,
        )
        prompt_aliases = _aliases(
            result,
            exact=_PROMPT_MODULE,
        )
        tool_aliases = _aliases(
            result,
            prefix=_TOOL_MODULE,
        )
        imports_by_alias = _all_import_aliases(result)
        calls_by_variable = _calls_by_variable(result)

        agents: list[ComponentDetection] = []
        agents_by_variable: dict[
            tuple[tuple[int, int], str],
            tuple[ComponentDetection, str],
        ] = {}
        components: dict[
            tuple[ComponentType, str],
            ComponentDetection,
        ] = {}
        tools_by_variable: dict[
            tuple[tuple[int, int], str],
            ComponentDetection,
        ] = {}

        for call in result.function_calls:
            receiver = call.receiver or ""
            kind = _GRAPH_CONSTRUCTORS.get(call.function_name)

            if receiver not in compose_aliases or kind is None:
                continue

            assigned = call.assigned_to or call.function_name
            agent = _component(
                component_type=ComponentType.AGENT,
                canonical_name=canonicalize_text(f"eino:agent:{file_path}:{assigned}"),
                display_name=assigned,
                adapter=self,
                file_path=file_path,
                call=call,
                confidence=0.85,
                metadata={
                    "framework": "eino",
                    "language": "golang",
                    "module": (compose_aliases[receiver]),
                    "orchestration_kind": kind,
                    "creation_method": (call.full_name),
                    **({"assigned_to": call.assigned_to} if call.assigned_to else {}),
                },
            )
            _relate(
                agent,
                framework,
                agent,
                "USES",
            )
            agents.append(agent)

            if call.assigned_to:
                scope = _scope_for_line(
                    result,
                    call.line,
                )
                agents_by_variable.setdefault(
                    (
                        scope,
                        call.assigned_to,
                    ),
                    (
                        agent,
                        kind,
                    ),
                )

        for call in result.function_calls:
            receiver = call.receiver or ""

            if receiver not in tool_aliases or call.function_name not in _TOOL_CONSTRUCTORS:
                continue

            tool = self._standalone_tool(
                call,
                file_path,
                tool_aliases[receiver],
            )

            if tool is None:
                continue

            stored = components.setdefault(
                (
                    ComponentType.TOOL,
                    tool.canonical_name,
                ),
                tool,
            )
            _relate(
                stored,
                framework,
                stored,
                "USES",
            )

            if call.assigned_to:
                scope = _scope_for_line(
                    result,
                    call.line,
                )
                tools_by_variable[
                    (
                        scope,
                        call.assigned_to,
                    )
                ] = stored

        for call in result.function_calls:
            receiver = call.receiver or ""
            scope = _scope_for_line(
                result,
                call.line,
            )
            agent_entry = agents_by_variable.get(
                (
                    scope,
                    receiver,
                )
            )
            spec = _REGISTRATIONS.get(call.function_name)

            if agent_entry is None or spec is None:
                continue

            agent, kind = agent_entry

            if kind != spec.orchestration_kind:
                continue

            arguments = _call_arguments(call.source_snippet or "")
            node_key = _node_key(
                call,
                arguments,
                spec,
            )
            variable = _component_variable(
                arguments,
                spec,
            )

            if not (node_key or variable):
                continue

            candidate = self._registered_component(
                call,
                spec,
                file_path=file_path,
                agent=agent,
                node_key=node_key,
                variable=variable,
                constructor=(
                    calls_by_variable.get(
                        (
                            scope,
                            variable,
                        )
                    )
                ),
                imports_by_alias=imports_by_alias,
                prompt_aliases=prompt_aliases,
                tools_by_variable=(tools_by_variable),
                scope=scope,
            )

            if candidate is None:
                continue

            stored = components.setdefault(
                (
                    candidate.component_type,
                    candidate.canonical_name,
                ),
                candidate,
            )
            _record_registration(
                stored,
                call,
                agent,
                kind,
                node_key,
                variable,
            )
            _relate(
                stored,
                agent,
                stored,
                spec.relationship_type,
            )

        return [
            framework,
            *agents,
            *components.values(),
        ]

    def _standalone_tool(
        self,
        call: GoFunctionCall,
        file_path: str,
        module_path: str,
    ) -> ComponentDetection | None:
        info = call.positional_args[0] if call.positional_args else None

        if not isinstance(info, dict):
            return None

        name = self._clean(info.get("Name"))

        if not name:
            return None

        description = self._clean(info.get("Desc"))
        metadata: dict[str, Any] = {
            "framework": "eino",
            "language": "golang",
            "module": module_path,
            "creation_method": call.full_name,
            "registered": False,
        }

        if description:
            metadata["description"] = description

        if call.assigned_to:
            metadata["assigned_to"] = call.assigned_to

        return _component(
            component_type=ComponentType.TOOL,
            canonical_name=canonicalize_text(f"eino:tool:{name}"),
            display_name=name,
            adapter=self,
            file_path=file_path,
            call=call,
            confidence=0.85,
            metadata=metadata,
        )

    def _registered_component(
        self,
        call: GoFunctionCall,
        spec: _RegistrationSpec,
        *,
        file_path: str,
        agent: ComponentDetection,
        node_key: str,
        variable: str,
        constructor: GoFunctionCall | None,
        imports_by_alias: dict[str, str],
        prompt_aliases: dict[str, str],
        tools_by_variable: dict[
            tuple[tuple[int, int], str],
            ComponentDetection,
        ],
        scope: tuple[int, int],
    ) -> ComponentDetection | None:
        identity = node_key or variable
        module_path = _call_module(
            constructor,
            imports_by_alias,
        )
        metadata: dict[str, Any] = {
            "framework": "eino",
            "language": "golang",
            "registered": True,
            **(
                {
                    "node_key": node_key,
                }
                if node_key
                else {}
            ),
            **(
                {
                    "referenced_variable": variable,
                }
                if variable
                else {}
            ),
            **(
                {
                    "module": module_path,
                }
                if module_path
                else {}
            ),
            **(
                {
                    "creation_method": (constructor.full_name),
                    "constructor_line": (constructor.line),
                }
                if constructor is not None
                else {}
            ),
        }

        if spec.component_type == ComponentType.MODEL:
            model_name = _model_name(constructor)
            display_name = model_name or identity

            if model_name:
                metadata["model_name"] = model_name

            provider = _provider_name(module_path)

            if provider:
                metadata["provider"] = provider

            return _component(
                component_type=ComponentType.MODEL,
                canonical_name=(
                    canonicalize_text(model_name.lower())
                    if model_name
                    else canonicalize_text(
                        f"eino:model:{file_path}:{agent.display_name}:{identity}"
                    )
                ),
                display_name=display_name,
                adapter=self,
                file_path=file_path,
                call=call,
                confidence=(0.94 if model_name else 0.8),
                metadata=metadata,
            )

        if spec.component_type == ComponentType.PROMPT:
            content = _prompt_content(
                constructor,
                prompt_aliases,
            )

            if content:
                variables = self._template_vars(content)
                metadata.update(
                    {
                        "content": content,
                        "content_excerpt": (content[:200]),
                        "char_count": len(content),
                        "is_template": bool(variables),
                        "template_variables": (variables),
                    }
                )

            return _component(
                component_type=ComponentType.PROMPT,
                canonical_name=canonicalize_text(
                    f"eino:prompt:{file_path}:{agent.display_name}:{identity}"
                ),
                display_name=identity,
                adapter=self,
                file_path=file_path,
                call=call,
                confidence=(0.92 if content else 0.8),
                metadata=metadata,
            )

        if spec.component_type == ComponentType.TOOL:
            existing = tools_by_variable.get(
                (
                    scope,
                    variable,
                )
            )

            if existing is not None:
                existing.metadata["registered"] = True
                return existing

            member_variables = _tool_member_variables(constructor)
            member_tools = [
                tools_by_variable[
                    (
                        scope,
                        name,
                    )
                ].canonical_name
                for name in member_variables
                if (
                    scope,
                    name,
                )
                in tools_by_variable
            ]

            if member_variables:
                metadata["member_variables"] = member_variables

            if member_tools:
                metadata["member_tools"] = member_tools

            metadata["component_kind"] = "tools_node"

            return _component(
                component_type=ComponentType.TOOL,
                canonical_name=canonicalize_text(
                    f"eino:tool-node:{file_path}:{agent.display_name}:{identity}"
                ),
                display_name=identity,
                adapter=self,
                file_path=file_path,
                call=call,
                confidence=(0.9 if constructor is not None else 0.78),
                metadata=metadata,
            )

        return None


def _component(
    *,
    component_type: ComponentType,
    canonical_name: str,
    display_name: str,
    adapter: EinoAdapter,
    file_path: str,
    call: GoFunctionCall,
    confidence: float,
    metadata: dict[str, Any],
) -> ComponentDetection:
    return ComponentDetection(
        component_type=component_type,
        canonical_name=canonical_name,
        display_name=display_name,
        adapter_name=adapter.name,
        priority=adapter.priority,
        confidence=confidence,
        metadata=metadata,
        file_path=file_path,
        line=call.line,
        snippet=(call.source_snippet or f"{call.full_name}(...)"),
        evidence_kind="ast_call",
    )


def _aliases(
    result: GoParseResult,
    *,
    exact: str | None = None,
    prefix: str | None = None,
) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for imported in result.imports:
        if exact is not None:
            matches = imported.path == exact

        elif prefix is not None:
            matches = imported.path == prefix or imported.path.startswith(f"{prefix}/")

        else:
            matches = False

        alias = _effective_alias(imported)

        if matches and alias is not None:
            aliases[alias] = imported.path

    return aliases


def _all_import_aliases(
    result: GoParseResult,
) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for imported in result.imports:
        alias = _effective_alias(imported)

        if alias is not None:
            aliases[alias] = imported.path

    return aliases


def _effective_alias(
    imported: GoImport,
) -> str | None:
    if imported.alias in {
        "_",
        ".",
    }:
        return None

    return imported.alias or imported.path.rsplit("/", 1)[-1]


def _scope_for_line(
    result: GoParseResult,
    line: int,
) -> tuple[int, int]:
    for declaration in result.function_declarations:
        if declaration.line <= line <= declaration.line_end:
            return (
                declaration.line,
                declaration.line_end,
            )

    return 0, 0


def _calls_by_variable(
    result: GoParseResult,
) -> dict[
    tuple[tuple[int, int], str],
    GoFunctionCall,
]:
    calls: dict[
        tuple[tuple[int, int], str],
        GoFunctionCall,
    ] = {}

    for call in result.function_calls:
        if not call.assigned_to:
            continue

        scope = _scope_for_line(
            result,
            call.line,
        )
        calls.setdefault(
            (
                scope,
                call.assigned_to,
            ),
            call,
        )

    return calls


def _node_key(
    call: GoFunctionCall,
    arguments: list[str],
    spec: _RegistrationSpec,
) -> str:
    index = spec.node_key_index

    if index is None:
        return ""

    if index < len(call.positional_args):
        value = call.positional_args[index]

        if isinstance(value, str) and value and not value.startswith("$"):
            return value

    if index >= len(arguments):
        return ""

    return _decode_go_string(arguments[index])


def _component_variable(
    arguments: list[str],
    spec: _RegistrationSpec,
) -> str:
    if spec.component_index >= len(arguments):
        return ""

    expression = arguments[spec.component_index].strip().lstrip("&")

    return expression if _IDENTIFIER_RE.fullmatch(expression) else ""


def _record_registration(
    component: ComponentDetection,
    call: GoFunctionCall,
    agent: ComponentDetection,
    orchestration_kind: str,
    node_key: str,
    variable: str,
) -> None:
    component.metadata["registered"] = True

    registration: dict[str, Any] = {
        "agent": agent.canonical_name,
        "receiver": call.receiver or "",
        "orchestration_kind": (orchestration_kind),
        "method": call.function_name,
        "line": call.line,
        "snippet": (call.source_snippet or ""),
    }

    if node_key:
        registration["node_key"] = node_key

    if variable:
        registration["referenced_variable"] = variable

    registrations = component.metadata.setdefault(
        "eino_registrations",
        [],
    )

    if isinstance(registrations, list) and registration not in registrations:
        registrations.append(registration)


def _relate(
    owner: ComponentDetection,
    source: ComponentDetection,
    target: ComponentDetection,
    relationship_type: str,
) -> None:
    relationship = RelationshipHint(
        source_canonical=source.canonical_name,
        source_type=source.component_type,
        target_canonical=target.canonical_name,
        target_type=target.component_type,
        relationship_type=relationship_type,
    )

    if relationship not in owner.relationships:
        owner.relationships.append(relationship)


def _model_name(
    constructor: GoFunctionCall | None,
) -> str:
    if constructor is None:
        return ""

    for value in constructor.positional_args:
        found = _find_model_name(value)

        if found:
            return found

    match = _MODEL_OPTION_RE.search(constructor.source_snippet or "")

    return _decode_go_string(match.group("value")) if match else ""


def _find_model_name(
    value: Any,
) -> str:
    if isinstance(value, dict):
        for key in _MODEL_KEYS:
            candidate = value.get(key)

            if isinstance(candidate, str) and candidate and not candidate.startswith("$"):
                return candidate

        for nested in value.values():
            found = _find_model_name(nested)

            if found:
                return found

    elif isinstance(value, list):
        for nested in value:
            found = _find_model_name(nested)

            if found:
                return found

    return ""


def _prompt_content(
    constructor: GoFunctionCall | None,
    prompt_aliases: dict[str, str],
) -> str:
    if (
        constructor is None
        or (constructor.receiver or "") not in prompt_aliases
        or constructor.function_name not in _PROMPT_CONSTRUCTORS
    ):
        return ""

    values = [
        _decode_go_string(match.group(0))
        for match in _STRING_RE.finditer(constructor.source_snippet or "")
    ]

    return "\n".join(value for value in values if value)


def _tool_member_variables(
    constructor: GoFunctionCall | None,
) -> list[str]:
    if constructor is None:
        return []

    source = constructor.source_snippet or ""
    match = re.search(
        r"\bTools\s*:\s*",
        source,
    )

    if match is None:
        return []

    open_brace = source.find(
        "{",
        match.end(),
    )
    close_brace = (
        _find_balanced_end(
            source,
            open_brace,
            "{",
            "}",
        )
        if open_brace >= 0
        else None
    )

    if close_brace is None:
        return []

    members: list[str] = []

    for expression in _split_top_level(source[open_brace + 1 : close_brace]):
        candidate = expression.strip().lstrip("&")

        if _IDENTIFIER_RE.fullmatch(candidate) and candidate not in members:
            members.append(candidate)

    return members


def _call_module(
    call: GoFunctionCall | None,
    imports_by_alias: dict[str, str],
) -> str:
    if call is None:
        return ""

    return imports_by_alias.get(
        call.receiver or "",
        "",
    )


def _provider_name(
    module_path: str,
) -> str:
    if not module_path:
        return ""

    return module_path.rstrip("/").rsplit("/", 1)[-1]


def _call_arguments(
    source: str,
) -> list[str]:
    open_paren = source.find("(")
    close_paren = (
        _find_balanced_end(
            source,
            open_paren,
            "(",
            ")",
        )
        if open_paren >= 0
        else None
    )

    if close_paren is None:
        return []

    return _split_top_level(source[open_paren + 1 : close_paren])


def _split_top_level(
    value: str,
) -> list[str]:
    items: list[str] = []
    start = 0
    depths = {
        "(": 0,
        "[": 0,
        "{": 0,
    }
    closers = {
        ")": "(",
        "]": "[",
        "}": "{",
    }
    quote: str | None = None
    escaped = False

    for index, character in enumerate(value):
        if quote is not None:
            if quote != "`" and escaped:
                escaped = False

            elif quote != "`" and character == "\\":
                escaped = True

            elif character == quote:
                quote = None

            continue

        if character in {
            '"',
            "'",
            "`",
        }:
            quote = character

        elif character in depths:
            depths[character] += 1

        elif character in closers:
            depths[closers[character]] = max(
                0,
                depths[closers[character]] - 1,
            )

        elif character == "," and not any(depths.values()):
            item = value[start:index].strip()

            if item:
                items.append(item)

            start = index + 1

    tail = value[start:].strip()

    if tail:
        items.append(tail)

    return items


def _decode_go_string(
    value: str,
) -> str:
    text = value.strip()

    if len(text) < 2:
        return ""

    if text[0] == text[-1] == "`":
        return text[1:-1].replace("\r", "")

    if text[0] == text[-1] == '"':
        try:
            decoded = ast.literal_eval(text)

        except (
            SyntaxError,
            ValueError,
        ):
            return text[1:-1]

        return decoded if isinstance(decoded, str) else ""

    return ""


def _find_balanced_end(
    text: str,
    start: int,
    opener: str,
    closer: str,
) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False

    for index in range(
        start,
        len(text),
    ):
        character = text[index]

        if quote is not None:
            if quote != "`" and escaped:
                escaped = False

            elif quote != "`" and character == "\\":
                escaped = True

            elif character == quote:
                quote = None

            continue

        if character in {
            '"',
            "'",
            "`",
        }:
            quote = character

        elif character == opener:
            depth += 1

        elif character == closer:
            depth -= 1

            if depth == 0:
                return index

    return None


__all__ = ["EinoAdapter"]
