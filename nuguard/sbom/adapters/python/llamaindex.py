"""LlamaIndex (formerly GPT Index) framework adapter.

Detects usage of ``llama_index`` / ``llama-index``:
- ``VectorStoreIndex``, ``SimpleDirectoryReader``, etc. → DATASTORE nodes
- ``OpenAI()``, ``Anthropic()``, etc. LLM wrappers → MODEL nodes
- ``QueryEngine`` / ``RetrieverQueryEngine`` → AGENT nodes
- ``FunctionTool``, ``QueryEngineTool`` → TOOL nodes
- ``ServiceContext`` / ``Settings`` → FRAMEWORK configuration
- ``system_prompt=`` on agent/engine constructors → PROMPT nodes
"""

from __future__ import annotations

import re
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter, RelationshipHint
from xelo.adapters.models_kb import get_model_details
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

_TEMPLATE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_MIN_PROMPT_LENGTH = 40

_INDEX_CLASSES = {
    "VectorStoreIndex",
    "SimpleVectorStore",
    "PineconeVectorStore",
    "ChromaVectorStore",
    "WeaviateVectorStore",
    "QdrantVectorStore",
    "MilvusVectorStore",
    "FaissVectorStore",
    "RedisVectorStore",
    "SimpleKeywordTableIndex",
    "KnowledgeGraphIndex",
    "DocumentSummaryIndex",
    "SummaryIndex",
    "TreeIndex",
}

_LLM_WRAPPER_CLASSES = {
    "OpenAI": "openai",
    "Anthropic": "anthropic",
    "Gemini": "google",
    "VertexAI": "google",
    "MistralAI": "mistral",
    "Cohere": "cohere",
    "Groq": "groq",
    "Ollama": "ollama",
    "LlamaCPP": "meta",
    "HuggingFaceLLM": "huggingface",
    "AzureOpenAI": "azure",
    "BedrockLLM": "bedrock",
    "TogetherLLM": "together",
}

_QUERY_CLASSES = {
    "QueryEngine",
    "RetrieverQueryEngine",
    "RouterQueryEngine",
    "SubQuestionQueryEngine",
    "TransformQueryEngine",
    "ReActAgent",
    "OpenAIAgent",
    "FunctionCallingAgent",
}

_TOOL_CLASSES = {
    "FunctionTool",
    "QueryEngineTool",
    "ToolMetadata",
    "BaseTool",
    "AsyncBaseTool",
}


class LlamaIndexAdapter(FrameworkAdapter):
    """Adapter for LlamaIndex (llama-index / llama_index) framework."""

    name = "llamaindex"
    priority = 60
    handles_imports = [
        "llama_index",
        "llama-index",
        "llama_index.core",
        "llama_index.llms",
        "llama_index.embeddings",
        "llama_index.vector_stores",
        "llama_index.indices",
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
        model_canonicals: list[str] = []

        for inst in parse_result.instantiations:
            # --- Vector stores / indexes → DATASTORE ---
            if inst.class_name in _INDEX_CLASSES:
                var_name = inst.assigned_to or f"index_{inst.line}"
                store_type = (
                    inst.class_name.lower().replace("index", "").replace("store", "").strip("_")
                )
                canon = canonicalize_text(f"llamaindex:datastore:{var_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.DATASTORE,
                        canonical_name=canon,
                        display_name=var_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={
                            "index_class": inst.class_name,
                            "datastore_type": store_type or "vector",
                            "framework": "llamaindex",
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(...)",
                        evidence_kind="ast_instantiation",
                    )
                )

            # --- LLM wrappers → MODEL ---
            elif inst.class_name in _LLM_WRAPPER_CLASSES:
                provider = _LLM_WRAPPER_CLASSES[inst.class_name]
                args = inst.args or {}
                model_name = (
                    _clean(args.get("model") or args.get("model_name") or args.get("model_id"))
                    or inst.class_name
                )
                details = get_model_details(model_name, provider, args)
                model_canon = canonicalize_text(model_name.lower())

                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=model_canon,
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": "llamaindex",
                            "class_name": inst.class_name,
                            "provider": provider,
                            **{k: v for k, v in details.items() if v is not None},
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(model={model_name!r})",
                        evidence_kind="ast_instantiation",
                    )
                )
                model_canonicals.append(model_canon)

            # --- Query engines / agents → AGENT ---
            elif inst.class_name in _QUERY_CLASSES:
                var_name = inst.assigned_to or f"query_{inst.line}"
                canon = canonicalize_text(f"llamaindex:agent:{var_name}")

                # Emit AGENT→MODEL relationship for any models already seen
                rels: list[RelationshipHint] = [
                    RelationshipHint(
                        source_canonical=canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=mc,
                        target_type=ComponentType.MODEL,
                        relationship_type="USES",
                    )
                    for mc in model_canonicals
                ]

                # system_prompt= argument → PROMPT node
                args = inst.args or {}
                system_prompt = _clean(args.get("system_prompt") or args.get("system_message", ""))
                prompt_display = f"{var_name} System Prompt"
                prompt_canon = canonicalize_text(f"llamaindex:{prompt_display.lower()}")
                if system_prompt and len(system_prompt) >= _MIN_PROMPT_LENGTH:
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
                        display_name=var_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "agent_class": inst.class_name,
                            "framework": "llamaindex",
                            "has_instructions": bool(system_prompt),
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(...)",
                        evidence_kind="ast_instantiation",
                        relationships=rels,
                    )
                )

                if system_prompt and len(system_prompt) >= _MIN_PROMPT_LENGTH:
                    template_vars = _TEMPLATE_VAR_RE.findall(system_prompt)
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
                                "content": system_prompt,
                                "char_count": len(system_prompt),
                                "is_template": bool(template_vars),
                                "template_variables": template_vars,
                            },
                            file_path=file_path,
                            line=inst.line,
                            snippet=system_prompt[:80],
                            evidence_kind="ast_instantiation",
                        )
                    )

            # --- Tools → TOOL ---
            elif inst.class_name in _TOOL_CLASSES:
                tool_name = _clean(
                    inst.args.get("name")
                    or (inst.positional_args[0] if inst.positional_args else None)
                    or inst.assigned_to
                    or f"tool_{inst.line}"
                )
                canon = canonicalize_text(f"llamaindex:tool:{tool_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=canon,
                        display_name=tool_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={"tool_class": inst.class_name, "framework": "llamaindex"},
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(name={tool_name!r})",
                        evidence_kind="ast_instantiation",
                    )
                )

        # Class-method builders: VectorStoreIndex.from_documents(...) etc.
        for call in parse_result.function_calls:
            if (
                call.function_name in {"from_args", "from_tools"}
                and call.receiver in _QUERY_CLASSES
            ):
                var_name = call.assigned_to or f"query_{call.line}"
                canon = canonicalize_text(f"llamaindex:agent:{var_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.AGENT,
                        canonical_name=canon,
                        display_name=var_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.82,
                        metadata={"agent_class": call.receiver, "framework": "llamaindex"},
                        file_path=file_path,
                        line=call.line,
                        snippet=f"{call.receiver}.from_args(...)",
                        evidence_kind="ast_call",
                    )
                )
            elif call.function_name == "from_defaults" and call.receiver in _TOOL_CLASSES:
                var_name = call.assigned_to or f"tool_{call.line}"
                canon = canonicalize_text(f"llamaindex:tool:{var_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=canon,
                        display_name=var_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={"tool_class": call.receiver, "framework": "llamaindex"},
                        file_path=file_path,
                        line=call.line,
                        snippet=f"{call.receiver}.from_defaults(...)",
                        evidence_kind="ast_call",
                    )
                )
            elif call.function_name in {"from_documents", "from_vector_store"}:
                var_name = call.assigned_to or f"index_{call.line}"
                canon = canonicalize_text(f"llamaindex:datastore:{var_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.DATASTORE,
                        canonical_name=canon,
                        display_name=var_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.80,
                        metadata={"build_method": call.function_name, "framework": "llamaindex"},
                        file_path=file_path,
                        line=call.line,
                        snippet=f"{call.function_name}(...)",
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
