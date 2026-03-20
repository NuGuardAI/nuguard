"""Bedrock AgentCore SDK adapter.

Detects usage of the AWS ``bedrock-agentcore`` SDK:
- ``BedrockAgentCoreApp(...)`` instantiation → FRAMEWORK node
- ``@app.entrypoint`` / ``@app.route(...)`` decorated functions → AGENT nodes
- ``@app.async_task`` decorated functions → TOOL nodes
- ``requires_access_token(...)`` / ``@app.oauth2_token(...)`` → AUTH nodes
- ``AgentCoreMemorySessionManager`` instantiation → DATASTORE node
"""

from __future__ import annotations

from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

# Decorator names that mark the primary invocable handler → AGENT
_ENTRYPOINT_DECORATORS = {"entrypoint", "route", "stream", "websocket"}
# Decorator names that mark background async tasks → TOOL
_TASK_DECORATORS = {"async_task", "task", "background_task"}
# Decorators / functions that indicate Auth
_AUTH_FUNCTIONS = {"requires_access_token", "oauth2_token", "require_token", "authenticate"}
# Class names that indicate the SDK runtime
_RUNTIME_CLASSES = {"BedrockAgentCoreApp", "BedrockAgentCore", "AgentCoreApp"}
# Class names for SDK memory → DATASTORE
_MEMORY_CLASSES = {
    "AgentCoreMemorySessionManager",
    "MemorySessionManager",
    "BedrockAgentCoreMemoryClient",
}


def _clean(val: Any) -> str:
    if val is None:
        return ""
    s = str(val)
    if s.startswith("$"):
        return ""
    return s.strip().strip("\"'")


class BedrockAgentCoreAdapter(FrameworkAdapter):
    """Adapter for the AWS Bedrock AgentCore SDK."""

    name = "bedrock_agentcore"
    priority = 30
    handles_imports = [
        "bedrock_agentcore",
        "bedrock_agentcore.runtime",
        "bedrock_agentcore.identity",
        "bedrock_agentcore.identity.auth",
        "bedrock_agentcore.memory",
        "bedrock_agentcore.tools",
        "bedrock_agentcore.services",
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

        # Track which variables hold BedrockAgentCoreApp instances so we can
        # correctly attribute @var.entrypoint decorators.
        app_var_names: set[str] = set()

        # Pass 1: Instantiations
        for inst in parse_result.instantiations:
            if inst.class_name in _RUNTIME_CLASSES:
                # The app variable name is assigned_to
                if inst.assigned_to:
                    app_var_names.add(inst.assigned_to)

            elif inst.class_name in _MEMORY_CLASSES:
                mem_name = _clean(inst.assigned_to or f"memory_{inst.line}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.DATASTORE,
                        canonical_name=canonicalize_text(f"bedrock_agentcore:memory:{mem_name}"),
                        display_name=mem_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={"framework": "bedrock_agentcore", "datastore_type": "memory"},
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(...)",
                        evidence_kind="ast_instantiation",
                    )
                )

        # Pass 2: Function calls / decorators in parse_result.function_calls
        # After the AST parser extension, @app.entrypoint results in a ParsedCall
        # with function_name="entrypoint", receiver="app", assigned_to=handler_name
        for call in parse_result.function_calls:
            fn = call.function_name
            recv = call.receiver

            if fn in _ENTRYPOINT_DECORATORS and (
                recv is None or recv in app_var_names or recv == "app"
            ):
                handler_name = _clean(call.assigned_to or f"handler_{call.line}")
                canon = canonicalize_text(f"bedrock_agentcore:agent:{handler_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.AGENT,
                        canonical_name=canon,
                        display_name=handler_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={"framework": "bedrock_agentcore", "decorator": fn},
                        file_path=file_path,
                        line=call.line,
                        snippet=f"@{recv or 'app'}.{fn}",
                        evidence_kind="ast_decorator",
                    )
                )

            elif fn in _TASK_DECORATORS and (
                recv is None or recv in app_var_names or recv == "app"
            ):
                task_name = _clean(call.assigned_to or f"task_{call.line}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=canonicalize_text(f"bedrock_agentcore:tool:{task_name}"),
                        display_name=task_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={"framework": "bedrock_agentcore", "decorator": fn},
                        file_path=file_path,
                        line=call.line,
                        snippet=f"@{recv or 'app'}.{fn}",
                        evidence_kind="ast_decorator",
                    )
                )

            elif fn in _AUTH_FUNCTIONS:
                auth_flow = _clean((call.args or {}).get("auth_flow", ""))
                provider = _clean((call.args or {}).get("provider_name", ""))
                canon = canonicalize_text(
                    f"bedrock_agentcore:auth:{provider or auth_flow or 'oauth2'}"
                )
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.AUTH,
                        canonical_name=canon,
                        display_name=provider or auth_flow or "oauth2",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={
                            "framework": "bedrock_agentcore",
                            "auth_type": "oauth2",
                            "auth_flow": auth_flow,
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=f"@{fn}(provider={provider!r})",
                        evidence_kind="ast_decorator",
                    )
                )

        return detected
