"""``anthropics/anthropic-sdk-go`` adapter.

Detects request struct literals (``MessageNewParams``, ``CompletionNewParams``)
and reads their ``Model`` field. As with go-openai, only string-literal model
values resolve — SDK constants like ``anthropic.ModelClaude3_5SonnetLatest``
are unresolvable qualified identifiers, so those calls contribute only the
FRAMEWORK node.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/anthropics/anthropic-sdk-go"
_REQUEST_TYPES = {
    "MessageNewParams",
    "CompletionNewParams",
    "anthropic.MessageNewParams",
    "anthropic.CompletionNewParams",
}


class AnthropicSDKGoAdapter(GoFrameworkAdapter):
    """Detect ``anthropic-sdk-go`` clients and model selection."""

    name = "anthropic_sdk_go"
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

        framework = self._fw_node(file_path, matched_import, display_name="anthropic-sdk-go")
        framework.metadata.update({"framework": "anthropic_sdk_go", "provider": "anthropic"})
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
                    "framework": "anthropic_sdk_go",
                    "provider": "anthropic",
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

        return detections


__all__ = ["AnthropicSDKGoAdapter"]
