"""``sashabaranov/go-openai`` adapter.

Detects request struct literals (``ChatCompletionRequest``,
``CompletionRequest``, ``EmbeddingRequest``, ...) and reads their ``Model``
field. Only string-literal model values are resolvable — SDK constants like
``openai.GPT4`` are qualified identifiers that ``go_parser`` cannot resolve
to a value, so those requests contribute only the FRAMEWORK node (still
useful presence evidence, just without a distinct MODEL node).

Also detects hand-built ``openai.Tool``/``openai.FunctionDefinition`` struct
literals — the Go analogue of
``nuguard/sbom/adapters/python/openai_function_schema.py``'s dict-literal
tool-schema detection. Verified against the current go-openai source:
``Tool.Function`` is ``*FunctionDefinition`` directly (no wrapper type), so
``inst.args["Function"]`` already resolves to a nested dict via
``go_parser``'s existing recursive composite-literal handling, the same
mechanism used above for ``ChatCompletionRequest{Model: "..."}``. Unlike the
Python adapter, there's no same-file dispatcher lookup (``if tool_name ==
"x": ...``) here — that needs function-*declaration* parsing, which
``go_parser`` doesn't have yet.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/sashabaranov/go-openai"
_REQUEST_TYPES = {
    "ChatCompletionRequest",
    "CompletionRequest",
    "EmbeddingRequest",
    "ImageRequest",
    "AudioRequest",
    "openai.ChatCompletionRequest",
    "openai.CompletionRequest",
    "openai.EmbeddingRequest",
    "openai.ImageRequest",
    "openai.AudioRequest",
}
_TOOL_TYPES = {"Tool", "openai.Tool"}
_FUNCTION_DEF_TYPES = {"FunctionDefinition", "openai.FunctionDefinition"}
_TOOL_SCHEMA_CONFIDENCE = 0.85


class GoOpenAIAdapter(GoFrameworkAdapter):
    """Detect ``go-openai`` clients and model selection."""

    name = "go_openai"
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

        framework = self._fw_node(file_path, matched_import, display_name="go-openai")
        framework.metadata.update({"framework": "go_openai", "provider": "openai"})
        detections: list[ComponentDetection] = [framework]

        seen: set[str] = set()
        for inst in result.instantiations:
            if inst.class_name not in _REQUEST_TYPES:
                continue
            model_name = self._clean(inst.args.get("Model"))
            if not model_name or model_name in seen:
                continue
            seen.add(model_name)

            canon = canonicalize_text(model_name.lower())
            model = ComponentDetection(
                component_type=ComponentType.MODEL,
                canonical_name=canon,
                display_name=model_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.85,
                metadata={
                    "framework": "go_openai",
                    "provider": "openai",
                    "language": "golang",
                },
                file_path=file_path,
                line=inst.line,
                snippet=inst.source_snippet or f"{inst.class_name}{{Model: {model_name!r}}}",
                evidence_kind="ast_instantiation",
            )
            model.relationships.append(
                RelationshipHint(
                    source_canonical=framework.canonical_name,
                    source_type=ComponentType.FRAMEWORK,
                    target_canonical=canon,
                    target_type=ComponentType.MODEL,
                    relationship_type="USES",
                )
            )
            detections.append(model)

        seen_tools: set[str] = set()
        for inst in result.instantiations:
            func_def: dict[str, Any] | None = None
            if inst.class_name in _TOOL_TYPES:
                candidate = inst.args.get("Function")
                if isinstance(candidate, dict):
                    func_def = candidate
            elif inst.class_name in _FUNCTION_DEF_TYPES:
                func_def = inst.args

            if func_def is None:
                continue

            tool_name = self._clean(func_def.get("Name"))
            if not tool_name or tool_name in seen_tools:
                continue
            seen_tools.add(tool_name)

            description = self._clean(func_def.get("Description"))
            tool_canon = canonicalize_text(f"go_openai_function_schema:{tool_name}")
            tool = ComponentDetection(
                component_type=ComponentType.TOOL,
                canonical_name=tool_canon,
                display_name=tool_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=_TOOL_SCHEMA_CONFIDENCE,
                metadata={
                    "framework": "go_openai",
                    "language": "golang",
                    **({"description": description[:200]} if description else {}),
                },
                file_path=file_path,
                line=inst.line,
                snippet=inst.source_snippet or f"{inst.class_name}{{...}}",
                evidence_kind="ast_instantiation",
            )
            tool.relationships.append(
                RelationshipHint(
                    source_canonical=framework.canonical_name,
                    source_type=ComponentType.FRAMEWORK,
                    target_canonical=tool_canon,
                    target_type=ComponentType.TOOL,
                    relationship_type="CALLS",
                )
            )
            detections.append(tool)

        return detections


__all__ = ["GoOpenAIAdapter"]
