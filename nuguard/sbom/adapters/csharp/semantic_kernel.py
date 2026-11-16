"""C# adapter for Microsoft Semantic Kernel."""

from __future__ import annotations

import re
from typing import Any

from ...core.csharp_parser import CSharpParseResult, parse_csharp
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ..models_kb import get_model_details
from ._csharp_base import CSharpFrameworkAdapter
from ._source import (
    find_calls,
    first_argument,
    mask_non_code,
    method_source,
    resolve_expression,
    split_top_level,
    string_constants,
)

_SERVICE_CALLS: dict[str, str] = {
    "AddAzureOpenAIChatCompletion": "azure",
    "AddAzureOpenAITextEmbeddingGeneration": "azure",
    "AddOpenAIChatCompletion": "openai",
    "AddOpenAITextEmbeddingGeneration": "openai",
    "AddAnthropicChatCompletion": "anthropic",
    "AddOllamaChatCompletion": "ollama",
    "AddMistralChatCompletion": "mistral",
}
_PLUGIN_CALLS = {
    "AddFromType",
    "ImportPluginFromType",
    "AddFromObject",
    "ImportPluginFromObject",
    "AddFromPromptDirectory",
    "ImportPluginFromPromptDirectory",
    "CreateFromType",
}
_PLUGIN_NAME_POSITIONS: dict[str, tuple[int, ...]] = {
    "AddFromType": (0, 1),
    "ImportPluginFromType": (0, 1),
    "CreateFromType": (0, 1),
    "AddFromObject": (1, 2),
    "ImportPluginFromObject": (1, 2),
    "AddFromPromptDirectory": (1, 2),
    "ImportPluginFromPromptDirectory": (1, 2),
}
_PLANNER_CLASSES = {
    "ActionPlanner",
    "FunctionCallingStepwisePlanner",
    "HandlebarsPlanner",
    "SequentialPlanner",
    "StepwisePlanner",
}
_KERNEL_CALLS = {
    "CreateBuilder",
    "Build",
    "KernelBuilder",
}
_AGENT_INITIALIZER_RE = re.compile(
    r"(?:\bnew\s+ChatCompletionAgent\s*(?:\(\s*\))?"
    r"|\bChatCompletionAgent\s+@?[A-Za-z_]\w*\s*=\s*new\s*\(\s*\))\s*\{"
)
_CASE_LABEL_RE = re.compile(r"\bcase\s+(?P<label>[\w.]+)\s*:")
_AGENT_NAME_RE = re.compile(r"[A-Za-z][\w .-]{0,63}")
# Kernel functions that change state get privilege_scope db_write. Same idea as
# java_ai.py (camelCase-split write verbs), plus the create/add/register/transfer
# verbs SK plugins use for write operations.
_WRITE_VERBS = {
    "add",
    "cancel",
    "create",
    "delete",
    "insert",
    "patch",
    "purge",
    "refund",
    "register",
    "remove",
    "replace",
    "save",
    "transfer",
    "update",
    "upsert",
    "write",
}
_NAME_WRITE_VERBS = _WRITE_VERBS | {"approve", "close", "send"}
_NON_WRITE_CALLS = {
    "AddRange",
    "AddWithValue",
    "CreateCommand",
    "CreateParameter",
    "RemoveAll",
    "RemoveAt",
    "RemoveRange",
}


class CSharpSemanticKernelAdapter(CSharpFrameworkAdapter):
    """Detect Semantic Kernel services, plugins, planners, and prompts."""

    name = "csharp_semantic_kernel"
    priority = 40
    handles_namespaces = ["Microsoft.SemanticKernel"]

    def __init__(self) -> None:
        # Plugin class -> (base types, [KernelFunction] names), across all files.
        self._kernel_plugins: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = self._parse_result(
            content,
            file_path,
            parse_result,
        )

        calls = find_calls(
            content,
            _KERNEL_CALLS | set(_SERVICE_CALLS) | _PLUGIN_CALLS | _PLANNER_CLASSES,
        )
        has_kernel_call = any(
            (call.name == "CreateBuilder" and (call.receiver or "").split(".")[-1] == "Kernel")
            or (call.name == "KernelBuilder" and call.is_constructor)
            or call.name in _SERVICE_CALLS
            or call.name in _PLUGIN_CALLS
            or (call.name in _PLANNER_CLASSES and call.is_constructor)
            for call in calls
        )
        has_kernel_attribute = any(
            any(
                (
                    attribute.split("(", 1)[0].split(".")[-1].removesuffix("Attribute")
                    == "KernelFunction"
                )
                for attribute in method.attributes
            )
            for method in result.method_declarations
        )

        if not self._detect(result) and not has_kernel_call and not has_kernel_attribute:
            return []

        constants = string_constants(result)
        root = "framework:semantic_kernel"
        import_line = next(
            (
                item.line
                for item in result.using_directives
                if item.namespace.startswith("Microsoft.SemanticKernel")
            ),
            0,
        )
        detections: list[ComponentDetection] = [
            ComponentDetection(
                component_type=(ComponentType.FRAMEWORK),
                canonical_name=root,
                display_name=("Microsoft Semantic Kernel"),
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.98,
                metadata={
                    "framework": "semantic_kernel",
                    "language": "csharp",
                },
                file_path=file_path,
                line=import_line,
                snippet=("using Microsoft.SemanticKernel"),
                evidence_kind="ast_import",
            )
        ]

        builder_variables: set[str] = set()

        for call in calls:
            if call.name in _KERNEL_CALLS:
                receiver = call.receiver or ""
                is_kernel_builder = (
                    (call.name == "CreateBuilder" and receiver.split(".")[-1] == "Kernel")
                    or (call.name == "KernelBuilder" and call.is_constructor)
                    or (
                        call.name == "Build"
                        and (
                            receiver.split(".")[0] in builder_variables
                            or call.assigned_to in builder_variables
                        )
                    )
                )

                if not is_kernel_builder:
                    continue

                if (
                    call.name
                    in {
                        "CreateBuilder",
                        "KernelBuilder",
                    }
                    and call.assigned_to
                ):
                    builder_variables.add(call.assigned_to)

                instance_name = call.assigned_to or "kernel"
                canonical = canonicalize_text(f"semantic_kernel:{instance_name}")
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.FRAMEWORK),
                        canonical_name=canonical,
                        display_name=instance_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.94,
                        metadata={
                            "framework": ("semantic_kernel"),
                            "builder_call": (call.name),
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind="ast_call",
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.FRAMEWORK),
                                relationship_type=("CONTAINS"),
                            )
                        ],
                    )
                )
                continue

            if call.name in _SERVICE_CALLS:
                provider = _SERVICE_CALLS[call.name]
                model_name = first_argument(
                    call,
                    (
                        "modelId",
                        "model_id",
                        "deploymentName",
                        "deployment",
                        "aiModelId",
                    ),
                    constants,
                    0,
                )

                if not model_name:
                    continue

                canonical = canonicalize_text(model_name.lower())
                details = get_model_details(
                    model_name,
                    provider,
                    {},
                )
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.MODEL),
                        canonical_name=canonical,
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=(0.91 if model_name else 0.82),
                        metadata={
                            "framework": ("semantic_kernel"),
                            "provider": provider,
                            "registration": (call.name),
                            "language": "csharp",
                            **{key: value for key, value in details.items() if value is not None},
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind="ast_call",
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.MODEL),
                                relationship_type=("USES"),
                            )
                        ],
                    )
                )
                continue

            if call.name in _PLUGIN_CALLS:
                scope = max(
                    (
                        method
                        for method in result.method_declarations
                        if method.line <= call.line <= method.line_end
                    ),
                    key=lambda method: method.line,
                    default=None,
                )
                floor = (
                    sum(len(line) for line in content.splitlines(keepends=True)[: scope.line - 1])
                    if scope
                    else 0
                )
                plugin_type = _plugin_type(call, content, floor)
                plugin_name = _plugin_name(
                    call,
                    constants,
                )
                display = plugin_name or plugin_type or call.assigned_to

                if not display:
                    # An unnamed plugin of unknown type says nothing its functions don't.
                    continue
                canonical = canonicalize_text(f"semantic_kernel:plugin:{display}")
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.TOOL),
                        canonical_name=canonical,
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": ("semantic_kernel"),
                            "registration": (call.name),
                            "plugin_type": (plugin_type or None),
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind="ast_call",
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.TOOL),
                                relationship_type=("USES"),
                            )
                        ],
                    )
                )
                continue

            if call.name in _PLANNER_CLASSES and call.is_constructor:
                display = call.assigned_to or call.name
                canonical = canonicalize_text(f"semantic_kernel:planner:{display}")
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.AGENT),
                        canonical_name=canonical,
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": ("semantic_kernel"),
                            "planner_class": (call.name),
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind=("ast_instantiation"),
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.AGENT),
                                relationship_type=("USES"),
                            )
                        ],
                    )
                )

        for method in result.method_declarations:
            is_kernel_function = any(
                (
                    attribute.split(
                        "(",
                        1,
                    )[0]
                    .split(".")[-1]
                    .removesuffix("Attribute")
                    == "KernelFunction"
                )
                for attribute in method.attributes
            )

            if not is_kernel_function:
                continue

            canonical = canonicalize_text(f"semantic_kernel:function:{method.name}")
            metadata: dict[str, Any] = {
                "framework": ("semantic_kernel"),
                "registration": ("KernelFunctionAttribute"),
                "containing_type": (method.containing_type),
                "language": "csharp",
            }

            if _writes_state(method.name, method_source(content, method)):
                metadata["privilege_scope"] = "db_write"
                metadata["high_privilege"] = True

            detections.append(
                ComponentDetection(
                    component_type=(ComponentType.TOOL),
                    canonical_name=canonical,
                    display_name=method.name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.93,
                    metadata=metadata,
                    file_path=file_path,
                    line=method.line,
                    snippet=method.signature,
                    evidence_kind="ast_attribute",
                    relationships=[
                        RelationshipHint(
                            source_canonical=root,
                            source_type=(ComponentType.FRAMEWORK),
                            target_canonical=(canonical),
                            target_type=(ComponentType.TOOL),
                            relationship_type="USES",
                        )
                    ],
                )
            )

        detections.extend(
            self._agents(
                content,
                file_path,
                result,
                constants,
                root,
            )
        )

        for literal in result.string_literals:
            assigned = (literal.assigned_to or "").lower()
            prompt_name = any(
                token in assigned
                for token in (
                    "prompt",
                    "template",
                    "instruction",
                )
            )

            if not (literal.is_potential_prompt or prompt_name):
                continue

            if len(literal.value.strip()) < 12:
                continue

            display = literal.assigned_to or f"prompt_{literal.line}"
            canonical = canonicalize_text(f"semantic_kernel:prompt:{display}")
            detections.append(
                ComponentDetection(
                    component_type=(ComponentType.PROMPT),
                    canonical_name=canonical,
                    display_name=display,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.86,
                    metadata={
                        "framework": ("semantic_kernel"),
                        "role": "system",
                        "content": literal.value,
                        "char_count": len(literal.value),
                        "is_template": (literal.is_interpolated),
                        "template_variables": list(literal.interpolation_expressions),
                        "language": "csharp",
                    },
                    file_path=file_path,
                    line=literal.line,
                    snippet=literal.value[:160],
                    evidence_kind=("ast_string_literal"),
                    relationships=[
                        RelationshipHint(
                            source_canonical=root,
                            source_type=(ComponentType.FRAMEWORK),
                            target_canonical=(canonical),
                            target_type=(ComponentType.PROMPT),
                            relationship_type="USES",
                        )
                    ],
                )
            )

        return _dedupe(detections)

    def set_csharp_kernel_plugins(
        self,
        plugins: dict[str, tuple[tuple[str, ...], tuple[str, ...]]],
    ) -> None:
        """Receive the cross-file plugin index (type -> base types, kernel functions)."""
        self._kernel_plugins = dict(plugins)

    def _plugin_functions(self, plugin_type: str) -> list[str]:
        """Return kernel functions declared on *plugin_type* or inherited from its bases."""
        plugins = self._kernel_plugins
        functions: list[str] = []
        pending = [plugin_type]
        seen: set[str] = set()

        while pending:
            current = pending.pop()

            if current in seen or current not in plugins:
                continue

            seen.add(current)
            bases, names = plugins[current]
            functions.extend(name for name in names if name not in functions)
            pending.extend(bases)

        return functions

    def _agents(
        self,
        content: str,
        file_path: str,
        result: CSharpParseResult,
        constants: dict[str, str],
        root: str,
    ) -> list[ComponentDetection]:
        """Emit one AGENT per ``new ChatCompletionAgent { Name = ... }`` agent name.

        A name produced by a helper method that switches over an enum (``Name =
        GetAgentName(agentType)``) yields one agent per case; the same case labels
        in the ``Kernel = ...`` helper map each agent to the plugins it registers.
        """
        detections: list[ComponentDetection] = []
        masked = mask_non_code(content)
        methods = {method.name: method for method in result.method_declarations}
        group_chat = "AgentGroupChat" if re.search(r"\bnew\s+AgentGroupChat\b", masked) else None

        for match in _AGENT_INITIALIZER_RE.finditer(masked):
            close = _closing_brace(masked, match.end() - 1)

            if close is None:
                continue

            properties: dict[str, str] = {}

            for part in split_top_level(content[match.end() : close]):
                name, _, value = part.partition("=")

                if value and name.strip().isidentifier():
                    properties[name.strip()] = value.strip()

            names = _switch_values(
                properties.get("Name", ""),
                content,
                methods,
                constants,
                _string_cases,
            )
            plugins = _switch_values(
                properties.get("Kernel", ""),
                content,
                methods,
                constants,
                _plugin_cases,
            )
            line = masked.count("\n", 0, match.start()) + 1

            for label, agent_name in names.items():
                registered = plugins.get(label) or plugins.get("") or []
                plugin_types = list(dict.fromkeys(plugin_type for plugin_type, _ in registered))
                canonical = canonicalize_text(f"semantic_kernel:agent:{agent_name}")
                relationships = [
                    RelationshipHint(
                        source_canonical=root,
                        source_type=(ComponentType.FRAMEWORK),
                        target_canonical=(canonical),
                        target_type=(ComponentType.AGENT),
                        relationship_type=("USES"),
                    )
                ]

                for plugin_type, plugin_name in registered:
                    targets = [canonicalize_text(f"semantic_kernel:plugin:{plugin_name}")]
                    targets.extend(
                        canonicalize_text(f"semantic_kernel:function:{function}")
                        for function in self._plugin_functions(plugin_type)
                    )
                    relationships.extend(
                        RelationshipHint(
                            source_canonical=canonical,
                            source_type=(ComponentType.AGENT),
                            target_canonical=target,
                            target_type=(ComponentType.TOOL),
                            relationship_type="CALLS",
                        )
                        for target in targets
                    )

                metadata: dict[str, Any] = {
                    "framework": ("semantic_kernel"),
                    "agent_class": "ChatCompletionAgent",
                    "plugins": list(plugin_types),
                    "language": "csharp",
                }

                if group_chat:
                    metadata["orchestration"] = group_chat

                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.AGENT),
                        canonical_name=canonical,
                        display_name=agent_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata=metadata,
                        file_path=file_path,
                        line=line,
                        snippet=" ".join(content[match.start() : close + 1].split())[:240],
                        evidence_kind=("ast_instantiation"),
                        relationships=relationships,
                    )
                )

        return detections


def _closing_brace(masked: str, opening: int) -> int | None:
    depth = 0

    for index in range(opening, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1

            if depth == 0:
                return index

    return None


def _switch_sections(body: str) -> dict[str, str]:
    """Split a method body into ``case`` sections; the whole body when there are none."""
    masked = mask_non_code(body)
    labels = list(_CASE_LABEL_RE.finditer(masked))

    if not labels:
        return {"": body}

    sections: dict[str, str] = {}

    for index, label in enumerate(labels):
        end = labels[index + 1].start() if index + 1 < len(labels) else len(body)
        default = re.search(r"\bdefault\s*:", masked[label.end() : end])

        if default is not None:
            end = label.end() + default.start()

        sections[label.group("label").split(".")[-1]] = body[label.end() : end]

    return sections


def _switch_values(
    expression: str,
    content: str,
    methods: dict[str, Any],
    constants: dict[str, str],
    extract: Any,
) -> dict[str, Any]:
    """Resolve a property value directly, or per case label of a same-file helper method."""
    direct = extract(expression, content, constants)

    if direct:
        return {"": direct}

    helper = re.match(r"\s*(?:this\.)?(?P<name>[A-Za-z_]\w*)\s*\(", expression)

    if helper is None or helper.group("name") not in methods:
        return {}

    body = method_source(content, methods[helper.group("name")])
    values: dict[str, Any] = {}

    for label, section in _switch_sections(body).items():
        value = extract(section, content, constants)

        if value:
            values[label] = value

    return values


def _string_cases(text: str, content: str, constants: dict[str, str]) -> str:
    """Return the agent name a ``case`` section (or a plain expression) assigns."""
    stripped = text.strip()
    resolved = resolve_expression(stripped, constants)

    if resolved and resolved != stripped and _AGENT_NAME_RE.fullmatch(resolved):
        return resolved

    masked = mask_non_code(text)

    for assignment in re.finditer(r"(?:[^=!<>]=(?!=)|\breturn\b)", masked):
        literal = re.match(r'\s*"(?P<value>[^"\\]*)"', text[assignment.end() :])

        if literal is not None and _AGENT_NAME_RE.fullmatch(literal.group("value")):
            return literal.group("value")

    return ""


def _plugin_cases(text: str, content: str, constants: dict[str, str]) -> list[tuple[str, str]]:
    """Return ``(plugin type, plugin node name)`` pairs registered in a ``case`` section.

    A bare kernel identifier resolves to the plugins registered on it, or on the builder
    it was built from, anywhere in the file.
    """
    identifier = text.strip()

    if identifier.isidentifier():
        masked = mask_non_code(content)
        roots = {identifier}
        roots.update(
            built.group("builder")
            for built in re.finditer(
                rf"\b{re.escape(identifier)}\s*=\s*(?P<builder>[A-Za-z_]\w*)\s*\.\s*Build\s*\(",
                masked,
            )
        )
        calls = [
            call
            for call in find_calls(content, _PLUGIN_CALLS)
            if (call.receiver or "").split(".")[0] in roots or call.assigned_to in roots
        ]
        source = content
    else:
        calls = find_calls(text, _PLUGIN_CALLS)
        source = text

    return [
        (plugin_type, _plugin_name(call, constants) or plugin_type)
        for call in calls
        if (plugin_type := _plugin_type(call, source, content=content))
    ]


def _plugin_type(call: Any, source: str, floor: int = 0, content: str | None = None) -> str:
    """Return the plugin class: the generic argument, or the ``new T(...)`` behind the object.

    *source* is the text *call* was found in; an object variable resolves to its nearest
    declaration between *floor* and the call, falling back to the first one in *content*.
    """
    if call.generic_arguments:
        return str(call.generic_arguments[0]).split(".")[-1]

    if not call.name.endswith("FromObject") or not call.positional_arguments:
        return ""

    argument = call.positional_arguments[0].strip()
    inline = re.match(r"new\s+(?P<type>[A-Za-z_][\w.]*)\s*\(", argument)

    if inline is not None:
        return inline.group("type").split(".")[-1]

    if not argument.isidentifier():
        return ""

    pattern = re.compile(
        rf"(?:\b{re.escape(argument)}\s*=\s*new\s+(?P<type>[A-Za-z_][\w.]*)\s*\("
        rf"|\b(?P<declared>[A-Za-z_][\w.]*)\s+{re.escape(argument)}\s*=\s*new\s*\()"
    )
    scoped = list(pattern.finditer(mask_non_code(source), floor, call.start))
    declared = scoped[-1] if scoped else pattern.search(mask_non_code(content or source))

    if declared is None:
        return ""

    return (declared.group("type") or declared.group("declared")).split(".")[-1]


def _writes_state(name: str, body: str) -> bool:
    """Return whether a kernel function's name or awaited service calls write state."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).lower().split()

    if words and words[0] in _NAME_WRITE_VERBS:
        return True

    for call in find_calls(body):
        if call.name in _NON_WRITE_CALLS:
            continue

        call_words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", call.name).lower().split()

        if call_words[:3] == ["execute", "non", "query"]:
            return True

        # Bare Add/Remove are collection operations, not writes.
        if len(call_words) > 1 and call_words[0] in _WRITE_VERBS:
            return True

    return False


def collect_kernel_plugins(
    content: str,
    file_path: str = "",
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Return ``type -> (base types, kernel function names)`` for one C# file."""
    if "class" not in content:
        return {}

    result = parse_csharp(content, file_path)
    functions: dict[str, list[str]] = {}

    for method in result.method_declarations:
        if method.containing_type and any(
            attribute.split("(", 1)[0].split(".")[-1].removesuffix("Attribute") == "KernelFunction"
            for attribute in method.attributes
        ):
            functions.setdefault(method.containing_type, []).append(method.name)

    index: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}

    for declaration in result.type_declarations:
        bases = tuple(
            base.split("<", 1)[0].split(".")[-1].strip() for base in declaration.base_types
        )
        names = tuple(functions.get(declaration.name, ()))

        if bases or names:
            index[declaration.name] = (bases, names)

    return index


def _plugin_name(
    call: Any,
    constants: dict[str, str],
) -> str:
    """Resolve a plugin name according to the matched overload."""
    named = first_argument(
        call,
        (
            "pluginName",
            "name",
        ),
        constants,
        None,
    )

    if named:
        return named

    for position in _PLUGIN_NAME_POSITIONS.get(
        call.name,
        (),
    ):
        positional = first_argument(
            call,
            (),
            constants,
            position,
        )

        if positional:
            return positional

    return ""


def _dedupe(
    detections: list[ComponentDetection],
) -> list[ComponentDetection]:
    seen: set[tuple[ComponentType, str]] = set()
    result: list[ComponentDetection] = []

    for detection in detections:
        key = (
            detection.component_type,
            detection.canonical_name,
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(detection)

    return result


__all__ = ["CSharpSemanticKernelAdapter", "collect_kernel_plugins"]
