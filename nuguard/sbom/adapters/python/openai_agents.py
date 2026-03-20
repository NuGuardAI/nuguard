"""OpenAI Agents SDK adapter.

Detects usage of the ``openai-agents`` (``agents``) Python SDK:
- ``Agent(name=..., instructions=..., tools=[...])`` → AGENT node
- ``Runner.run(agent, ...)`` / ``Runner.run_sync(...)`` → execution evidence
- ``tool`` decorator / ``@function_tool`` → TOOL nodes
- ``model`` argument → MODEL reference
- ``Handoff`` / ``handoff()`` → AGENT-CALLS-AGENT relationship
"""

from __future__ import annotations

import re
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter, RelationshipHint
from xelo.adapters.models_kb import get_model_details, infer_provider
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

_TEMPLATE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class OpenAIAgentsAdapter(FrameworkAdapter):
    """Adapter for the OpenAI Agents SDK (openai-agents / agents library)."""

    name = "openai_agents"
    priority = 20
    handles_imports = ["agents", "openai_agents", "openai.agents", "swarm"]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        detected: list[ComponentDetection] = [self._framework_node(file_path)]
        agent_canonicals: list[str] = []

        # Collect guardrail agent variable names (populated by AST parser from @input_guardrail bodies)
        guardrail_vars: set[str] = getattr(parse_result, "guardrail_agent_vars", set())

        # 1. Agent class instantiations
        for inst in parse_result.instantiations:
            # InputGuardrail / OutputGuardrail → GUARDRAIL node
            if inst.class_name in {"InputGuardrail", "OutputGuardrail"}:
                guardrail_name = _clean(
                    inst.assigned_to or (inst.args or {}).get("name") or f"guardrail_{inst.line}"
                )
                guardrail_type = "input" if "Input" in inst.class_name else "output"
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.GUARDRAIL,
                        canonical_name=canonicalize_text(
                            f"openai_agents:guardrail:{guardrail_name}"
                        ),
                        display_name=guardrail_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.92,
                        metadata={"guardrail_type": guardrail_type, "framework": "openai_agents"},
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(...)",
                        evidence_kind="ast_instantiation",
                    )
                )
                continue
            if inst.class_name not in {"Agent", "AssistantAgent", "SwarmAgent"}:
                continue
            args = inst.args or {}
            agent_name = _clean(
                args.get("name")
                or (inst.positional_args[0] if inst.positional_args else None)
                or inst.assigned_to
                or f"agent_{inst.line}"
            )
            instructions = _clean(args.get("instructions") or args.get("system_prompt", ""))
            model_name = _clean(args.get("model", ""))
            # swarm uses 'functions' instead of 'tools'
            tools_raw = args.get("tools") or args.get("functions") or []

            # Classify as GUARDRAIL if this agent variable is invoked inside an @input_guardrail fn
            is_guardrail = bool(inst.assigned_to and inst.assigned_to in guardrail_vars)

            canon = canonicalize_text(f"openai_agents:{agent_name}")
            rels: list[RelationshipHint] = []

            # Model reference — emit a MODEL node and a relationship hint
            if model_name:
                provider = infer_provider(model_name)
                model_canon = canonicalize_text(model_name.lower())
                model_details = get_model_details(model_name, provider)
                model_meta: dict[str, Any] = {
                    "framework": "openai_agents",
                    "provider": provider,
                    **{k: v for k, v in model_details.items() if v is not None},
                }
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=model_canon,
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata=model_meta,
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"Agent(model={model_name!r})",
                        evidence_kind="ast_instantiation",
                    )
                )
                rels.append(
                    RelationshipHint(
                        source_canonical=canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=model_canon,
                        target_type=ComponentType.MODEL,
                        relationship_type="USES",
                    )
                )

            # Tool references
            if isinstance(tools_raw, list):
                for tool_ref in tools_raw:
                    if isinstance(tool_ref, str) and not tool_ref.startswith("$"):
                        tool_canon = canonicalize_text(f"openai_agents:tool:{tool_ref}")
                        rels.append(
                            RelationshipHint(
                                source_canonical=canon,
                                source_type=ComponentType.AGENT,
                                target_canonical=tool_canon,
                                target_type=ComponentType.TOOL,
                                relationship_type="CALLS",
                            )
                        )

            # If instructions is a function reference, find string literals from that function
            instructions_raw = args.get("instructions") or args.get("system_prompt", "")
            if (
                not instructions
                and isinstance(instructions_raw, str)
                and instructions_raw.startswith("$")
            ):
                func_name = instructions_raw[1:]  # strip "$"
                func_literals = [
                    lit.value
                    for lit in parse_result.string_literals
                    if lit.context == func_name and len(lit.value) >= 40 and not lit.is_docstring
                ]
                if func_literals:
                    instructions = max(func_literals, key=len)

            template_vars = _TEMPLATE_VAR_RE.findall(instructions) if instructions else []
            meta: dict[str, Any] = {
                "framework": "openai_agents",
                "has_instructions": bool(instructions),
            }
            if instructions:
                meta["instructions_preview"] = instructions[:500]
                meta["is_template"] = bool(template_vars)
                meta["template_variables"] = template_vars
            if model_name:
                meta["model"] = model_name
                details = get_model_details(model_name, infer_provider(model_name))
                meta.update({k: v for k, v in details.items() if v is not None})

            comp_type = ComponentType.GUARDRAIL if is_guardrail else ComponentType.AGENT
            detected.append(
                ComponentDetection(
                    component_type=comp_type,
                    canonical_name=canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.92,
                    metadata=meta,
                    file_path=file_path,
                    line=inst.line,
                    snippet=f"Agent(name={agent_name!r})",
                    evidence_kind="ast_instantiation",
                    relationships=rels,
                )
            )
            if not is_guardrail:
                agent_canonicals.append(canon)

            # Instructions as PROMPT node
            if instructions and len(instructions) >= 40:
                prompt_display = f"{agent_name} Instructions"
                prompt_canon = canonicalize_text(prompt_display.lower())
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.PROMPT,
                        canonical_name=prompt_canon,
                        display_name=prompt_display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.92,
                        metadata={
                            "role": "system",
                            "content": instructions,
                            "char_count": len(instructions),
                            "is_template": bool(template_vars),
                            "template_variables": template_vars,
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=instructions[:80],
                        evidence_kind="ast_instantiation",
                    )
                )

            # Inline tool list strings
            if isinstance(tools_raw, list):
                for tool_ref in tools_raw:
                    if isinstance(tool_ref, str) and not tool_ref.startswith("$"):
                        tool_canon = canonicalize_text(f"openai_agents:tool:{tool_ref}")
                        detected.append(
                            ComponentDetection(
                                component_type=ComponentType.TOOL,
                                canonical_name=tool_canon,
                                display_name=tool_ref,
                                adapter_name=self.name,
                                priority=self.priority,
                                confidence=0.75,
                                metadata={"framework": "openai_agents"},
                                file_path=file_path,
                                line=inst.line,
                                snippet=f"tools=[..., {tool_ref!r}, ...]",
                                evidence_kind="ast_instantiation",
                            )
                        )

        # 2. @function_tool / @tool decorated functions → TOOL
        # Detected as function_calls if used as decorator - look for calls named "function_tool" or "tool"
        for call in parse_result.function_calls:
            if call.function_name in {"function_tool", "tool"}:
                tool_name = _clean(
                    call.args.get("name_override")  # @function_tool(name_override="foo")
                    or call.args.get("name")
                    or (call.positional_args[0] if call.positional_args else None)
                    or call.assigned_to
                    or f"tool_{call.line}"
                )
                tool_canon = canonicalize_text(f"openai_agents:tool:{tool_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=tool_canon,
                        display_name=tool_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={"framework": "openai_agents", "decorator": call.function_name},
                        file_path=file_path,
                        line=call.line,
                        snippet=f"@{call.function_name}",
                        evidence_kind="ast_call",
                    )
                )

        # 3. Handoff → AGENT-CALLS-AGENT relationship hint
        for inst in parse_result.instantiations:
            if inst.class_name == "Handoff":
                target_agent = _clean(
                    inst.args.get("agent")
                    or (inst.positional_args[0] if inst.positional_args else None)
                )
                if target_agent and agent_canonicals:
                    target_canon = canonicalize_text(f"openai_agents:{target_agent}")
                    if detected:
                        detected[-1].relationships.append(
                            RelationshipHint(
                                source_canonical=agent_canonicals[-1],
                                source_type=ComponentType.AGENT,
                                target_canonical=target_canon,
                                target_type=ComponentType.AGENT,
                                relationship_type="CALLS",
                            )
                        )

        return detected


def _clean(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip("'\"` ")
    if s.startswith("$") or s in {"<complex>", "<lambda>", "<dict>", "<list>"}:
        return ""
    return s
