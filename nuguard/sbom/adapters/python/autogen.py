"""AutoGen framework adapter.

Detects usage of Microsoft AutoGen (``autogen``, ``pyautogen``, ``autogen_agentchat``):
- ``ConversableAgent``, ``AssistantAgent``, ``UserProxyAgent`` → AGENT nodes
- ``GroupChat`` / ``GroupChatManager`` → AGENT (orchestrator) node
- ``llm_config`` dict with ``model`` → MODEL reference
- ``register_function`` / ``register_for_llm`` → TOOL nodes
- System messages / ``system_message`` argument → PROMPT nodes
"""

from __future__ import annotations

import re
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter, RelationshipHint
from xelo.adapters.models_kb import get_model_details, infer_provider
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

_TEMPLATE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_MIN_PROMPT_LENGTH = 30

_AGENT_CLASSES = {
    "ConversableAgent",
    "AssistantAgent",
    "UserProxyAgent",
    "GPTAssistantAgent",
    "RetrieveAssistantAgent",
    "RetrieveUserProxyAgent",
    "CompressibleAgent",
    "TransformMessages",
}

_ORCHESTRATOR_CLASSES = {
    "GroupChat",
    "GroupChatManager",
    "RoundRobinGroupChat",
    "SelectorGroupChat",
    "Swarm",
}


class AutoGenAdapter(FrameworkAdapter):
    """Adapter for Microsoft AutoGen multi-agent framework."""

    name = "autogen"
    priority = 30
    handles_imports = ["autogen", "pyautogen", "autogen_agentchat", "autogen_ext", "autogen_core"]

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

        for inst in parse_result.instantiations:
            # --- Agent classes ---
            if inst.class_name in _AGENT_CLASSES:
                args = inst.args or {}
                agent_name = _clean(
                    args.get("name")
                    or (inst.positional_args[0] if inst.positional_args else None)
                    or inst.assigned_to
                    or f"agent_{inst.line}"
                )
                system_msg_raw = args.get("system_message") or args.get("instructions", "")
                system_msg = _clean(system_msg_raw)
                # Function-reference instructions: look up string literals from that function
                if (
                    not system_msg
                    and isinstance(system_msg_raw, str)
                    and system_msg_raw.startswith("$")
                ):
                    func_name = system_msg_raw[1:]
                    func_literals = [
                        lit.value
                        for lit in parse_result.string_literals
                        if lit.context == func_name
                        and len(lit.value) >= 40
                        and not lit.is_docstring
                    ]
                    if func_literals:
                        system_msg = max(func_literals, key=len)
                llm_config = args.get("llm_config")
                rels: list[RelationshipHint] = []
                canon = canonicalize_text(f"autogen:{agent_name}")

                # Extract model from llm_config dict
                model_name = ""
                if isinstance(llm_config, dict):
                    config_list = llm_config.get("config_list")
                    if isinstance(config_list, list) and config_list:
                        model_name = _clean(
                            config_list[0].get("model") if isinstance(config_list[0], dict) else ""
                        )
                    if not model_name:
                        model_name = _clean(llm_config.get("model", ""))
                elif isinstance(llm_config, str) and not llm_config.startswith("$"):
                    model_name = llm_config

                if model_name:
                    provider = infer_provider(model_name)
                    model_canon = canonicalize_text(model_name.lower())
                    rels.append(
                        RelationshipHint(
                            source_canonical=canon,
                            source_type=ComponentType.AGENT,
                            target_canonical=model_canon,
                            target_type=ComponentType.MODEL,
                            relationship_type="USES",
                        )
                    )

                template_vars = _TEMPLATE_VAR_RE.findall(system_msg) if system_msg else []
                meta: dict[str, Any] = {
                    "class_name": inst.class_name,
                    "framework": "autogen",
                    "has_instructions": bool(system_msg),
                }
                if model_name:
                    meta["model"] = model_name
                    details = get_model_details(model_name, infer_provider(model_name))
                    meta.update({k: v for k, v in details.items() if v is not None})
                if system_msg:
                    meta["instructions_preview"] = system_msg[:500]
                    meta["is_template"] = bool(template_vars)
                    meta["template_variables"] = template_vars

                prompt_display = f"{agent_name} System Message"
                prompt_canon = canonicalize_text(prompt_display.lower())
                if system_msg and len(system_msg) >= _MIN_PROMPT_LENGTH:
                    rels.append(
                        RelationshipHint(
                            source_canonical=canon,
                            source_type=ComponentType.AGENT,
                            target_canonical=prompt_canon,
                            target_type=ComponentType.PROMPT,
                            relationship_type="USES",
                        )
                    )

                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.AGENT,
                        canonical_name=canon,
                        display_name=agent_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata=meta,
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(name={agent_name!r})",
                        evidence_kind="ast_instantiation",
                        relationships=rels,
                    )
                )
                agent_canonicals.append(canon)

                # System message → PROMPT
                if system_msg and len(system_msg) >= _MIN_PROMPT_LENGTH:
                    detected.append(
                        ComponentDetection(
                            component_type=ComponentType.PROMPT,
                            canonical_name=prompt_canon,
                            display_name=prompt_display,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.90,
                            metadata={
                                "role": "system",
                                "content": system_msg,
                                "char_count": len(system_msg),
                                "is_template": bool(template_vars),
                                "template_variables": template_vars,
                            },
                            file_path=file_path,
                            line=inst.line,
                            snippet=system_msg[:80],
                            evidence_kind="ast_instantiation",
                        )
                    )

                # Model node if named
                if model_name:
                    provider = infer_provider(model_name)
                    details = get_model_details(model_name, provider)
                    model_canon = canonicalize_text(model_name.lower())
                    detected.append(
                        ComponentDetection(
                            component_type=ComponentType.MODEL,
                            canonical_name=model_canon,
                            display_name=model_name,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.88,
                            metadata={
                                "framework": "autogen",
                                "provider": provider,
                                **{k: v for k, v in details.items() if v is not None},
                            },
                            file_path=file_path,
                            line=inst.line,
                            snippet=f"llm_config={{model: {model_name!r}}}",
                            evidence_kind="ast_instantiation",
                        )
                    )

            # --- Orchestrator classes ---
            elif inst.class_name in _ORCHESTRATOR_CLASSES:
                var_name = inst.assigned_to or f"group_{inst.line}"
                canon = canonicalize_text(f"autogen:group:{var_name}")
                agents_arg = inst.args.get("agents", [])
                group_rels: list[RelationshipHint] = []
                if isinstance(agents_arg, list):
                    for agent_ref in agents_arg:
                        if isinstance(agent_ref, str) and agent_ref.startswith("$"):
                            ref_name = agent_ref[1:]
                            ref_canon = canonicalize_text(f"autogen:{ref_name}")
                            group_rels.append(
                                RelationshipHint(
                                    source_canonical=canon,
                                    source_type=ComponentType.AGENT,
                                    target_canonical=ref_canon,
                                    target_type=ComponentType.AGENT,
                                    relationship_type="CALLS",
                                )
                            )

                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.AGENT,
                        canonical_name=canon,
                        display_name=var_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "orchestrator_type": inst.class_name,
                            "framework": "autogen",
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(...)",
                        evidence_kind="ast_instantiation",
                        relationships=group_rels,
                    )
                )

        # register_function / register_for_llm → TOOL
        for call in parse_result.function_calls:
            if call.function_name in {
                "register_function",
                "register_for_llm",
                "register_for_execution",
            }:
                tool_name = _clean(
                    call.args.get("name")
                    or (call.positional_args[0] if call.positional_args else None)
                    or f"tool_{call.line}"
                )
                tool_canon = canonicalize_text(f"autogen:tool:{tool_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=tool_canon,
                        display_name=tool_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={"framework": "autogen", "registration": call.function_name},
                        file_path=file_path,
                        line=call.line,
                        snippet=f"{call.function_name}(...)",
                        evidence_kind="ast_call",
                    )
                )

        # Scan for llm_config = dict(model=...) module/function-level assignments
        for call in parse_result.function_calls:
            var = call.assigned_to or ""
            if call.function_name != "dict":
                continue
            if "llm" not in var.lower() and "config" not in var.lower():
                continue
            model_val = _clean(call.args.get("model") or call.args.get("model_name") or "")
            if not model_val:
                continue
            provider = infer_provider(model_val)
            model_canon = canonicalize_text(model_val.lower())
            details = get_model_details(model_val, provider)
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=model_canon,
                    display_name=model_val,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.80,
                    metadata={
                        "source": "llm_config_dict",
                        "config_var": var,
                        "provider": provider,
                        **{k: v for k, v in details.items() if v is not None},
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"{var} = dict(model={model_val!r})",
                    evidence_kind="ast_call",
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
