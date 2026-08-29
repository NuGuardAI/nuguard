"""``firebase/genkit/go`` agent-orchestration framework adapter.

Not present in any locally ground-truth-validated fixture (see the same
caveat in ``eino.py``); call shapes verified against upstream docs/examples
(firebase.google.com/docs/genkit-go, pkg.go.dev/github.com/firebase/genkit/go).

- ``genkit.DefineFlow(g, "flowName", func(...) {...})`` → AGENT node (a
  flow is genkit's orchestration unit, the Go analogue of a LangGraph graph
  or an eino chain).
- ``genkit.DefineTool(g, "toolName", "description", func(...) {...})`` →
  TOOL node.

``genkit.DefinePrompt(g, "name", ai.WithPrompt("..."), ...)`` is out of
scope: the prompt text is the argument to a *nested* call
(``ai.WithPrompt(...)``), not a plain string literal or struct field, and
``go_parser``'s ``_extract_value`` has no case for a nested call expression
— it falls through to the generic ``f"${text}"`` catch-all, which
``_clean()`` (correctly) treats as unresolved. Extracting it would need a
dedicated nested-call unwrap, which isn't worth adding speculatively for a
framework with no local ground truth yet.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/firebase/genkit/go"
_FLOW_CALL = "DefineFlow"
_TOOL_CALL = "DefineTool"


class GenkitGoAdapter(GoFrameworkAdapter):
    """Detect ``genkit`` flow and tool definitions."""

    name = "genkit_go"
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

        framework = self._fw_node(file_path, matched_import, display_name="Genkit")
        framework.metadata.update({"framework": "genkit_go"})
        detections: list[ComponentDetection] = [framework]

        for call in result.function_calls:
            if call.receiver != "genkit":
                continue

            if call.function_name == _FLOW_CALL:
                name = self._resolve(call, 1)
                if not name:
                    continue
                canon = canonicalize_text(f"genkit:agent:{name}")
                agent = ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=canon,
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.9,
                    metadata={"framework": "genkit_go", "language": "golang"},
                    file_path=file_path,
                    line=call.line,
                    snippet=call.source_snippet or f'genkit.DefineFlow(g, "{name}", ...)',
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

            elif call.function_name == _TOOL_CALL:
                name = self._resolve(call, 1)
                if not name:
                    continue
                desc = self._resolve(call, 2)
                canon = canonicalize_text(f"genkit:tool:{name}")
                tool = ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canon,
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.9,
                    metadata={
                        "framework": "genkit_go",
                        "language": "golang",
                        **({"description": desc} if desc else {}),
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=call.source_snippet or f'genkit.DefineTool(g, "{name}", ...)',
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


__all__ = ["GenkitGoAdapter"]
