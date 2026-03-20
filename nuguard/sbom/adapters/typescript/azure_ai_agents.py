"""Azure AI Agent Service TypeScript/JavaScript Adapter for Xelo SBOM.

Parsing is performed by ``xelo.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).

Supports the ``@azure/ai-agents`` and ``@azure/ai-projects`` npm packages:
- ``new AgentsClient(endpoint, credential)`` / ``new AIProjectClient(...)`` → FRAMEWORK
- ``client.createAgent(model, { name })`` → AGENT + MODEL
- ``ToolUtility.createBingGroundingTool(...)`` → TOOL
- ``ToolUtility.createFileSearchTool(...)`` → TOOL
- ``ToolUtility.createCodeInterpreterTool(...)`` → TOOL
- ``ToolUtility.createFunctionTool({ name })`` → TOOL
- ``ToolUtility.createAzureAISearchTool(...)`` → TOOL
- ``ToolUtility.createConnectedAgentTool(...)`` → TOOL
- ``ToolUtility.createOpenApiTool(...)`` → TOOL
- ``new DefaultAzureCredential()`` / ``ManagedIdentityCredential()`` → AUTH
"""

from __future__ import annotations

from typing import Any

from xelo.adapters.base import ComponentDetection, RelationshipHint
from xelo.adapters.typescript._ts_regex import TSFrameworkAdapter
from xelo.core.ts_parser import TSParseResult, parse_typescript
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType


_AZURE_AI_PACKAGES = [
    "@azure/ai-agents",
    "@azure/ai-projects",
]

# Identity package — only included when other Azure AI packages are present
_IDENTITY_PACKAGE = "@azure/identity"

# Client class names that confirm the Azure AI Agent Service SDK
_FRAMEWORK_CLIENT_CLASSES = {
    "AgentsClient",
    "AIProjectClient",
    "AzureAIProjectClient",
    "AIAgentClient",
}

# Credential classes → AUTH
_CREDENTIAL_CLASSES = {
    "DefaultAzureCredential",
    "ManagedIdentityCredential",
    "ClientSecretCredential",
    "WorkloadIdentityCredential",
    "EnvironmentCredential",
    "InteractiveBrowserCredential",
    "CertificateCredential",
}

# ToolUtility static factory method suffixes → TOOL
_TOOL_UTILITY_METHODS: dict[str, str] = {
    "createBingGroundingTool": "bing_grounding",
    "createFileSearchTool": "file_search",
    "createCodeInterpreterTool": "code_interpreter",
    "createFunctionTool": "function",
    "createAzureAISearchTool": "azure_ai_search",
    "createConnectedAgentTool": "connected_agent",
    "createOpenApiTool": "openapi",
    "createSharePointTool": "sharepoint",
    "createFabricTool": "fabric",
}

# Direct agent creation call names on client or agents sub-object
_CREATE_AGENT_CALLS = {"createAgent", "create_agent"}


class AzureAIAgentsTSAdapter(TSFrameworkAdapter):
    """Detect Azure AI Agent Service SDK usage in TypeScript/JavaScript files."""

    name = "azure_ai_agents_ts"
    priority = 28
    handles_imports = _AZURE_AI_PACKAGES

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
        agent_canonicals: list[str] = []

        # ------------------------------------------------------------------
        # Pass 1: Instantiations — clients and credentials
        # ------------------------------------------------------------------
        for inst in result.instantiations:
            cls = inst.class_name

            if cls in _FRAMEWORK_CLIENT_CLASSES:
                # Already covered by framework node above; record variable name
                # so we can attribute later createAgent() calls.
                pass  # No additional FRAMEWORK node; one is sufficient

            elif cls in _CREDENTIAL_CLASSES:
                cred_canon = canonicalize_text(f"azure:auth:{cls.lower()}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.AUTH,
                        canonical_name=cred_canon,
                        display_name=cls,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={
                            "framework": "azure-ai-agents",
                            "credential_class": cls,
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=inst.line_start,
                        snippet=inst.source_snippet or f"new {cls}()",
                        evidence_kind="ast_instantiation",
                    )
                )

        # ------------------------------------------------------------------
        # Pass 2: Function calls — createAgent, ToolUtility.create*
        # ------------------------------------------------------------------
        for call in result.function_calls:
            fn = call.function_name
            method = call.method_name or fn.split(".")[-1]

            # createAgent calls (client.createAgent or project.agents.createAgent)
            if method in _CREATE_AGENT_CALLS:
                # First positional arg is the model deployment name
                model_name = ""
                if call.positional_args:
                    model_name = self._clean(call.positional_args[0])

                agent_name = (
                    self._resolve(call, "name")
                    or self._assignment_name(source, call.line_start)
                    or f"agent_{call.line_start}"
                )
                agent_canon = canonicalize_text(agent_name.lower())
                agent_canonicals.append(agent_canon)
                rels: list[RelationshipHint] = []

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
                            confidence=0.88,
                            metadata={
                                "framework": "azure-ai-agents",
                                "language": "typescript",
                            },
                            file_path=file_path,
                            line=call.line_start,
                            snippet=f"createAgent({model_name!r}, ...)",
                            evidence_kind="ast_call",
                        )
                    )

                # instructions → PROMPT
                instructions = self._resolve(call, "instructions")
                instr_tvars = self._template_vars(instructions) if instructions else []
                if len(instructions) > 10:
                    prompt_canon = canonicalize_text(f"{agent_name} instructions")
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
                            display_name=f"{agent_name} Instructions",
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.90,
                            metadata={
                                "framework": "azure-ai-agents",
                                "prompt_type": "instructions",
                                "role": "system",
                                "content": instructions,
                                "char_count": len(instructions),
                                "is_template": bool(instr_tvars),
                                "template_variables": instr_tvars,
                                "language": "typescript",
                            },
                            file_path=file_path,
                            line=call.line_start,
                            snippet=instructions[:80],
                            evidence_kind="ast_call",
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
                            "framework": "azure-ai-agents",
                            "model": model_name or None,
                            "has_instructions": len(instructions) > 10,
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=call.line_start,
                        snippet=call.source_snippet or f"createAgent({model_name!r})",
                        evidence_kind="ast_call",
                        relationships=rels,
                    )
                )
                continue

            # ToolUtility.create* calls
            if method in _TOOL_UTILITY_METHODS:
                tool_type = _TOOL_UTILITY_METHODS[method]

                # Function tool has a `name` argument
                tool_name = self._resolve(call, "name", "toolName") or ""
                if not tool_name:
                    # Positional arg for some overloads — only accept simple string values
                    if call.positional_args:
                        raw = self._clean(call.positional_args[0]) or ""
                        # Reject complex values (arrays, objects, long expressions)
                        if (
                            raw
                            and not any(c in raw for c in ("{", "[", ".", "("))
                            and len(raw) < 60
                        ):
                            tool_name = raw
                if not tool_name:
                    tool_name = self._assignment_name(source, call.line_start) or tool_type

                tool_canon = canonicalize_text(f"azure:{tool_type}:{tool_name.lower()}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=tool_canon,
                        display_name=tool_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={
                            "framework": "azure-ai-agents",
                            "tool_type": tool_type,
                            "creation_method": f"ToolUtility.{method}",
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=call.line_start,
                        snippet=call.source_snippet or f"ToolUtility.{method}(...)",
                        evidence_kind="ast_call",
                    )
                )
                continue

            # toolSet.addFileSearchTool / addCodeInterpreterTool shortcuts
            if method in {
                "addFileSearchTool",
                "addCodeInterpreterTool",
                "addBingGroundingTool",
                "addAzureAISearchTool",
            }:
                tool_type = method[3:]  # strip "add" prefix → e.g. "FileSearchTool"
                tool_canon = canonicalize_text(f"azure:{tool_type.lower()}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=tool_canon,
                        display_name=tool_type,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "framework": "azure-ai-agents",
                            "tool_type": tool_type,
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=call.line_start,
                        snippet=call.source_snippet or f"toolSet.{method}(...)",
                        evidence_kind="ast_call",
                    )
                )

        return detected
