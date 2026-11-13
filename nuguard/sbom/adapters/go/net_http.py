"""Standard-library ``net/http`` adapter.

``net/http`` routing has no verb in the call name — ``http.HandleFunc(path,
handler)`` (or ``mux.HandleFunc(...)`` on a ``*http.ServeMux``) registers a
handler for all methods unless the handler itself branches on
``r.Method``. Such endpoints are emitted with ``method: "ANY"``.

Go 1.22 patterns carry the method and an optional host in the pattern itself
(``"POST /api/v1/conversations"``, ``"GET example.com/x/{id}"``); those are
emitted with the real method and path, ``{name}`` wildcards kept verbatim.
"""

from __future__ import annotations

import re
from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter
from ._http_endpoint_metadata import enrich_http_endpoint_detections

_MODULE = "net/http"
_HANDLER_CALLS = {"HandleFunc", "Handle"}
# ``[METHOD ][HOST]/[PATH]`` — see net/http.ServeMux "Patterns".
_PATTERN_RE = re.compile(r"^(?:(?P<method>[A-Z]+)[ \t]+)?(?P<host>[^/\s]*)(?P<path>/\S*)$")


def _split_pattern(pattern: str) -> tuple[str, str, str] | None:
    """Return ``(method, host, path)`` for a ServeMux pattern, or None."""
    match = _PATTERN_RE.fullmatch(pattern.strip())
    if match is None:
        return None
    return match.group("method") or "ANY", match.group("host"), match.group("path")


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
            split = _split_pattern(self._resolve(call, 0) or "")
            if split is None:
                continue
            method, host, path = split

            key = (method, f"{host}{path}")
            if key in seen:
                continue
            seen.add(key)

            canon = f"endpoint:{method}:{host}{path}"
            metadata = {
                "framework": self.name,
                "method": method,
                "endpoint": f"{host}{path}",
                "language": "golang",
            }
            if host:
                metadata["host"] = host
            endpoint = ComponentDetection(
                component_type=ComponentType.API_ENDPOINT,
                canonical_name=canon,
                display_name=f"{method} {host}{path}",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.85,
                metadata=metadata,
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

        enriched = enrich_http_endpoint_detections(
            content,
            result,
            detections,
            framework=self.name,
            verb_names={
                name: "ANY"
                for name in _HANDLER_CALLS
            },
            route_key=_route_key,
        )
        for detection in detections:
            route_host = detection.metadata.get("host")
            if detection.component_type == ComponentType.API_ENDPOINT and route_host:
                detection.metadata["endpoint"] = detection.metadata["endpoint"][len(route_host):]
        return enriched


def _route_key(_method: str, pattern: str) -> tuple[str, str] | None:
    split = _split_pattern(pattern)
    return (split[0], f"{split[1]}{split[2]}") if split else None


__all__ = ["NetHTTPAdapter"]
