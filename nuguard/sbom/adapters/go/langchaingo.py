"""``tmc/langchaingo`` adapter.

langchaingo spans many subpackages (``llms/openai``, ``llms/anthropic``,
``llms/googleai``, ``agents``, ``tools``, ``chains``, ...) that all import
under the ``github.com/tmc/langchaingo`` module root, so a single
FRAMEWORK-node import gate covers the whole family.

Model selection is provider-agnostic in langchaingo: every ``llms/*``
provider package exposes a ``WithModel("model-id")`` functional option
passed to its constructor (``openai.New(openai.WithModel(...))``,
``anthropic.New(anthropic.WithModel(...))``, ...), so a single
call-name scan covers all of them without per-provider branching.

Also detects a type implementing langchaingo's ``tools.Tool`` interface
(``Name() string``, ``Description() string``,
``Call(ctx context.Context, input string) (string, error)``, verified
against pkg.go.dev) via ``go_parser``'s function-declaration parsing:
methods are grouped by ``receiver_type`` and checked for all three
signatures. The tool's actual name/description content (the string a
``return "..."`` in the method *body* would produce) isn't extracted —
that needs method-body-statement parsing, a further capability
``go_parser`` doesn't have — so the receiver type name is used as the
display name instead (``metadata["name_source"] = "receiver_type"`` marks
this explicitly rather than silently). ``agents.Executor`` construction is
still out of scope.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoFunctionDeclaration, GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/tmc/langchaingo"
_MODEL_OPTION_CALL = "WithModel"
_TOOL_INTERFACE_CONFIDENCE = 0.75


def _is_no_arg_string_getter(decl: GoFunctionDeclaration) -> bool:
    """``func (T) X() string`` — the shape of ``Name()``/``Description()``."""
    return (
        not decl.parameters
        and len(decl.results) == 1
        and decl.results[0].type == "string"
    )


def _is_tool_call_method(decl: GoFunctionDeclaration) -> bool:
    """``func (T) Call(ctx context.Context, input string) (string, error)``."""
    if len(decl.parameters) != 2 or len(decl.results) != 2:
        return False
    ctx_param, input_param = decl.parameters
    if "Context" not in ctx_param.type or input_param.type != "string":
        return False
    return decl.results[0].type == "string" and decl.results[1].type == "error"


class LangChainGoAdapter(GoFrameworkAdapter):
    """Detect ``langchaingo`` usage and provider-agnostic model selection."""

    name = "langchaingo"
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

        framework = self._fw_node(file_path, matched_import, display_name="LangChainGo")
        framework.metadata.update({"framework": "langchaingo"})
        detections: list[ComponentDetection] = [framework]

        seen: set[str] = set()
        for call in result.function_calls:
            if call.function_name != _MODEL_OPTION_CALL:
                continue
            model_name = self._resolve(call, 0)
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
                    "framework": "langchaingo",
                    "language": "golang",
                },
                file_path=file_path,
                line=call.line,
                snippet=call.source_snippet or f"WithModel({model_name!r})",
                evidence_kind="ast_call",
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

        by_receiver_type: dict[str, list[GoFunctionDeclaration]] = {}
        for decl in result.function_declarations:
            if decl.receiver_type:
                by_receiver_type.setdefault(decl.receiver_type, []).append(decl)

        for receiver_type, decls in by_receiver_type.items():
            by_name = {decl.name: decl for decl in decls}
            name_decl = by_name.get("Name")
            description_decl = by_name.get("Description")
            call_decl = by_name.get("Call")
            if name_decl is None or description_decl is None or call_decl is None:
                continue
            if not (
                _is_no_arg_string_getter(name_decl)
                and _is_no_arg_string_getter(description_decl)
                and _is_tool_call_method(call_decl)
            ):
                continue

            tool_canon = canonicalize_text(f"langchaingo_tool:{file_path}:{receiver_type}")
            tool = ComponentDetection(
                component_type=ComponentType.TOOL,
                canonical_name=tool_canon,
                display_name=receiver_type,
                adapter_name=self.name,
                priority=self.priority,
                confidence=_TOOL_INTERFACE_CONFIDENCE,
                metadata={
                    "framework": "langchaingo",
                    "detection_kind": "interface_satisfaction",
                    "name_source": "receiver_type",
                    "language": "golang",
                },
                file_path=file_path,
                line=call_decl.line,
                snippet=f"func ({receiver_type}) Call(ctx, input) (string, error)",
                evidence_kind="ast_declaration",
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


__all__ = ["LangChainGoAdapter"]
