"""Standard-library ``net/http`` adapter.

``net/http`` routing has no verb in the call name — ``http.HandleFunc(path,
handler)`` (or ``mux.HandleFunc(...)`` on a ``*http.ServeMux``) registers a
handler for all methods unless the handler itself branches on
``r.Method``. Endpoints are therefore emitted with ``method: "ANY"``.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter
from ._http_endpoint_metadata import enrich_http_endpoint_detections

_MODULE = "net/http"
_HANDLER_CALLS = {"HandleFunc", "Handle"}


class NetHTTPAdapter(GoFrameworkAdapter):
    """Detect ``net/http`` handler registrations."""

    name = "net_http"
    priority = 45
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

        framework = self._fw_node(file_path, matched_import, display_name="net/http")
        detections: list[ComponentDetection] = [framework]

        seen: set[tuple[str, str]] = set()
        for call in result.function_calls:
            if call.function_name not in _HANDLER_CALLS:
                continue
            path = self._resolve(call, 0)
            if not path or not path.startswith("/"):
                continue

            key = ("ANY", path)
            if key in seen:
                continue
            seen.add(key)

            canon = f"endpoint:ANY:{path}"
            endpoint = ComponentDetection(
                component_type=ComponentType.API_ENDPOINT,
                canonical_name=canon,
                display_name=f"ANY {path}",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.85,
                metadata={
                    "framework": self.name,
                    "method": "ANY",
                    "endpoint": path,
                    "language": "golang",
                },
                file_path=file_path,
                line=call.line,
                snippet=call.source_snippet or f"http.{call.function_name}({path!r})",
                evidence_kind="ast_call",
            )
            endpoint.relationships.append(
                RelationshipHint(
                    source_canonical=framework.canonical_name,
                    source_type=ComponentType.FRAMEWORK,
                    target_canonical=canon,
                    target_type=ComponentType.API_ENDPOINT,
                    relationship_type="CALLS",
                )
            )
            detections.append(endpoint)

        return enrich_http_endpoint_detections(
            content,
            result,
            detections,
            framework=self.name,
            verb_names={
                name: "ANY"
                for name in _HANDLER_CALLS
            },
        )


__all__ = ["NetHTTPAdapter"]
