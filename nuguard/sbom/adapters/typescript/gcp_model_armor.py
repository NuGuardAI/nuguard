"""GCP Model Armor + Vertex/Gemini safety-settings TypeScript/JavaScript adapter.

Mirrors ``python/gcp_model_armor.py``:
- ``@google-cloud/modelarmor`` — ``ModelArmorClient`` + ``sanitizeUserPrompt``/
  ``sanitizeModelResponse`` calls → GUARDRAIL.
- ``@google/generative-ai`` / ``@google-cloud/vertexai`` — a
  ``safetySettings: [...]`` field passed to ``generateContent(...)`` →
  GUARDRAIL, lower confidence (kwarg-presence heuristic).
"""

from __future__ import annotations

import re
from typing import Any

from ...core.ts_parser import TSParseResult, parse_typescript
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ._ts_regex import TSFrameworkAdapter

_MODEL_ARMOR_PACKAGES = ["@google-cloud/modelarmor"]
_GEMINI_PACKAGES = ["@google/generative-ai", "@google-cloud/vertexai"]
_MODEL_ARMOR_METHODS = {"sanitizeUserPrompt", "sanitizeModelResponse"}
_SAFETY_SETTINGS_RE = re.compile(r"\bsafetySettings\s*:")


class GCPModelArmorTSAdapter(TSFrameworkAdapter):
    """Detect GCP Model Armor and Vertex/Gemini safety-settings usage in TS/JS."""

    name = "gcp_model_armor_ts"
    priority = 30
    handles_imports = _MODEL_ARMOR_PACKAGES + _GEMINI_PACKAGES

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result: TSParseResult = (
            parse_result
            if isinstance(parse_result, TSParseResult)
            else parse_typescript(content, file_path)
        )
        detected: list[ComponentDetection] = []
        lines = (result.source or content).splitlines()

        has_model_armor = any(
            pkg in imp.module or imp.module == pkg
            for imp in result.imports
            for pkg in _MODEL_ARMOR_PACKAGES
        )
        if has_model_armor:
            for inst in result.instantiations:
                if inst.class_name != "ModelArmorClient":
                    continue
                detected.append(
                    self._node(
                        file_path,
                        inst.line_start,
                        inst.source_snippet or "new ModelArmorClient(...)",
                        "ast_instantiation",
                        "model_armor",
                        "GCP Model Armor",
                        0.88,
                    )
                )
            for call in result.function_calls:
                method = call.method_name or call.function_name.split(".")[-1]
                if method not in _MODEL_ARMOR_METHODS:
                    continue
                detected.append(
                    self._node(
                        file_path,
                        call.line_start,
                        call.source_snippet or f"client.{method}(...)",
                        "ast_call",
                        "model_armor",
                        "GCP Model Armor",
                        0.88,
                    )
                )

        for call in result.function_calls:
            method = call.method_name or call.function_name.split(".")[-1]
            if method != "generateContent":
                continue
            snippet_lines = lines[call.line_start - 1 : max(call.line_end, call.line_start)]
            if not _SAFETY_SETTINGS_RE.search("\n".join(snippet_lines)):
                continue
            detected.append(
                self._node(
                    file_path,
                    call.line_start,
                    "model.generateContent({ safetySettings: ... })",
                    "regex",
                    "vertex_safety_settings",
                    "Vertex/Gemini Safety Settings",
                    0.75,
                )
            )

        return detected

    def _node(
        self,
        file_path: str,
        line: int,
        snippet: str,
        evidence_kind: str,
        guardrail_type: str,
        display_name: str,
        confidence: float,
    ) -> ComponentDetection:
        return ComponentDetection(
            component_type=ComponentType.GUARDRAIL,
            canonical_name=canonicalize_text(f"gcp_{guardrail_type}:{file_path}:{line}"),
            display_name=display_name,
            adapter_name=self.name,
            priority=self.priority,
            confidence=confidence,
            metadata={
                "framework": "gcp_model_armor",
                "vendor": "gcp",
                "guardrail_type": guardrail_type,
                "detection_kind": "framework_native" if guardrail_type == "model_armor" else "heuristic",
                "language": "typescript",
            },
            file_path=file_path,
            line=line,
            snippet=snippet,
            evidence_kind=evidence_kind,
        )


__all__ = ["GCPModelArmorTSAdapter"]
