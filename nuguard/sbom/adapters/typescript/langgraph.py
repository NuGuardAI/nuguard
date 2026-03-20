"""LangChain.js / LangGraph.js TypeScript Adapter for Xelo SBOM.

Parsing is performed by ``xelo.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).

Supports:
- StateGraph, MessageGraph construction
- .addNode() graph node registration
- ChatOpenAI, ChatAnthropic, ChatGoogleGenerativeAI LLM wrappers
- ToolNode detection
- PromptTemplate, ChatPromptTemplate
- createAgent() function-factory agent detection
- tool() constructor tool detection (reads explicit name: config field)
- InMemoryStore datastore detection
"""

from __future__ import annotations

from typing import Any

from xelo.adapters.base import ComponentDetection, RelationshipHint
from xelo.adapters.typescript._ts_regex import TSFrameworkAdapter
from xelo.core.ts_parser import TSParseResult, parse_typescript
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType


_LANGCHAIN_PACKAGES = [
    "@langchain/langgraph",
    "@langchain/core",
    "@langchain/openai",
    "@langchain/anthropic",
    "@langchain/google-genai",
    "@langchain/community",
    "langchain",
]

_GRAPH_CLASSES = {"StateGraph", "MessageGraph", "Graph"}

_LLM_CLASSES: dict[str, str] = {
    "ChatOpenAI": "openai",
    "AzureChatOpenAI": "azure",
    "ChatAnthropic": "anthropic",
    "ChatGoogleGenerativeAI": "google",
    "ChatVertexAI": "google",
    "ChatOllama": "ollama",
    "ChatMistralAI": "mistral",
    "ChatCohere": "cohere",
    "ChatGroq": "groq",
}

_PROMPT_CLASSES = {
    "PromptTemplate",
    "ChatPromptTemplate",
    "SystemMessagePromptTemplate",
    "HumanMessagePromptTemplate",
    "FewShotPromptTemplate",
}


class LangGraphTSAdapter(TSFrameworkAdapter):
    """Detect LangGraph.js / LangChain.js assets in TypeScript/JavaScript files."""

    name = "langgraph_ts"
    priority = 15
    handles_imports = _LANGCHAIN_PACKAGES

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
        graph_canonicals: list[str] = []

        # --- Graph classes → AGENT nodes ---
        for inst in result.instantiations:
            if inst.class_name not in _GRAPH_CLASSES:
                continue
            var = self._assignment_name(source, inst.line_start) or f"langgraph_{inst.line_start}"
            canon = canonicalize_text(var)
            graph_canonicals.append(canon)
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=canon,
                    display_name=var,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": "langgraph-js",
                        "graph_class": inst.class_name,
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or "",
                    evidence_kind="ast_instantiation",
                )
            )

        # --- addNode() calls → graph node registrations ---
        for call in result.function_calls:
            if call.function_name != "addNode" and call.method_name != "addNode":
                continue
            node_name = call.positional_args[0] if call.positional_args else None
            node_name = self._clean(node_name) if node_name else ""
            if not node_name:
                continue
            canon = canonicalize_text(node_name)
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=canon,
                    display_name=node_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "langgraph-js",
                        "is_graph_node": True,
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=call.line_start,
                    snippet=f"addNode({node_name!r})",
                    evidence_kind="ast_call",
                )
            )

        # --- LLM wrapper classes → MODEL nodes ---
        for inst in result.instantiations:
            provider = _LLM_CLASSES.get(inst.class_name)
            if provider is None:
                continue
            # resolved_arguments has variable references expanded by the symbol table
            model_name = self._resolve(inst, "model", "modelName") or inst.class_name
            canon = canonicalize_text(model_name.lower())
            rels: list[RelationshipHint] = [
                RelationshipHint(
                    source_canonical=gc,
                    source_type=ComponentType.AGENT,
                    target_canonical=canon,
                    target_type=ComponentType.MODEL,
                    relationship_type="USES",
                )
                for gc in graph_canonicals
            ]
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=canon,
                    display_name=model_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "langchain-js",
                        "client_class": inst.class_name,
                        "provider": "azure" if "Azure" in inst.class_name else provider,
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or "",
                    evidence_kind="ast_instantiation",
                    relationships=rels,
                )
            )

        # --- ToolNode → TOOL node ---
        for inst in result.instantiations:
            if inst.class_name != "ToolNode":
                continue
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name="toolnode",
                    display_name="ToolNode",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={"framework": "langgraph-js", "language": "typescript"},
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or "",
                    evidence_kind="ast_instantiation",
                )
            )

        # --- PromptTemplate instantiations → PROMPT nodes ---
        for inst in result.instantiations:
            if inst.class_name not in _PROMPT_CLASSES:
                continue
            template = self._resolve(inst, "template", "0") or ""
            # Use assigned variable name or class name as display
            raw_name = (
                self._assignment_name(result.source or "", inst.line_start) or inst.class_name
            )
            name = raw_name.replace("_", " ").title()
            canon = canonicalize_text(name.lower())
            tvars = self._template_vars(template)
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=canon,
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "langchain-js",
                        "prompt_class": inst.class_name,
                        "role": "system",
                        "content": template,
                        "char_count": len(template),
                        "is_template": bool(tvars),
                        "template_variables": tvars,
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or "",
                    evidence_kind="ast_instantiation",
                )
            )

        # --- tool() function-constructor calls → TOOL nodes ---
        # Matches: const getTool = tool(async (...) => {...}, { name: "get_user_info", ... })
        # Prefers the explicit `name` field in the config object; falls back to the
        # assigned variable name, then to position-based sentinel.
        for call in result.function_calls:
            if call.function_name != "tool" and call.method_name != "tool":
                continue
            args = call.resolved_arguments or call.arguments
            explicit_name: str | None = self._clean(args.get("name")) or None
            if not explicit_name:
                explicit_name = self._assignment_name(source, call.line_start)
            if not explicit_name:
                # Skip unlabelled tool() calls — they can't be uniquely identified
                continue
            tool_canon = canonicalize_text(explicit_name.lower())
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=tool_canon,
                    display_name=explicit_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": "langchain-js",
                        "description": self._clean(args.get("description")),
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=call.line_start,
                    snippet=call.source_snippet or f"tool({{ name: {explicit_name!r} }})",
                    evidence_kind="ast_call",
                )
            )

        # --- createAgent() function-factory calls → AGENT nodes ---
        # Matches: const myAgent = createAgent({ model: ..., tools: [...], ... })
        for call in result.function_calls:
            if call.function_name != "createAgent" and call.method_name != "createAgent":
                continue
            agent_name = (
                self._assignment_name(source, call.line_start) or f"agent_{call.line_start}"
            )
            agent_canon = canonicalize_text(agent_name.lower())
            graph_canonicals.append(agent_canon)
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=agent_canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": "langchain-js",
                        "factory_function": "createAgent",
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=call.line_start,
                    snippet=call.source_snippet or "createAgent({...})",
                    evidence_kind="ast_call",
                )
            )

        # --- InMemoryStore instantiations → DATASTORE nodes ---
        for inst in result.instantiations:
            if inst.class_name != "InMemoryStore":
                continue
            # Use the assigned variable name when available; fall back to the
            # class name so the display value matches user expectations ("InMemoryStore").
            store_name = self._assignment_name(source, inst.line_start) or inst.class_name
            store_canon = canonicalize_text(f"langgraph-js:{store_name}".lower())
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=store_canon,
                    display_name=store_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": "langgraph-js",
                        "datastore_type": "memory",
                        "provider": "langgraph-js",
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or "new InMemoryStore()",
                    evidence_kind="ast_instantiation",
                )
            )

        return detected


# Export alias
LangChainTSAdapter = LangGraphTSAdapter
