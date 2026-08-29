"""``99designs/gqlgen`` GraphQL framework adapter.

Emits a FRAMEWORK presence node for gqlgen-generated GraphQL servers.

Resolver-level operation extraction (mapping ``queryResolver``/
``mutationResolver`` methods to individual GraphQL operations, the
GraphQL analogue of per-route API_ENDPOINT nodes for REST frameworks) is
intentionally out of scope here: ``go_parser`` currently extracts imports,
instantiations, calls, and string literals but not function *declarations*,
so resolver method names and signatures aren't available to an adapter yet.
Adding that requires extending ``go_parser.GoParseResult`` with a
function-declaration pass first — see docs/go-support.md phase 2 follow-up.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ..base import ComponentDetection
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/99designs/gqlgen"


class GqlgenAdapter(GoFrameworkAdapter):
    """Detect ``gqlgen``-based GraphQL servers."""

    name = "gqlgen"
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

        framework = self._fw_node(file_path, matched_import, display_name="gqlgen")
        framework.metadata.update({"framework": "gqlgen", "api_style": "graphql"})
        return [framework]


__all__ = ["GqlgenAdapter"]
