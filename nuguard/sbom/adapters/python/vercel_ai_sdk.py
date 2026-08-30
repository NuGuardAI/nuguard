"""Vercel AI SDK — Python-side bridge adapter.

Vercel's ``ai`` SDK itself is TypeScript-only (see
``typescript/vercel_ai_sdk.py`` for the real SDK surface: ``tool()``,
``streamText``/``generateText``). The supported way to serve its Data
Stream Protocol from a Python backend — so a frontend using the SDK's
``useChat`` hook can talk to it — is Pydantic AI's ``VercelAIAdapter``
(``pydantic_ai.ui.vercel_ai``), confirmed via
https://ai.pydantic.dev/ui/vercel-ai/ and
https://github.com/pydantic/pydantic-ai/blob/main/docs/ui/vercel-ai.md::

    from pydantic_ai import Agent
    from pydantic_ai.ui.vercel_ai import VercelAIAdapter

    agent = Agent('openai:gpt-5.2')

    @app.post('/chat')
    async def chat(request: Request) -> Response:
        return await VercelAIAdapter.dispatch_request(request, agent=agent)

Detects:
- ``Agent(...)`` (``pydantic_ai.Agent``) → AGENT, plus a MODEL node when the
  first positional/``model=`` argument is a literal ``"provider:model"``
  string.
- ``@<agent_var>.tool`` / ``@<agent_var>.tool_plain`` decorated functions →
  TOOL, same decorator-as-``ParsedCall`` convention as ``mcp_server.py``.
- ``VercelAIAdapter.dispatch_request(request, agent=<var>)`` → tags the
  matching AGENT's ``metadata.extras.vercel_ai_protocol = True`` so it's
  identifiable as Vercel-AI-SDK-frontend-compatible, without inventing a
  new node type for what is just a protocol-bridge marker.
"""

from __future__ import annotations

from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint

_AGENT_CLASS = "Agent"
_TOOL_METHODS = {"tool", "tool_plain"}
_DISPATCH_ADAPTER = "VercelAIAdapter"
_DISPATCH_METHOD = "dispatch_request"


def _clean(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip("'\" ")
    return "" if s.startswith("$") else s


class VercelAISDKPythonAdapter(FrameworkAdapter):
    """Detect Pydantic AI's Vercel AI SDK protocol bridge."""

    name = "vercel_ai_sdk_python"
    priority = 30
    handles_imports = [
        "pydantic_ai",
        "pydantic_ai.ui",
        "pydantic_ai.ui.vercel_ai",
        "pydantic_ai.agent",
    ]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        detected: list[ComponentDetection] = []

        # Agent variable name -> its already-created ComponentDetection, so
        # tool decorators discovered later can append CALLS relationships
        # onto it (mirrors mcp_server.py's deferred-relationship pattern).
        agent_detections: dict[str, ComponentDetection] = {}

        # Agent variable names passed as `agent=` to VercelAIAdapter.dispatch_request(...).
        vercel_bridged_vars: set[str] = set()
        for call in parse_result.function_calls:
            if call.function_name == _DISPATCH_METHOD and call.receiver == _DISPATCH_ADAPTER:
                agent_ref = call.args.get("agent")
                if isinstance(agent_ref, str) and agent_ref.startswith("$"):
                    vercel_bridged_vars.add(agent_ref[1:])

        for inst in parse_result.instantiations:
            if inst.class_name != _AGENT_CLASS:
                continue
            var_name = inst.assigned_to or f"agent_{inst.line}"
            canon = canonicalize_text(f"vercel_ai_sdk:agent:{file_path}:{inst.line}")

            model_raw = _clean(inst.args.get("model")) or (
                _clean(inst.positional_args[0]) if inst.positional_args else ""
            )
            rels: list[RelationshipHint] = []
            if model_raw:
                provider, sep, model_name = model_raw.partition(":")
                if not sep:
                    provider, model_name = "unknown", model_raw
                model_canon = canonicalize_text(f"vercel_ai_sdk:model:{provider}:{model_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=model_canon,
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "framework": "vercel_ai_sdk",
                            "provider": provider,
                            "language": "python",
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"Agent({model_raw!r})",
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

            metadata: dict[str, Any] = {"framework": "vercel_ai_sdk", "language": "python"}
            if var_name in vercel_bridged_vars:
                metadata["vercel_ai_protocol"] = True
            agent_detection = ComponentDetection(
                component_type=ComponentType.AGENT,
                canonical_name=canon,
                display_name=var_name.replace("_", " ").title() or "PydanticAI Agent",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.85,
                metadata=metadata,
                file_path=file_path,
                line=inst.line,
                snippet=f"Agent({model_raw!r})" if model_raw else "Agent(...)",
                evidence_kind="ast_instantiation",
                relationships=rels,
            )
            detected.append(agent_detection)
            agent_detections[var_name] = agent_detection

        for call in parse_result.function_calls:
            if call.function_name not in _TOOL_METHODS or call.assigned_to is None:
                continue
            if agent_detections and call.receiver not in agent_detections:
                continue
            tool_name = call.assigned_to
            canon = canonicalize_text(f"vercel_ai_sdk:tool:{tool_name}:{file_path}:{call.line}")
            owning_agent = agent_detections.get(call.receiver or "")
            if owning_agent is not None:
                owning_agent.relationships.append(
                    RelationshipHint(
                        source_canonical=owning_agent.canonical_name,
                        source_type=ComponentType.AGENT,
                        target_canonical=canon,
                        target_type=ComponentType.TOOL,
                        relationship_type="CALLS",
                    )
                )
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canon,
                    display_name=tool_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "framework": "vercel_ai_sdk",
                        "decorator": f"@{call.receiver or 'agent'}.{call.function_name}",
                        "language": "python",
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"@{call.receiver or 'agent'}.{call.function_name}\ndef {tool_name}(...)",
                    evidence_kind="ast_decorator",
                )
            )

        return detected


__all__ = ["VercelAISDKPythonAdapter"]
