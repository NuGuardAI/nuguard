"""Agno (Phidata) framework adapter.

Detects usage of the ``agno`` library:
- ``Agent(name=..., model=..., tools=[...])`` → AGENT nodes
- ``Team(name=..., members=[...])`` → AGENT (team coordinator) nodes
- Model class instantiations (``OpenAIChat``, ``Gemini``, ``Claude``, etc.)
  with an ``id=`` keyword argument → MODEL nodes
- Tool references from ``tools=[...]`` → TOOL nodes
- Agent ``instructions=`` / ``description=`` keyword arguments → PROMPT nodes
  (NuGuard patch: mirrors the openai_agents adapter behaviour so that Agno
  system-prompt text is surfaced as first-class PROMPT assets)
"""

from __future__ import annotations

import re
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter, RelationshipHint
from xelo.adapters.models_kb import get_model_details, infer_provider
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

# Agno model wrapper class names → provider hint
_AGNO_MODEL_CLASSES: dict[str, str] = {
    "OpenAIChat": "openai",
    "AzureOpenAI": "azure_openai",
    "Gemini": "google",
    "Google": "google",
    "GoogleChat": "google",
    "Claude": "anthropic",
    "Anthropic": "anthropic",
    "AnthropicChat": "anthropic",
    "Ollama": "ollama",
    "OllamaTools": "ollama",
    "HuggingFaceChat": "huggingface",
    "Cohere": "cohere",
    "CohereChat": "cohere",
    "Groq": "groq",
    "GroqChat": "groq",
    "MistralChat": "mistral",
    "Mistral": "mistral",
    "DeepSeek": "deepseek",
    "Bedrock": "aws_bedrock",
    "BedrockChat": "aws_bedrock",
    "TogetherAI": "together",
    "Fireworks": "fireworks",
    "Perplexity": "perplexity",
    "XAI": "xai",
    "LMStudio": "lmstudio",
}

_TEMPLATE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Minimum instruction length to emit a PROMPT node (avoids noise from very
# short placeholder strings like "Be helpful.").
_MIN_INSTRUCTION_LENGTH = 40


def _clean(val: Any) -> str:
    """Return a clean string from any value, stripping variable-reference markers."""
    if val is None:
        return ""
    s = str(val)
    if s.startswith("$"):
        return ""
    return s.strip().strip("\"'")


class AgnoAdapter(FrameworkAdapter):
    """Adapter for the Agno (formerly Phidata) multi-agent framework."""

    name = "agno"
    priority = 25  # Between OpenAI Agents (20) and LangGraph (15)
    handles_imports = [
        "agno",
        "agno.agent",
        "agno.team",
        "agno.models",
        "agno.run",
        "agno.tools",
        "agno.workflow",
        "agno.playground",
        "agno.storage",
        "agno.knowledge",
        "agno.embedder",
        "phi",
        "phi.agent",
        "phi.model",
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

        # Pass 1: Collect model instantiations from agno.models.* classes
        # These are recorded as separate instantiations because the parser
        # recursively visits nested calls (e.g. Agent(model=OpenAIChat(id="gpt-4o")))
        model_by_line: dict[int, ComponentDetection] = {}
        for inst in parse_result.instantiations:
            if inst.class_name not in _AGNO_MODEL_CLASSES:
                continue
            args = inst.args or {}
            model_id = _clean(args.get("id") or args.get("model") or args.get("model_id", ""))
            if not model_id or model_id.startswith("$"):
                continue
            provider = _AGNO_MODEL_CLASSES.get(inst.class_name) or infer_provider(model_id)
            details = get_model_details(model_id, provider)
            model_canon = canonicalize_text(model_id.lower())
            det = ComponentDetection(
                component_type=ComponentType.MODEL,
                canonical_name=model_canon,
                display_name=model_id,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.92,
                metadata={
                    "framework": "agno",
                    "provider": provider,
                    "model_class": inst.class_name,
                    **{k: v for k, v in details.items() if v is not None},
                },
                file_path=file_path,
                line=inst.line,
                snippet=f"{inst.class_name}(id={model_id!r})",
                evidence_kind="ast_instantiation",
            )
            detected.append(det)
            model_by_line[inst.line] = det

        # Pass 2: Agent / Team instantiations
        for inst in parse_result.instantiations:
            if inst.class_name not in {"Agent", "Team", "Workflow"}:
                continue
            args = inst.args or {}

            # Determine name
            if inst.class_name == "Agent":
                agent_name = _clean(
                    args.get("name")
                    or (inst.positional_args[0] if inst.positional_args else None)
                    or inst.assigned_to
                    or f"agent_{inst.line}"
                )
            elif inst.class_name == "Team":
                agent_name = _clean(args.get("name") or inst.assigned_to or f"team_{inst.line}")
            else:  # Workflow
                agent_name = _clean(args.get("name") or inst.assigned_to or f"workflow_{inst.line}")

            canon = canonicalize_text(f"agno:{agent_name}")
            rels: list[RelationshipHint] = []

            # Tool references from tools=[] arg
            tools_raw = args.get("tools", [])
            if isinstance(tools_raw, list):
                for tool_ref in tools_raw:
                    if isinstance(tool_ref, str) and not tool_ref.startswith("$"):
                        tool_canon = canonicalize_text(f"agno:tool:{tool_ref}")
                        rels.append(
                            RelationshipHint(
                                source_canonical=canon,
                                source_type=ComponentType.AGENT,
                                target_canonical=tool_canon,
                                target_type=ComponentType.TOOL,
                                relationship_type="CALLS",
                            )
                        )

            # Extract system instructions — Agno uses either `instructions=` or
            # `description=` as the agent's system prompt parameter.
            instructions = _clean(args.get("instructions") or args.get("description", ""))
            template_vars = _TEMPLATE_VAR_RE.findall(instructions) if instructions else []

            # Pre-compute PROMPT canonical name so it can be referenced in both
            # the RelationshipHint (added to the AGENT) and the PROMPT node.
            prompt_display = f"{agent_name} Instructions"
            prompt_canon = canonicalize_text(f"agno:{prompt_display.lower()}")

            # Wire an explicit USES edge from the agent to its prompt so that
            # xelo's relationship-building stage creates the agent_uses_prompt edge.
            if instructions and len(instructions) >= _MIN_INSTRUCTION_LENGTH:
                rels.append(
                    RelationshipHint(
                        source_canonical=canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=prompt_canon,
                        target_type=ComponentType.PROMPT,
                        relationship_type="USES",
                    )
                )

            meta: dict[str, Any] = {
                "framework": "agno",
                "agent_class": inst.class_name,
                "has_instructions": bool(instructions),
            }

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

            # Emit a PROMPT node for the agent's system instructions.
            # Mirrors the openai_agents adapter pattern so that Agno instruction
            # text is surfaced as a first-class PROMPT asset in the AIBOM.
            if instructions and len(instructions) >= _MIN_INSTRUCTION_LENGTH:
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.PROMPT,
                        canonical_name=prompt_canon,
                        display_name=prompt_display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.92,
                        metadata={
                            "framework": "agno",
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

        # Pass 3: @agent.tool decorated functions → TOOL nodes
        for call in parse_result.function_calls:
            if call.function_name in {"tool"} and call.receiver is not None:
                tool_name = _clean(call.assigned_to or f"tool_{call.line}")
                tool_name_override = _clean(
                    (call.args or {}).get("name") or (call.args or {}).get("name_override", "")
                )
                display = tool_name_override or tool_name
                tool_canon = canonicalize_text(f"agno:tool:{display}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=tool_canon,
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={"framework": "agno"},
                        file_path=file_path,
                        line=call.line,
                        snippet=f"@{call.receiver}.tool",
                        evidence_kind="ast_decorator",
                    )
                )

        return detected
