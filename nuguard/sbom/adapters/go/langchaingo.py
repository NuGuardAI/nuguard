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

TOOL/agent extraction (mapping langchaingo ``tools.Tool`` implementations
or ``agents.Executor`` construction to AGENT/TOOL nodes) is out of scope
here — that needs interface-implementation analysis that isn't available
from ``go_parser``'s call/instantiation extraction alone.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/tmc/langchaingo"
_MODEL_OPTION_CALL = "WithModel"


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

        return detections


__all__ = ["LangChainGoAdapter"]
