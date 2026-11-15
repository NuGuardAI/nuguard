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

import re
from typing import Any

from ...core.go_parser import GoFunctionCall, GoParseResult, parse_go
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter
from ._http_endpoint_metadata import (
    _AUTH_DEPENDENCY_NAME_RE,
    _AUTH_WRAPPER_NAME_RE,
    _call_arguments,
    enrich_http_endpoint_detections,
)

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
_CAMEL_TOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+")
_AUTH_NAME_TOKENS = {"auth", "authn", "jwt", "jwtauth", "bearer", "authenticator"}
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
        route_calls: list[tuple[ComponentDetection, GoFunctionCall]] = []
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
            route_calls.append((endpoint, call))

        enriched = enrich_http_endpoint_detections(
            content,
            result,
            detections,
            framework=self.name,
            verb_names=self.verb_names,
        )
        for endpoint, call in route_calls:
            if endpoint.metadata.get("auth_required"):
                continue
            inference = _middleware_auth_inference(result, call, local_only=self.name != "chi")
            if inference:
                endpoint.metadata["auth_required"] = True
                endpoint.metadata["auth_inference"] = inference
        return enriched


def _is_auth_middleware(expression: str, local_only: bool = False) -> bool:
    """Return whether a middleware expression is named like authentication.

    ``local_only`` rejects package-qualified names, leaving gin/echo library
    middleware to the recognized-library evidence path.
    """
    callee = expression.strip().lstrip("&").split("(", 1)[0].strip()
    if local_only and "." in callee:
        return False
    for segment in callee.split("."):
        if _AUTH_WRAPPER_NAME_RE.fullmatch(segment) or _AUTH_DEPENDENCY_NAME_RE.fullmatch(segment):
            return True
        if any(token.lower() in _AUTH_NAME_TOKENS for token in _CAMEL_TOKEN_RE.findall(segment)):
            return True
    return False


def _middleware_auth_inference(
    result: GoParseResult, route: GoFunctionCall, *, local_only: bool
) -> str:
    """Name the router-middleware auth signal covering ``route``, if any.

    Covers chi ``r.With(auth).Get(...)`` chains, ``.Use(auth)`` inside a chi
    ``Group``/``Route`` closure (and nested closures), and ``.Use(auth)`` /
    ``Group(path, auth)`` on a gin/echo group variable the route hangs off.
    """
    receiver = route.receiver or ""
    with_at = receiver.find(".With(")
    if with_at >= 0 and any(
        _is_auth_middleware(arg) for arg in _call_arguments(receiver[with_at + 1 :])
    ):
        return "route_middleware"

    scopes = [
        (call.line, call.line + (call.source_snippet or "").count("\n"))
        for call in result.function_calls
        if call.function_name in {"Group", "Route"} and "func(" in (call.source_snippet or "")
    ]

    def innermost(line: int) -> tuple[int, int] | None:
        containing = [scope for scope in scopes if scope[0] < line <= scope[1]]
        return max(containing, default=None)

    receivers = {receiver}
    changed = True
    while changed:
        changed = False
        for call in result.function_calls:
            if (
                call.function_name == "Group"
                and call.assigned_to in receivers
                and call.receiver
                and call.receiver not in receivers
                and call.line <= route.line
            ):
                receivers.add(call.receiver)
                changed = True

    for call in result.function_calls:
        if call.line > route.line or call.function_name not in {"Use", "Group"}:
            continue
        arguments = _call_arguments(call.source_snippet or "")
        if call.function_name == "Group":
            if call.assigned_to not in receivers or not any(
                _is_auth_middleware(arg, local_only) for arg in arguments[1:]
            ):
                continue
            return "group_middleware"
        if not any(_is_auth_middleware(arg, local_only) for arg in arguments):
            continue
        scope = innermost(call.line)
        if scope is None:
            if call.receiver in receivers:
                return "group_middleware"
        elif scope[0] < route.line <= scope[1]:
            return "group_middleware"
    return ""


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
