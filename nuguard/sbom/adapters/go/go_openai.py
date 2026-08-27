"""``sashabaranov/go-openai`` adapter.

Detects request struct literals (``ChatCompletionRequest``,
``CompletionRequest``, ``EmbeddingRequest``, ...) and reads their ``Model``
field. Only string-literal model values are resolvable — SDK constants like
``openai.GPT4`` are qualified identifiers that ``go_parser`` cannot resolve
to a value, so those requests contribute only the FRAMEWORK node (still
useful presence evidence, just without a distinct MODEL node).
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

        return detections


__all__ = ["GoOpenAIAdapter"]
