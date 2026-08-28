"""``google/generative-ai-go`` (Gemini) adapter.

Detects ``client.GenerativeModel("gemini-1.5-flash")`` calls — the SDK's
model-selection call — and emits a MODEL node per distinct model name found,
alongside a FRAMEWORK node for the SDK itself.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/google/generative-ai-go/genai"
_MODEL_CALL = "GenerativeModel"


class GoogleGenAIAdapter(GoFrameworkAdapter):
    """Detect ``google/generative-ai-go`` clients and model selection."""

    name = "google_genai_go"
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

        framework = self._fw_node(file_path, matched_import, display_name="Google Generative AI (Go)")
        framework.metadata.update({"framework": "google_genai_go", "provider": "google"})
        detections: list[ComponentDetection] = [framework]

        seen: set[str] = set()
        for call in result.function_calls:
            if call.function_name != _MODEL_CALL:
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
                confidence=0.9,
                metadata={
                    "framework": "google_genai_go",
                    "provider": "google",
                    "language": "golang",
                },
                file_path=file_path,
                line=call.line,
                snippet=call.source_snippet or f"{call.receiver}.GenerativeModel({model_name!r})",
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


__all__ = ["GoogleGenAIAdapter"]
