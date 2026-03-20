"""CrewAI framework adapter.

Detects usage of the ``crewai`` library:
- ``Agent(role=..., goal=..., backstory=...)`` → AGENT nodes
- ``Task(description=..., agent=...)`` → TOOL nodes (task-as-tool pattern)
- ``Crew(agents=[...], tasks=[...])`` → orchestrator AGENT node
- ``llm`` / ``llm_config`` arguments → MODEL references
- ``tools=[...]`` argument → TOOL references
- ``backstory=`` / ``goal=`` long text → PROMPT nodes
"""

from __future__ import annotations

import re
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter, RelationshipHint
from xelo.adapters.models_kb import get_model_details, infer_provider
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

_TEMPLATE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_MIN_PROMPT_LENGTH = 40


class CrewAIAdapter(FrameworkAdapter):
    """Adapter for the CrewAI multi-agent framework."""

    name = "crewai"
    priority = 50
    handles_imports = [
        "crewai",
        "crewai.agent",
        "crewai.task",
        "crewai.crew",
        "crewai.tools",
        "crewai_tools",
    ]

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
        task_canonicals: list[str] = []

        for inst in parse_result.instantiations:
            # ---- Agent ----
            if inst.class_name == "Agent":
                args = inst.args or {}
                role = _clean(
                    args.get("role") or (inst.positional_args[0] if inst.positional_args else None)
                )
                agent_name = (
                    _clean(args.get("name") or inst.assigned_to) or role or f"agent_{inst.line}"
                )
                goal = _clean(args.get("goal", ""))
                backstory = _clean(args.get("backstory", ""))
                llm_ref = _clean(args.get("llm") or args.get("llm_config"))
                tools_raw = args.get("tools", [])

                canon = canonicalize_text(f"crewai:{agent_name}")
                rels: list[RelationshipHint] = []

                # Model reference from llm argument
                if llm_ref:
                    provider = infer_provider(llm_ref)
                    model_canon = canonicalize_text(llm_ref.lower())
                    rels.append(
                        RelationshipHint(
                            source_canonical=canon,
                            source_type=ComponentType.AGENT,
                            target_canonical=model_canon,
                            target_type=ComponentType.MODEL,
                            relationship_type="USES",
                        )
                    )
                    # Emit model node
                    details = get_model_details(llm_ref, provider)
                    detected.append(
                        ComponentDetection(
                            component_type=ComponentType.MODEL,
                            canonical_name=model_canon,
                            display_name=llm_ref,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.85,
                            metadata={
                                "framework": "crewai",
                                "provider": provider,
                                **{k: v for k, v in details.items() if v is not None},
                            },
                            file_path=file_path,
                            line=inst.line,
                            snippet=f"Agent(llm={llm_ref!r})",
                            evidence_kind="ast_instantiation",
                        )
                    )

                # Tool references
                if isinstance(tools_raw, list):
                    for tool_ref in tools_raw:
                        if isinstance(tool_ref, str) and not tool_ref.startswith("$"):
                            tool_canon = canonicalize_text(f"crewai:tool:{tool_ref}")
                            rels.append(
                                RelationshipHint(
                                    source_canonical=canon,
                                    source_type=ComponentType.AGENT,
                                    target_canonical=tool_canon,
                                    target_type=ComponentType.TOOL,
                                    relationship_type="CALLS",
                                )
                            )

                meta: dict[str, Any] = {
                    "framework": "crewai",
                    "role": role,
                    "has_goal": bool(goal),
                    "has_backstory": bool(backstory),
                    "has_instructions": bool(backstory or goal),
                }
                if goal:
                    meta["goal_preview"] = goal[:200]
                if backstory:
                    meta["backstory_preview"] = backstory[:200]
                    template_vars = _TEMPLATE_VAR_RE.findall(backstory)
                    meta["is_template"] = bool(template_vars)
                    meta["template_variables"] = template_vars
                elif goal:
                    template_vars = _TEMPLATE_VAR_RE.findall(goal)
                    meta["is_template"] = bool(template_vars)
                    meta["template_variables"] = template_vars

                # Backstory is CrewAI's system prompt — emit as a PROMPT node
                system_text = backstory or goal
                prompt_display = f"{agent_name} Backstory" if backstory else f"{agent_name} Goal"
                prompt_canon = canonicalize_text(f"crewai:{prompt_display.lower()}")
                if system_text and len(system_text) >= _MIN_PROMPT_LENGTH:
                    rels.append(
                        RelationshipHint(
                            source_canonical=canon,
                            source_type=ComponentType.AGENT,
                            target_canonical=prompt_canon,
                            target_type=ComponentType.PROMPT,
                            relationship_type="USES",
                        )
                    )

                # Require behavioral evidence; bare role-only agents are low-signal
                has_behavioral_evidence = bool(llm_ref or goal or backstory or tools_raw)
                agent_confidence = 0.90 if has_behavioral_evidence else 0.55
                # Skip agents where role string is too short to be meaningful
                if role and len(role) < 3:
                    continue

                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.AGENT,
                        canonical_name=canon,
                        display_name=agent_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=agent_confidence,
                        metadata=meta,
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"Agent(role={role!r})",
                        evidence_kind="ast_instantiation",
                        relationships=rels,
                    )
                )
                agent_canonicals.append(canon)

                # Emit the backstory / goal as a first-class PROMPT node
                if system_text and len(system_text) >= _MIN_PROMPT_LENGTH:
                    prompt_tvars = _TEMPLATE_VAR_RE.findall(system_text)
                    detected.append(
                        ComponentDetection(
                            component_type=ComponentType.PROMPT,
                            canonical_name=prompt_canon,
                            display_name=prompt_display,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.88,
                            metadata={
                                "role": "system",
                                "content": system_text,
                                "char_count": len(system_text),
                                "is_template": bool(prompt_tvars),
                                "template_variables": prompt_tvars,
                            },
                            file_path=file_path,
                            line=inst.line,
                            snippet=system_text[:80],
                            evidence_kind="ast_instantiation",
                        )
                    )

            # ---- Task ----
            elif inst.class_name == "Task":
                args = inst.args or {}
                description = _clean(
                    args.get("description")
                    or (inst.positional_args[0] if inst.positional_args else None)
                )
                task_name = _clean(inst.assigned_to) or f"task_{inst.line}"
                canon = canonicalize_text(f"crewai:task:{task_name}")
                task_meta: dict[str, Any] = {
                    "framework": "crewai",
                    "task_type": "Task",
                }
                if description:
                    task_meta["description_preview"] = description[:200]

                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=canon,
                        display_name=task_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.80,
                        metadata=task_meta,
                        file_path=file_path,
                        line=inst.line,
                        snippet="Task(description=...)",
                        evidence_kind="ast_instantiation",
                    )
                )
                task_canonicals.append(canon)

            # ---- Crew ----
            # Crew is the orchestration container, not an individual agent.
            # Emitting it as AGENT produces many FPs; skip it entirely since
            # the FRAMEWORK node (emitted above) already signals crewai usage.
            # Relationships to member agents are captured via the agents=[...]
            # arg already processed when each Agent() was visited.
            elif inst.class_name == "Crew":
                pass  # intentionally not emitting a separate node for Crew

            # ---- @tool decorated functions (crewai.tools.tool) ----
            elif inst.class_name in {"BaseTool", "Tool"}:
                tool_name = _clean(
                    inst.args.get("name")
                    or (inst.positional_args[0] if inst.positional_args else None)
                    or inst.assigned_to
                    or f"tool_{inst.line}"
                )
                canon = canonicalize_text(f"crewai:tool:{tool_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=canon,
                        display_name=tool_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={"framework": "crewai"},
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(name={tool_name!r})",
                        evidence_kind="ast_instantiation",
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
