"""OpenAI Agents SDK TypeScript Adapter for Xelo SBOM.

Parsing is performed by ``xelo.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).

Supports:
- Agent class definitions with name/model/instructions/tools
- tool() / createTool() / defineTool() registrations
- Agent → Model and Agent → Tool relationship hints
"""

from __future__ import annotations

from typing import Any

from xelo.adapters.base import ComponentDetection, RelationshipHint
from xelo.adapters.typescript._ts_regex import TSFrameworkAdapter
from xelo.core.ts_parser import TSParseResult, parse_typescript
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType


_OPENAI_AGENTS_PACKAGES = [
    "openai-agents",
    "@openai/agents",
    "agents-js",
]

_AGENT_CLASSES = {"Agent", "OpenAIAgent", "AssistantAgent"}
_TOOL_CALL_NAMES = {"tool", "createTool", "defineTool", "function_tool"}


class OpenAIAgentsTSAdapter(TSFrameworkAdapter):
    """Detect OpenAI Agents SDK usage in TypeScript/JavaScript files."""

    name = "openai_agents_ts"
    priority = 20
    handles_imports = _OPENAI_AGENTS_PACKAGES

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
        if not self._detect(result):
            return []

        source = result.source or content
        detected: list[ComponentDetection] = [self._fw_node(file_path)]
        tool_canonicals: dict[str, str] = {}

        # --- Extract tools ---
        for call in result.function_calls:
            fn = call.function_name.split(".")[-1]
            if fn not in _TOOL_CALL_NAMES:
                continue
            tool_name = (
                self._resolve(call, "name", "toolName")
                or (self._clean(call.positional_args[0]) if call.positional_args else "")
                or self._assignment_name(source, call.line_start)
                or f"tool_{call.line_start}"
            )
            canon = canonicalize_text(tool_name.lower())
            tool_canonicals[tool_name] = canon
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canon,
                    display_name=tool_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "creation_method": call.function_name,
                        "framework": "openai-agents-sdk",
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=call.line_start,
                    snippet=call.source_snippet or f"{call.function_name}({tool_name!r})",
                    evidence_kind="ast_call",
                )
            )

        # --- Extract agents ---
        for inst in result.instantiations:
            if inst.class_name not in _AGENT_CLASSES:
                continue
            agent_name = (
                self._resolve(inst, "name")
                or self._assignment_name(source, inst.line_start)
                or f"agent_{inst.line_start}"
            )
            if "guardrail" in agent_name.lower():
                continue

            agent_canon = canonicalize_text(agent_name.lower())
            rels: list[RelationshipHint] = []

            # Model link — resolved_arguments handles const MODEL = "gpt-4o" patterns
            model_name = self._resolve(inst, "model")
            if model_name:
                model_canon = canonicalize_text(model_name.lower())
                rels.append(
                    RelationshipHint(
                        source_canonical=agent_canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=model_canon,
                        target_type=ComponentType.MODEL,
                        relationship_type="USES",
                    )
                )
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=model_canon,
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": "openai-agents-sdk",
                            "provider": "openai",
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=inst.line_start,
                        snippet=f"model={model_name!r}",
                        evidence_kind="ast_instantiation",
                    )
                )

            # Instructions → PROMPT
            instructions = self._resolve(inst, "instructions", "system_prompt")
            template_vars = self._template_vars(instructions) if instructions else []
            if len(instructions) > 10:
                prompt_name = f"{agent_name} Instructions"
                prompt_canon = canonicalize_text(prompt_name.lower())
                rels.append(
                    RelationshipHint(
                        source_canonical=agent_canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=prompt_canon,
                        target_type=ComponentType.PROMPT,
                        relationship_type="USES",
                    )
                )
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.PROMPT,
                        canonical_name=prompt_canon,
                        display_name=prompt_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.92,
                        metadata={
                            "framework": "openai-agents-sdk",
                            "prompt_type": "instructions",
                            "role": "system",
                            "content": instructions,
                            "char_count": len(instructions),
                            "is_template": bool(template_vars),
                            "template_variables": template_vars,
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=inst.line_start,
                        snippet=instructions[:80],
                        evidence_kind="ast_instantiation",
                    )
                )

            # Tools list — tools: [searchTool, calcTool]
            tools_val = (inst.resolved_arguments or inst.arguments).get("tools")
            if tools_val:
                refs = (
                    tools_val
                    if isinstance(tools_val, list)
                    else [
                        t.strip().strip("'\"")
                        for t in str(tools_val).strip("[]").split(",")
                        if t.strip()
                    ]
                )
                for ref in refs:
                    rels.append(
                        RelationshipHint(
                            source_canonical=agent_canon,
                            source_type=ComponentType.AGENT,
                            target_canonical=canonicalize_text(str(ref).lower()),
                            target_type=ComponentType.TOOL,
                            relationship_type="CALLS",
                        )
                    )

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=agent_canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "class": inst.class_name,
                        "framework": "openai-agents-sdk",
                        "has_instructions": len(instructions) > 10,
                        **(
                            {
                                "instructions_preview": instructions[:500],
                                "is_template": bool(template_vars),
                                "template_variables": template_vars,
                            }
                            if len(instructions) > 10
                            else {}
                        ),
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or "",
                    evidence_kind="ast_instantiation",
                    relationships=rels,
                )
            )

        return detected


OPENAI_AGENTS_TS_PACKAGES = _OPENAI_AGENTS_PACKAGES
AGENT_CLASS_NAMES = list(_AGENT_CLASSES)
