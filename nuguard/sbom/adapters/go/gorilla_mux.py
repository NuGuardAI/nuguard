"""``gorilla/mux`` adapter.

Unlike gin/echo/chi, gorilla/mux registers a route with a verb-less
``HandleFunc(path, handler)`` call and (optionally) restricts it to specific
methods via a chained ``.Methods("GET", "POST")`` call on the returned
``*mux.Route``. ``go_parser`` does not model call chaining, so the chained
``.Methods(...)`` is recovered with a small line-scoped regex over the
handler call's own source rather than a second structured call lookup.
"""

from __future__ import annotations

import re
from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/gorilla/mux"
_HANDLER_CALLS = {"HandleFunc", "Handle"}
_METHODS_CHAIN_RE = re.compile(r"\.Methods\(([^)]*)\)")
_QUOTED_RE = re.compile(r'"([A-Za-z]+)"')


def _chained_methods(content: str, call_line: int, call_line_end: int) -> list[str]:
    """Best-effort extraction of a ``.Methods("GET", "POST")`` chain.

    Scans from the call's own start line through a few lines past its end
    (route registrations are occasionally split across lines) for a
    ``.Methods(...)`` call and returns the quoted method names within it.
    """
    lines = content.splitlines()
    window = "\n".join(lines[max(call_line - 1, 0) : call_line_end + 3])
    match = _METHODS_CHAIN_RE.search(window)
    if match is None:
        return []
    return [m.upper() for m in _QUOTED_RE.findall(match.group(1))]


class GorillaMuxAdapter(GoFrameworkAdapter):
    """Detect ``gorilla/mux`` routers and their route registrations."""

    name = "gorilla_mux"
    priority = 40
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

        framework = self._fw_node(file_path, matched_import, display_name="gorilla/mux")
        detections: list[ComponentDetection] = [framework]

        seen: set[tuple[str, str]] = set()
        for call in result.function_calls:
            if call.receiver is None or call.function_name not in _HANDLER_CALLS:
                continue
            path = self._resolve(call, 0)
            if not path or not path.startswith("/"):
                continue

            methods = _chained_methods(content, call.line, call.line_end) or ["ANY"]
            for method in methods:
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
                    confidence=0.85,
                    metadata={
                        "framework": self.name,
                        "method": method,
                        "endpoint": path,
                        "language": "golang",
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=call.source_snippet or f"{call.receiver}.HandleFunc({path!r}, ...)",
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

        return detections


__all__ = ["GorillaMuxAdapter"]
