"""``cloudwego/eino`` agent-orchestration framework adapter.

eino is not present in any fixture this codebase has ground-truth-validated
against yet (unlike gin/net-http/mongo/jwt, which were cross-checked against
the real mosaic-care/healthcare-service repo in docs/go-support.md phase 5).
The call shapes below were verified against the upstream source
(``compose.NewChain``/``compose.NewGraph`` in ``compose/chain.go``,
``utils.NewTool`` in ``components/tool/utils/invokable_func_test.go``) via
the public GitHub repo rather than a local fixture.

- ``compose.NewChain[I, O](...)`` / ``compose.NewGraph[I, O](...)`` — the
  generic type arguments (``[I, O]``) are already stripped by
  ``go_parser._split_callee`` before receiver/function-name splitting, so
  these behave like ordinary receiver-qualified calls here → AGENT node
  (eino's orchestration graph/chain, the Go analogue of LangGraph's
  ``StateGraph``).
- ``tool.NewTool(toolInfo, invokeFunc)`` / ``utils.NewTool(toolInfo, ...)``
  where ``toolInfo`` resolves — inline or via ``go_parser``'s single-file
  symbol table — to a ``&schema.ToolInfo{Name: "...", Desc: "..."}`` struct
  literal → TOOL node using its ``Name``/``Desc`` fields. A ``toolInfo``
  declared in a different file won't resolve (no cross-file symbol
  tracking) and is silently skipped, same as unresolved values elsewhere
  in the Go adapters.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/cloudwego/eino"
_GRAPH_CALLS = {"NewChain", "NewGraph"}
_TOOL_CALLS = {"NewTool", "NewStreamTool"}


class EinoAdapter(GoFrameworkAdapter):
    """Detect ``eino`` chain/graph construction and tool definitions."""

    name = "eino"
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

        framework = self._fw_node(file_path, matched_import, display_name="Eino")
        framework.metadata.update({"framework": "eino"})
        detections: list[ComponentDetection] = [framework]

        for call in result.function_calls:
            if call.receiver == "compose" and call.function_name in _GRAPH_CALLS:
                assigned = call.assigned_to or call.function_name
                canon = canonicalize_text(f"eino:agent:{file_path}:{assigned}")
                agent = ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=canon,
                    display_name=assigned,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={"framework": "eino", "language": "golang"},
                    file_path=file_path,
                    line=call.line,
                    snippet=call.source_snippet or f"compose.{call.function_name}(...)",
                    evidence_kind="ast_call",
                )
                agent.relationships.append(
                    RelationshipHint(
                        source_canonical=framework.canonical_name,
                        source_type=ComponentType.FRAMEWORK,
                        target_canonical=canon,
                        target_type=ComponentType.AGENT,
                        relationship_type="USES",
                    )
                )
                detections.append(agent)

            elif call.receiver in ("tool", "utils") and call.function_name in _TOOL_CALLS:
                info = call.positional_args[0] if call.positional_args else None
                if not isinstance(info, dict):
                    continue
                name = self._clean(info.get("Name"))
                if not name:
                    continue
                desc = self._clean(info.get("Desc"))

                canon = canonicalize_text(f"eino:tool:{name}")
                tool = ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canon,
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "eino",
                        "language": "golang",
                        **({"description": desc} if desc else {}),
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=call.source_snippet or f"{call.receiver}.{call.function_name}(...)",
                    evidence_kind="ast_call",
                )
                tool.relationships.append(
                    RelationshipHint(
                        source_canonical=framework.canonical_name,
                        source_type=ComponentType.FRAMEWORK,
                        target_canonical=canon,
                        target_type=ComponentType.TOOL,
                        relationship_type="USES",
                    )
                )
                detections.append(tool)

        return detections


__all__ = ["EinoAdapter"]
