"""LangChainGo framework adapter for NuGuard SBOM extraction.

Detects ``github.com/tmc/langchaingo`` usage and emits:

- a ``FRAMEWORK`` node for LangChainGo;
- ``MODEL`` nodes from constructors in ``langchaingo/llms`` packages;
- ``TOOL`` nodes from concrete values in ``[]tools.Tool`` collections;
- ``AGENT`` nodes from ``agents.NewOneShotAgent`` and
  ``agents.NewConversationalAgent`` calls.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ._go_base import GoFrameworkAdapter

_LANGCHAINGO_MODULE = "github.com/tmc/langchaingo"
_AGENT_IMPORT = f"{_LANGCHAINGO_MODULE}/agents"
_LLM_IMPORT = f"{_LANGCHAINGO_MODULE}/llms"
_TOOL_IMPORT = f"{_LANGCHAINGO_MODULE}/tools"

_AGENT_CONSTRUCTORS = {
    "NewOneShotAgent",
    "NewConversationalAgent",
}

_MODEL_OPTION_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?"
    r"With(?:Default)?Model(?:Name|ID|Id)?\s*\(\s*"
    r'(?P<value>"(?:\\.|[^"\\])*"|`[^`]*`)'
)

_TOOL_COLLECTION_RE = re.compile(r"\[\]\s*(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\.Tool\{")

_TOOL_LITERAL_RE = re.compile(
    r"^(?:&\s*)?"
    r"(?:(?P<receiver>[A-Za-z_][A-Za-z0-9_]*)\.)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\{"
)


class LangChainGoAdapter(GoFrameworkAdapter):
    """Detect LangChainGo agents, models, and tools."""

    name = "langchaingo"
    priority = 30
    handles_imports = [_LANGCHAINGO_MODULE]

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

        agent_aliases = self._aliases_for_prefix(
            result,
            _AGENT_IMPORT,
        )
        model_aliases = self._aliases_for_prefix(
            result,
            _LLM_IMPORT,
        )
        tool_aliases = self._aliases_for_prefix(
            result,
            _TOOL_IMPORT,
        )

        framework = self._fw_node(
            file_path,
            matched_import,
            display_name="LangChainGo",
        )
        framework.metadata.update(
            {
                "framework": "langchaingo",
                "language": "golang",
                "module": _LANGCHAINGO_MODULE,
            }
        )

        return [
            framework,
            *self._models(
                result,
                model_aliases,
                file_path,
            ),
            *self._tools(
                content,
                result,
                tool_aliases,
                file_path,
            ),
            *self._agents(
                result,
                agent_aliases,
                file_path,
            ),
        ]

    @staticmethod
    def _aliases_for_prefix(
        result: GoParseResult,
        prefix: str,
    ) -> dict[str, str]:
        aliases: dict[str, str] = {}

        for imported in result.imports:
            if imported.path != prefix and not imported.path.startswith(f"{prefix}/"):
                continue

            if imported.alias == "_":
                continue

            alias = "" if imported.alias == "." else imported.alias
            if alias is None:
                alias = imported.path.rsplit("/", 1)[-1]

            aliases[alias] = imported.path

        return aliases

    def _models(
        self,
        result: GoParseResult,
        aliases: dict[str, str],
        file_path: str,
    ) -> list[ComponentDetection]:
        detections: dict[str, ComponentDetection] = {}

        for instantiation in result.instantiations:
            receiver, constructor = _split_qualified(instantiation.class_name)

            # Dot-imported provider constructors cannot be distinguished from
            # unrelated local New* functions with the current parser contract.
            if not receiver or receiver not in aliases or not constructor.startswith("New"):
                continue

            provider = _provider_from_import(aliases[receiver])
            model_name = _model_name(instantiation.source_snippet or "")
            identity = model_name or provider
            canonical = canonicalize_text(f"langchaingo:model:{identity}")

            metadata: dict[str, Any] = {
                "framework": "langchaingo",
                "language": "golang",
                "module": aliases[receiver],
                "provider": provider,
                "creation_method": instantiation.class_name,
            }

            if model_name:
                metadata["model_name"] = model_name

            if instantiation.assigned_to:
                metadata["assigned_to"] = instantiation.assigned_to

            detections.setdefault(
                canonical,
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=canonical,
                    display_name=model_name or provider,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.94 if model_name else 0.86,
                    metadata=metadata,
                    file_path=file_path,
                    line=instantiation.line,
                    snippet=(instantiation.source_snippet or f"{instantiation.class_name}(...)"),
                    evidence_kind="ast_instantiation",
                ),
            )

        return list(detections.values())

    def _tools(
        self,
        content: str,
        result: GoParseResult,
        aliases: dict[str, str],
        file_path: str,
    ) -> list[ComponentDetection]:
        detections: dict[str, ComponentDetection] = {}

        # Direct LangChainGo tool package struct literals.
        for instantiation in result.instantiations:
            if instantiation.kind != "struct_literal":
                continue

            receiver, type_name = _split_qualified(instantiation.class_name)

            # Avoid treating every unqualified struct in a file with a dot
            # import as a LangChainGo tool.
            if not receiver or receiver not in aliases:
                continue

            self._add_tool(
                detections,
                file_path=file_path,
                line=instantiation.line,
                snippet=(instantiation.source_snippet or f"{instantiation.class_name}{{}}"),
                tool_name=type_name,
                tool_type=instantiation.class_name,
                module=aliases[receiver],
                assigned_to=instantiation.assigned_to,
                confidence=0.92,
            )

        # Custom local tool implementations can only be identified safely when
        # they occur as concrete values in a typed []tools.Tool collection.
        masked = _mask_non_code(content)

        for match in _TOOL_COLLECTION_RE.finditer(masked):
            alias = match.group("alias")
            if alias not in aliases:
                continue

            open_brace = match.end() - 1
            close_brace = _find_balanced_end(
                masked,
                open_brace,
                "{",
                "}",
            )
            if close_brace is None:
                continue

            collection_module = aliases[alias]
            body_start = open_brace + 1
            body_masked = masked[body_start:close_brace]

            for item_start, item_end in _top_level_spans(body_masked):
                masked_item = body_masked[item_start:item_end]
                literal = _TOOL_LITERAL_RE.match(masked_item)
                if literal is None:
                    continue

                receiver = literal.group("receiver") or ""
                tool_name = literal.group("name")

                if receiver and receiver not in aliases:
                    continue

                absolute_start = body_start + item_start
                original_item = content[absolute_start : body_start + item_end].strip()

                self._add_tool(
                    detections,
                    file_path=file_path,
                    line=content.count(
                        "\n",
                        0,
                        absolute_start,
                    )
                    + 1,
                    snippet=original_item,
                    tool_name=tool_name,
                    tool_type=(f"{receiver}.{tool_name}" if receiver else tool_name),
                    module=aliases.get(
                        receiver,
                        collection_module,
                    ),
                    assigned_to=None,
                    confidence=0.90 if receiver else 0.84,
                )

        return list(detections.values())

    def _add_tool(
        self,
        detections: dict[str, ComponentDetection],
        *,
        file_path: str,
        line: int,
        snippet: str,
        tool_name: str,
        tool_type: str,
        module: str,
        assigned_to: str | None,
        confidence: float,
    ) -> None:
        canonical = canonicalize_text(f"langchaingo:tool:{tool_name}")

        metadata: dict[str, Any] = {
            "framework": "langchaingo",
            "language": "golang",
            "module": module,
            "tool_type": tool_type,
            "creation_method": "struct_literal",
        }

        if assigned_to:
            metadata["assigned_to"] = assigned_to

        detections.setdefault(
            canonical,
            ComponentDetection(
                component_type=ComponentType.TOOL,
                canonical_name=canonical,
                display_name=tool_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=confidence,
                metadata=metadata,
                file_path=file_path,
                line=line,
                snippet=snippet,
                evidence_kind="ast_instantiation",
            ),
        )

    def _agents(
        self,
        result: GoParseResult,
        aliases: dict[str, str],
        file_path: str,
    ) -> list[ComponentDetection]:
        detections: dict[str, ComponentDetection] = {}

        for instantiation in result.instantiations:
            receiver, constructor = _split_qualified(instantiation.class_name)

            # Empty receiver is accepted only for exact, known agent
            # constructors reached through a dot import.
            if receiver not in aliases or constructor not in _AGENT_CONSTRUCTORS:
                continue

            agent_name = instantiation.assigned_to or _agent_display_name(constructor)
            canonical = canonicalize_text(f"langchaingo:agent:{agent_name}")

            metadata: dict[str, Any] = {
                "framework": "langchaingo",
                "language": "golang",
                "module": aliases[receiver],
                "agent_type": constructor.removeprefix("New"),
                "creation_method": instantiation.class_name,
            }

            if instantiation.assigned_to:
                metadata["assigned_to"] = instantiation.assigned_to

            detections.setdefault(
                canonical,
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=canonical,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.94,
                    metadata=metadata,
                    file_path=file_path,
                    line=instantiation.line,
                    snippet=(instantiation.source_snippet or f"{instantiation.class_name}(...)"),
                    evidence_kind="ast_instantiation",
                ),
            )

        return list(detections.values())


def _provider_from_import(
    import_path: str,
) -> str:
    suffix = import_path.removeprefix(f"{_LLM_IMPORT}/")

    if suffix == import_path or not suffix:
        return "langchaingo"

    return suffix.replace("/", "_")


def _model_name(
    source: str,
) -> str:
    match = _MODEL_OPTION_RE.search(source)
    if match is None:
        return ""

    raw = match.group("value")

    if raw.startswith("`") and raw.endswith("`"):
        return raw[1:-1].replace("\r", "")

    try:
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        return ""

    return value if isinstance(value, str) else ""


def _split_qualified(
    name: str,
) -> tuple[str, str]:
    receiver, separator, symbol = name.rpartition(".")

    if not separator:
        return "", name

    return receiver, symbol


def _agent_display_name(
    constructor: str,
) -> str:
    raw = constructor.removeprefix("New").removesuffix("Agent")

    return (
        re.sub(
            r"(?<!^)(?=[A-Z])",
            " ",
            raw,
        ).strip()
        + " Agent"
    )


def _mask_non_code(
    value: str,
) -> str:
    """Blank Go comments and literals while preserving offsets and newlines."""

    masked = list(value)
    index = 0

    def blank(
        start: int,
        end: int,
    ) -> None:
        for position in range(start, end):
            if masked[position] != "\n":
                masked[position] = " "

    while index < len(value):
        if value.startswith("//", index):
            end = value.find("\n", index)
            end = len(value) if end == -1 else end
            blank(index, end)
            index = end
            continue

        if value.startswith("/*", index):
            end = value.find("*/", index + 2)
            end = len(value) if end == -1 else end + 2
            blank(index, end)
            index = end
            continue

        if value[index] in {'"', "'", "`"}:
            quote = value[index]
            start = index
            index += 1

            while index < len(value):
                if quote != "`" and value[index] == "\\":
                    index += 2
                    continue

                if value[index] == quote:
                    index += 1
                    break

                index += 1

            blank(
                start,
                min(index, len(value)),
            )
            continue

        index += 1

    return "".join(masked)


def _find_balanced_end(
    text: str,
    start: int,
    opener: str,
    closer: str,
) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if quote is not None:
            if quote != "`" and escaped:
                escaped = False
            elif quote != "`" and char == "\\":
                escaped = True
            elif char == quote:
                quote = None

            continue

        if char in {'"', "'", "`"}:
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1

            if depth == 0:
                return index

    return None


def _top_level_spans(
    value: str,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
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

    def append_span(
        raw_start: int,
        raw_end: int,
    ) -> None:
        segment = value[raw_start:raw_end]
        if not segment.strip():
            return

        leading = len(segment) - len(segment.lstrip())
        trailing = len(segment.rstrip())
        spans.append(
            (
                raw_start + leading,
                raw_start + trailing,
            )
        )

    for index, char in enumerate(value):
        if char in depths:
            depths[char] += 1
        elif char in closers:
            depths[closers[char]] = max(
                0,
                depths[closers[char]] - 1,
            )
        elif char == "," and not any(depths.values()):
            append_span(
                start,
                index,
            )
            start = index + 1

    append_span(
        start,
        len(value),
    )

    return spans
