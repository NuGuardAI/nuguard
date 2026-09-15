"""``ollama/ollama/api`` adapter.

Detects request struct literals (``ChatRequest``, ``GenerateRequest``,
``EmbedRequest``, ``EmbeddingRequest``) and reads their ``Model`` field.
Only string-literal model values and same-file consts resolve — unresolved
identifiers contribute only the FRAMEWORK node.

Type matching is qualifier-aware: a request struct is attributed to this SDK
only when its qualifier matches the file's Ollama import (``api``, an
explicit alias, or a dot import). A local unqualified ``ChatRequest`` is not
treated as Ollama merely because the package is imported.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoImport, GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/ollama/ollama/api"
_PACKAGE_IDENT = "api"
_REQUEST_TYPES = {
    "ChatRequest",
    "GenerateRequest",
    "EmbedRequest",
    "EmbeddingRequest",
}


def _split_qualified_name(name: str) -> tuple[str, str]:
    """Return ``(qualifier, type_suffix)`` for a Go type name."""
    cleaned = name.replace("*", "").replace("&", "").strip()
    if not cleaned:
        return "", ""
    if "." not in cleaned:
        return "", cleaned
    qualifier, suffix = cleaned.rsplit(".", 1)
    return qualifier, suffix


def _qualifier_matches(qualifier: str, matched_import: GoImport) -> bool:
    """Return whether *qualifier* is in scope for the matched Ollama import."""
    alias = matched_import.alias
    if alias == "_":
        return False
    if alias == ".":
        return qualifier == ""
    expected = alias if alias else _PACKAGE_IDENT
    return qualifier == expected


class OllamaSDKGoAdapter(GoFrameworkAdapter):
    """Detect the official Ollama Go SDK client and model selection."""

    name = "ollama_sdk_go"
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

        framework = self._fw_node(file_path, matched_import, display_name="Ollama Go SDK")
        framework.metadata.update({"framework": "ollama_sdk_go", "provider": "ollama"})
        detections: list[ComponentDetection] = [framework]

        seen: set[str] = set()
        for inst in result.instantiations:
            if inst.kind != "struct_literal":
                continue
            qualifier, type_name = _split_qualified_name(inst.class_name)
            if type_name not in _REQUEST_TYPES:
                continue
            if not _qualifier_matches(qualifier, matched_import):
                continue
            model_name = self._resolve(inst, "Model")
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
                    "framework": "ollama_sdk_go",
                    "provider": "ollama",
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


__all__ = ["OllamaSDKGoAdapter"]
