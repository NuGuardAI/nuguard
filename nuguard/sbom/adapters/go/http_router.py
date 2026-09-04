"""Verb-call-based Go HTTP router adapters (gin, echo, chi).

These frameworks all register routes through a call whose *name* is the
HTTP verb — ``r.GET(path, handler)``, ``e.POST(path, handler)``,
``r.Delete(path, handler)`` — differing only in casing and the receiver's
constructing package. A single base class walks
:class:`~nuguard.sbom.core.go_parser.GoFunctionCall` entries (already
extracted by ``go_parser.parse_go``) looking for a call whose function name
matches one of the framework's verb spellings and whose first positional
argument looks like a route path.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter
from ._http_endpoint_metadata import enrich_http_endpoint_detections

# Method spellings as they appear in each framework's call sites, mapped to
# the canonical uppercase HTTP method used in node metadata/canonical names.
_UPPER_VERBS = {
    "GET": "GET",
    "POST": "POST",
    "PUT": "PUT",
    "PATCH": "PATCH",
    "DELETE": "DELETE",
    "OPTIONS": "OPTIONS",
    "HEAD": "HEAD",
}
_TITLE_VERBS = {
    "Get": "GET",
    "Post": "POST",
    "Put": "PUT",
    "Patch": "PATCH",
    "Delete": "DELETE",
    "Options": "OPTIONS",
    "Head": "HEAD",
}


class _GoHTTPVerbRouterAdapter(GoFrameworkAdapter):
    """Shared base for frameworks that name routing calls after the verb."""

    #: mapping of call function_name -> canonical uppercase HTTP method
    verb_names: dict[str, str] = {}

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

        framework = self._fw_node(file_path, matched_import)
        detections: list[ComponentDetection] = [framework]

        seen: set[tuple[str, str]] = set()
        for call in result.function_calls:
            if call.receiver is None:
                continue
            method = self.verb_names.get(call.function_name)
            if method is None:
                continue
            path = self._resolve(call, 0)
            if not path or not path.startswith("/"):
                continue

            key = (method, path)
            if key in seen:
                continue
            seen.add(key)

            canon = f"endpoint:{method}:{path}"
            endpoint = ComponentDetection(
                component_type=ComponentType.API_ENDPOINT,
                canonical_name=canon,
                display_name=f"{method} {path}",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.9,
                metadata={
                    "framework": self.name,
                    "method": method,
                    "endpoint": path,
                    "language": "golang",
                },
                file_path=file_path,
                line=call.line,
                snippet=call.source_snippet or f"{call.receiver}.{call.function_name}({path!r})",
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
            verb_names=self.verb_names,
        )


class GinAdapter(_GoHTTPVerbRouterAdapter):
    """Detect ``gin-gonic/gin`` routers and their route registrations."""

    name = "gin"
    priority = 40
    handles_imports = ["github.com/gin-gonic/gin"]
    verb_names = _UPPER_VERBS


class EchoAdapter(_GoHTTPVerbRouterAdapter):
    """Detect ``labstack/echo`` routers and their route registrations."""

    name = "echo"
    priority = 40
    handles_imports = [
        "github.com/labstack/echo",
        "github.com/labstack/echo/v4",
    ]
    verb_names = _UPPER_VERBS


class ChiAdapter(_GoHTTPVerbRouterAdapter):
    """Detect ``go-chi/chi`` routers and their route registrations."""

    name = "chi"
    priority = 40
    handles_imports = [
        "github.com/go-chi/chi",
        "github.com/go-chi/chi/v5",
    ]
    verb_names = _TITLE_VERBS


__all__ = ["GinAdapter", "EchoAdapter", "ChiAdapter"]
