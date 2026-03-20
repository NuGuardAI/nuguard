"""LangGraph framework adapter.

Extracts AI assets from LangGraph-based applications:
- ``StateGraph`` / ``MessageGraph`` instantiation → AGENT nodes
- ``.add_node()`` calls → AGENT nodes
- ``.add_edge()`` / ``.add_conditional_edges()`` → graph AGENT-INVOKES-AGENT hints
- ``ToolNode`` → TOOL nodes (with underlying tool resolution)
- ``ChatOpenAI``, ``ChatAnthropic``, etc. → MODEL nodes (via models_kb)
- ``create_react_agent`` / ``create_supervisor`` / factory functions → AGENT nodes
- ``@tool`` decorated functions → TOOL nodes
- ``FAISS.load_local`` / ``FAISS.from_texts`` / ``FAISS.save_local`` → DATASTORE nodes
- ``SystemMessage`` / chat prompt templates / large string literals → PROMPT nodes
"""

from __future__ import annotations

import re
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter, RelationshipHint
from xelo.adapters.models_kb import (
    LANGCHAIN_LLM_CLASS_PROVIDERS,
    get_model_details,
)
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

# ---------------------------------------------------------------------------
# LangGraph-specific constants
# ---------------------------------------------------------------------------

_LANGGRAPH_IMPORTS = [
    "langgraph",
    "langgraph.graph",
    "langgraph.prebuilt",
    "langgraph_supervisor",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langchain_anthropic",
    "langchain_community",
]

_STATEGRAPH_CLASSES = {"StateGraph", "MessageGraph", "Graph"}

_TOOLNODE_CLASSES = {"ToolNode"}

_AGENT_FACTORY_FUNCTIONS = {
    "create_react_agent",
    "create_tool_calling_agent",
    "create_openai_functions_agent",
    "create_openai_tools_agent",
    "create_structured_chat_agent",
    # Supervisor / orchestration graphs
    "create_supervisor",
    "make_supervisor_graph",
    # Azure AI Foundry agent factory methods
    "create_prompt_agent_node",
    "create_agent_node",
    "create_agent",
}

_PROMPT_CLASSES = {
    "SystemMessage",
    "HumanMessagePromptTemplate",
    "AIMessagePromptTemplate",
    "ChatPromptTemplate",
    "PromptTemplate",
}

# FAISS vector-store operations that imply a named datastore
_FAISS_CONSTRUCTOR_METHODS = {"load_local", "from_texts", "from_documents", "from_embeddings"}
_FAISS_SAVE_METHOD = "save_local"

# Constant names that encode the index path/name — used for identity resolution
_INDEX_PATH_CONST_RE = re.compile(
    r"([A-Z][A-Z0-9_]*(?:INDEX|STORE|DB|PATH)[A-Z0-9_]*)",
)
# Strip common suffix noise from constant names to get a readable store name
_PATH_CONST_CLEANUP_RE = re.compile(r"[_ ]*(path|dir|folder|location)$", re.IGNORECASE)

# Python built-in names that may appear as argument-wrapping function calls
# (e.g. str(MY_CONST)) and should never be used as FAISS store identifiers.
_PYTHON_BUILTIN_NAMES = frozenset(
    {
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "bytes",
        "bytearray",
        "range",
        "type",
        "object",
        "complex",
        "len",
        "repr",
        "abs",
        "max",
        "min",
        "sum",
        "sorted",
        "enumerate",
        "zip",
        "map",
        "filter",
        "open",
        "print",
    }
)

_TEMPLATE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Graph-internal node names that should never be emitted as AGENT nodes
_LANGGRAPH_INTERNAL_NODES = {"__start__", "__end__", "tools", "END", "START"}


class LangGraphAdapter(FrameworkAdapter):
    """Adapter for LangGraph / LangChain framework detection."""

    name = "langgraph"
    priority = 10
    handles_imports = _LANGGRAPH_IMPORTS

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        # Determine if langgraph is actually imported (vs just langchain)
        imported_modules = {imp.module or "" for imp in parse_result.imports}
        has_langgraph = any(
            m == "langgraph" or m.startswith("langgraph.") for m in imported_modules
        )
        # Emit the correct framework node
        if has_langgraph:
            framework_det = self._framework_node(file_path)
        else:
            # Only langchain imported — emit framework:langchain, not framework:langgraph
            from xelo.types import ComponentType as _CT

            framework_det = ComponentDetection(
                component_type=_CT.FRAMEWORK,
                canonical_name="framework:langchain",
                display_name="framework:langchain",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.95,
                metadata={"framework": "langchain"},
                file_path=file_path,
                line=0,
                snippet="import langchain",
                evidence_kind="ast_import",
            )
        detected: list[ComponentDetection] = [framework_det]

        # Track node canonical names for relationship building
        agent_canonicals: list[str] = []
        model_canonicals: list[str] = []
        tool_canonicals: list[str] = []
        node_name_map: dict[str, str] = {}  # node_name → canonical_name

        # Build a symbol table for constant resolution (e.g. DOCS_INDEX_PATH → "docs_index")
        symbol_table = _build_symbol_table(parse_result, content)

        # Pre-compute set of tool classes imported from *.tools.* modules.
        # Used in both section 4 (ToolNode fallback) and section 4b (constructor detection).
        # e.g. `from langchain_azure_ai.tools import AzureAIDocumentIntelligenceTool`
        tool_classes_from_imports: set[str] = set()
        for imp in parse_result.imports:
            if imp.module and ".tools" in imp.module:
                for name in imp.names:
                    tool_classes_from_imports.add(name)

        # Pre-compute variables assigned from agent factory functions (used in section 1).
        # e.g. `parser_node = service.create_prompt_agent_node(...)` → "parser_node"
        agent_factory_vars: set[str] = set()
        for call in parse_result.function_calls:
            if call.function_name in _AGENT_FACTORY_FUNCTIONS and call.assigned_to:
                agent_factory_vars.add(call.assigned_to)

        # 0. @tool-decorated functions → TOOL nodes
        for call in parse_result.function_calls:
            if call.function_name != "tool":
                continue
            # Must be a bare decorator (@tool) — assigned_to is the function name
            func_name = call.assigned_to
            if not func_name:
                continue
            canon = canonicalize_text(f"langchain:tool:{func_name}")
            det = ComponentDetection(
                component_type=ComponentType.TOOL,
                canonical_name=canon,
                display_name=func_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.90,
                metadata={"tool_type": "decorated_function", "framework": "langchain"},
                file_path=file_path,
                line=call.line,
                snippet=f"@tool\ndef {func_name}(...)",
                evidence_kind="ast_call",
            )
            detected.append(det)
            tool_canonicals.append(canon)

        # 1. .add_node() calls → AGENT (graph nodes)
        for call in parse_result.function_calls:
            if call.function_name != "add_node":
                continue
            node_name = None
            if call.positional_args:
                first = call.positional_args[0]
                if isinstance(first, str) and not first.startswith("$"):
                    node_name = first.strip("'\"")
            if not node_name:
                node_name = _clean(call.args.get("node") or call.args.get("name"))
            if not node_name or node_name in _LANGGRAPH_INTERNAL_NODES:
                continue

            # Check what the second arg (callback) is:
            # - If it's a known agent factory var → definitely an agent (high confidence)
            # - If it's unknown/None → keep as agent (can't tell, might be imported)
            # - If the second arg is a known non-agent callable (not from any factory),
            #   skip to avoid FPs from bridging/routing functions
            second_arg_var: str | None = None
            if len(call.positional_args) >= 2:
                arg2 = call.positional_args[1]
                if isinstance(arg2, str) and arg2.startswith("$"):
                    second_arg_var = arg2[1:]
            if second_arg_var is not None and second_arg_var not in agent_factory_vars:
                # The callback is a local variable but not from a known agent factory.
                # It could be: (a) imported agent, (b) plain bridging function.
                # Only skip if it looks like a plain snake_case function (no uppercase
                # hint of a class/imported agent, no "_node" / "_agent" suffix).
                looks_like_plain_func = (
                    second_arg_var == second_arg_var.lower()
                    and not second_arg_var.endswith(("_node", "_agent", "_graph"))
                )
                if looks_like_plain_func:
                    continue

            canon = canonicalize_text(f"langgraph:{node_name}")
            det = ComponentDetection(
                component_type=ComponentType.AGENT,
                canonical_name=canon,
                display_name=node_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.85,
                metadata={"registration_method": "add_node", "framework": "langgraph"},
                file_path=file_path,
                line=call.line,
                snippet=f"add_node({node_name!r}, ...)",
                evidence_kind="ast_call",
            )
            detected.append(det)
            agent_canonicals.append(canon)
            node_name_map[node_name] = canon

        # 3. .add_edge() / .add_conditional_edges() → RelationshipHints
        for call in parse_result.function_calls:
            if call.function_name == "add_edge":
                src = _positional_str(call, 0) or _clean(call.args.get("source"))
                tgt = _positional_str(call, 1) or _clean(call.args.get("target"))
                if src and tgt:
                    src_canon = node_name_map.get(src, canonicalize_text(f"langgraph:{src}"))
                    tgt_canon = node_name_map.get(tgt, canonicalize_text(f"langgraph:{tgt}"))
                    # Attach as relationship hints on the first agent node
                    if detected:
                        detected[-1].relationships.append(
                            RelationshipHint(
                                source_canonical=src_canon,
                                source_type=ComponentType.AGENT,
                                target_canonical=tgt_canon,
                                target_type=ComponentType.AGENT,
                                relationship_type="CALLS",
                            )
                        )

        # 4. ToolNode instantiations → TOOL
        # Prefer emitting the concrete tools passed into ToolNode over a generic wrapper.
        for inst in parse_result.instantiations:
            if inst.class_name not in _TOOLNODE_CLASSES:
                continue
            # Try to resolve the tools list passed as the first positional arg
            resolved_tools: list[str] = []
            if inst.positional_args:
                first = inst.positional_args[0]
                if isinstance(first, list):
                    for item in first:
                        name = _clean(item)
                        if name:
                            resolved_tools.append(name)
                elif isinstance(first, str) and first.startswith("$"):
                    # Variable reference — look it up in the symbol table
                    ref = first[1:]
                    resolved_tools = symbol_table.get(ref, [])

            if resolved_tools:
                for tool_name in resolved_tools:
                    canon = canonicalize_text(f"langchain:tool:{tool_name}")
                    det = ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=canon,
                        display_name=tool_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={"tool_type": "ToolNode_member", "framework": "langgraph"},
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"ToolNode([{tool_name}])",
                        evidence_kind="ast_instantiation",
                    )
                    detected.append(det)
                    tool_canonicals.append(canon)
            else:
                # Fallback: emit the wrapper with the assigned var name, UNLESS
                # there are specific tool-class instantiations in this file that
                # will be detected by section 4b (prefer concrete class names).
                has_tool_class_insts = any(
                    i.class_name in tool_classes_from_imports
                    for i in parse_result.instantiations
                    if i.class_name not in _TOOLNODE_CLASSES
                )
                if has_tool_class_insts:
                    continue  # concrete tools will be detected in section 4b
                var_name = inst.assigned_to or f"tools_{inst.line}"
                canon = canonicalize_text(f"langgraph:toolnode:{var_name}")
                det = ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canon,
                    display_name=var_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.80,
                    metadata={"tool_type": "ToolNode", "framework": "langgraph"},
                    file_path=file_path,
                    line=inst.line,
                    snippet=f"{inst.class_name}(...)",
                    evidence_kind="ast_instantiation",
                )
                detected.append(det)
                tool_canonicals.append(canon)

        # 4b. Tool-class constructors imported from *.tools.* modules → TOOL
        # e.g. `from langchain_community.tools import TavilySearchResults`
        # then  `tavily_tool = TavilySearchResults(max_results=5)` → TOOL tavily_tool
        # (tool_classes_from_imports was pre-computed above; reuse here)
        for inst in parse_result.instantiations:
            if inst.class_name not in tool_classes_from_imports:
                continue
            tool_name = inst.assigned_to or inst.class_name
            canon = canonicalize_text(f"langchain:tool:{tool_name}")
            det = ComponentDetection(
                component_type=ComponentType.TOOL,
                canonical_name=canon,
                display_name=tool_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.88,
                metadata={
                    "tool_type": "constructor",
                    "tool_class": inst.class_name,
                    "framework": "langchain",
                },
                file_path=file_path,
                line=inst.line,
                snippet=f"{inst.class_name}(...)",
                evidence_kind="ast_instantiation",
            )
            detected.append(det)
            tool_canonicals.append(canon)

        # 5. LangChain LLM wrapper instantiations → MODEL
        for inst in parse_result.instantiations:
            if inst.class_name not in LANGCHAIN_LLM_CLASS_PROVIDERS:
                continue
            provider = LANGCHAIN_LLM_CLASS_PROVIDERS[inst.class_name]
            args = inst.args or {}
            raw_model = (
                args.get("model")
                or args.get("model_name")
                or args.get("model_id")  # LangChain Bedrock uses model_id=
                or args.get("deployment_name")
            )
            # Resolve variable references (e.g. model=$MODEL_ID) via symbol table
            if isinstance(raw_model, str) and raw_model.startswith("$"):
                ref = raw_model[1:]
                resolved = symbol_table.get(ref, [])
                raw_model = resolved[0] if resolved else None
            model_name = _clean(raw_model) or inst.class_name
            # When no model string arg is resolvable, the class name itself
            # (e.g. "ChatOpenAI") is still useful — it identifies the provider
            # and wrapper being used.  Emit with lower confidence.
            class_name_fallback = model_name == inst.class_name
            details = get_model_details(model_name, provider, args)
            canon = canonicalize_text(model_name.lower())

            rels: list[RelationshipHint] = []
            for agent_canon in agent_canonicals:
                rels.append(
                    RelationshipHint(
                        source_canonical=agent_canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=canon,
                        target_type=ComponentType.MODEL,
                        relationship_type="USES",
                    )
                )

            det = ComponentDetection(
                component_type=ComponentType.MODEL,
                canonical_name=canon,
                display_name=model_name,
                adapter_name=self.name,
                priority=self.priority,
                # Lower confidence when falling back to class name alone
                confidence=0.75 if class_name_fallback else 0.90,
                metadata={
                    "framework": "langchain",
                    "class_name": inst.class_name,
                    "provider": provider,
                    **{k: v for k, v in details.items() if v is not None},
                },
                file_path=file_path,
                line=inst.line,
                snippet=f"{inst.class_name}(...)",
                evidence_kind="ast_instantiation",
                relationships=rels,
            )
            detected.append(det)
            model_canonicals.append(canon)

        # 6. Agent factory functions (create_react_agent, etc.)
        for call in parse_result.function_calls:
            if call.function_name not in _AGENT_FACTORY_FUNCTIONS:
                continue
            agent_name = call.assigned_to or call.function_name
            canon = canonicalize_text(f"langgraph:{agent_name}")
            factory_rels: list[RelationshipHint] = []

            # First positional arg → LLM reference
            if call.positional_args:
                llm_ref = call.positional_args[0]
                if isinstance(llm_ref, str) and not llm_ref.startswith("$"):
                    model_canon = canonicalize_text(f"langchain:{llm_ref}")
                    factory_rels.append(
                        RelationshipHint(
                            source_canonical=canon,
                            source_type=ComponentType.AGENT,
                            target_canonical=model_canon,
                            target_type=ComponentType.MODEL,
                            relationship_type="USES",
                        )
                    )

            # Second positional arg → tools list
            if len(call.positional_args) >= 2:
                tools_ref = call.positional_args[1]
                if isinstance(tools_ref, list):
                    for tool_name in tools_ref:
                        if isinstance(tool_name, str) and not tool_name.startswith("$"):
                            tool_canon = canonicalize_text(f"langchain:tool:{tool_name}")
                            factory_rels.append(
                                RelationshipHint(
                                    source_canonical=canon,
                                    source_type=ComponentType.AGENT,
                                    target_canonical=tool_canon,
                                    target_type=ComponentType.TOOL,
                                    relationship_type="CALLS",
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
                    metadata={
                        "factory_function": call.function_name,
                        "is_agent_graph": True,
                        "framework": "langchain",
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"{call.function_name}(...)",
                    evidence_kind="ast_call",
                    relationships=factory_rels,
                )
            )

        # 6b. Compiled graph → AGENT  (builder.compile(...) → graph_var)
        # Blocklist: standard-library / third-party objects that also have a
        # .compile() method but are NOT LangGraph graph builders (e.g. re,
        # regex, pattern, lxml, ast).  Without this guard,
        # ``pattern = re.compile(...)`` would trigger this section.
        _COMPILE_RECEIVER_BLOCKLIST = frozenset(
            {
                "re",
                "regex",
                "re2",
                "regexp",
                "pattern",
                "lxml",
                "ast",
                "struct",
                "ctypes",
                "parser",
                "_re",
                "compiled",
            }
        )
        for call in parse_result.function_calls:
            if call.function_name != "compile":
                continue
            # Skip non-graph .compile() calls (e.g. re.compile(r"..."))
            if (call.receiver or "") in _COMPILE_RECEIVER_BLOCKLIST:
                continue
            agent_name = call.assigned_to
            if not agent_name:
                continue
            # Skip private variables (e.g. _compiled, _graph) — unlikely agent names
            if agent_name.startswith("_"):
                continue
            canon = canonicalize_text(f"langgraph:{agent_name}")
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "factory_function": "compile",
                        "is_agent_graph": True,
                        "framework": "langgraph",
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"{call.receiver or 'graph'}.compile(...) → {agent_name}",
                    evidence_kind="ast_call",
                )
            )
            agent_canonicals.append(canon)

        # 7. Prompt detection (SystemMessage, ChatPromptTemplate, large string literals)
        for inst in parse_result.instantiations:
            if inst.class_name not in _PROMPT_CLASSES:
                continue
            content_val = _clean(
                inst.args.get("content")
                or (inst.positional_args[0] if inst.positional_args else None)
            )
            if not content_val or len(content_val) < 40:
                # Also accept ChatPromptTemplate.from_messages without a resolved string
                if inst.class_name not in {"ChatPromptTemplate", "PromptTemplate"}:
                    continue
                # Use the assigned variable name as the display name
                if not inst.assigned_to:
                    continue
                content_val = ""
            role = _detect_role(inst.class_name)
            template_vars = _TEMPLATE_VAR_RE.findall(content_val) if content_val else []
            # Prefer the assigned variable name for the display name (Fix 2)
            dname = _prompt_display_name(
                content_val, inst.assigned_to or inst.class_name, inst.line
            )
            # Canonical matches the display name for clean, readable output
            canon = canonicalize_text(dname.lower())
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=canon,
                    display_name=dname,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.80,
                    metadata={
                        "framework": "langchain",
                        "message_type": inst.class_name,
                        "role": role,
                        "content": content_val,
                        "char_count": len(content_val),
                        "is_template": bool(template_vars),
                        "template_variables": template_vars,
                    },
                    file_path=file_path,
                    line=inst.line,
                    snippet=f"{inst.class_name}(content=...)",
                    evidence_kind="ast_instantiation",
                )
            )

        # 7b. ChatPromptTemplate.from_messages() / from_template() calls → PROMPT (assignment-aware)
        for call in parse_result.function_calls:
            if call.function_name not in {"from_messages", "from_template"}:
                continue
            # Only care about prompt-class methods
            prompt_var = call.assigned_to
            if not prompt_var:
                continue
            # Use the exact variable name as display — it's already a meaningful
            # snake_case identifier (e.g. "retrieval_prompt", "fallback_prompt").
            dname = prompt_var
            canon = canonicalize_text(dname.lower())
            content, role, template_vars = _extract_messages_content(call)
            snippet = (
                content[:80] + ("..." if len(content) > 80 else "")
                if content
                else f"ChatPromptTemplate.{call.function_name}(...)"
            )
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=canon,
                    display_name=dname,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.82,
                    metadata={
                        "framework": "langchain",
                        "message_type": "ChatPromptTemplate",
                        "role": role,
                        "content": content,
                        "char_count": len(content),
                        "is_template": bool(template_vars),
                        "template_variables": template_vars,
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=snippet,
                    evidence_kind="ast_call",
                )
            )

        # 8. FAISS vector-store operations → DATASTORE (with identity from assignment or path constant)
        faiss_imported = any(
            ("faiss" in imp.module.lower() or imp.module == "langchain_community.vectorstores")
            for imp in parse_result.imports
        ) or any("faiss" in name.lower() for imp in parse_result.imports for name in imp.names)
        for call in parse_result.function_calls:
            if call.function_name not in (_FAISS_CONSTRUCTOR_METHODS | {_FAISS_SAVE_METHOD}):
                continue
            # Only emit for FAISS.* calls (receiver should be "FAISS" or similar)
            if call.receiver and call.receiver not in {"FAISS", "faiss"}:
                continue
            # Derive the store name from (in order of preference):
            # 1. The path constant passed as first arg (e.g. DOCS_INDEX_PATH → "docs_index")
            # 2. The file-system path string literal (e.g. "data/docs_index" → "docs_index")
            # 3. The assigned variable name (e.g. docs_retriever → "docs")
            store_name = _resolve_faiss_store_name(
                call=call,
                symbol_table=symbol_table,
                source=content,
            )
            if not store_name:
                store_name = "faiss"
            canon = canonicalize_text(f"langchain:datastore:faiss:{store_name}")
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=canon,
                    display_name=store_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "datastore_type": "vector",
                        "provider": "faiss",
                        "framework": "langchain",
                        "operation": call.function_name,
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"FAISS.{call.function_name}(...)",
                    evidence_kind="ast_call",
                )
            )

        # 8b. FAISS import-level inference for index path constants
        # When a file imports path constants like DOCS_INDEX_PATH, TICKETS_INDEX_PATH, etc.
        # AND also imports FAISS, emit DATASTORE nodes for each constant even if the
        # call itself uses a loop variable (masking the direct constant reference).
        if faiss_imported:
            for imp in parse_result.imports:
                for imported_name in imp.names:
                    m = _INDEX_PATH_CONST_RE.search(imported_name)
                    if not m:
                        continue
                    raw = m.group(1).lower()
                    raw = _PATH_CONST_CLEANUP_RE.sub("", raw).strip("_")
                    if not raw:
                        continue
                    canon = canonicalize_text(f"langchain:datastore:faiss:{raw}")
                    # Suppress if already emitted from call-level detection
                    if any(
                        d.canonical_name == canon
                        for d in detected
                        if d.component_type == ComponentType.DATASTORE
                    ):
                        continue
                    detected.append(
                        ComponentDetection(
                            component_type=ComponentType.DATASTORE,
                            canonical_name=canon,
                            display_name=raw,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.80,
                            metadata={
                                "datastore_type": "vector",
                                "provider": "faiss",
                                "framework": "langchain",
                                "operation": "import",
                            },
                            file_path=file_path,
                            line=imp.line,
                            snippet=f"import {imported_name}",
                            evidence_kind="ast_import",
                        )
                    )

        # Large string literals that look like prompts
        for lit in parse_result.string_literals:
            if lit.is_docstring or len(lit.value) < 200:
                continue
            if not _is_prompt_literal(lit.value, lit.context or ""):
                continue
            template_vars = _TEMPLATE_VAR_RE.findall(lit.value)
            dname = _prompt_display_name(lit.value, lit.context or "", lit.line)
            canon = canonicalize_text(dname.lower())
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=canon,
                    display_name=dname,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.60,
                    metadata={
                        "framework": "langchain",
                        "role": _detect_role_from_content(lit.value),
                        "content": lit.value,
                        "char_count": len(lit.value),
                        "is_template": bool(template_vars),
                        "template_variables": template_vars,
                    },
                    file_path=file_path,
                    line=lit.line,
                    snippet=lit.value[:80] + ("..." if len(lit.value) > 80 else ""),
                    evidence_kind="ast_call",
                )
            )

        return detected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_messages_content(call: Any) -> tuple[str, str, list[str]]:
    """Extract (content, role, template_vars) from a from_messages/from_template call.

    For ``from_template`` the single string arg is returned as content.
    For ``from_messages`` the positional list of ``[role, text]`` tuples is joined;
    system/ai messages set the primary role.
    """
    if call.function_name == "from_template":
        raw = call.positional_args[0] if call.positional_args else ""
        text = raw if isinstance(raw, str) and not raw.startswith("$") else ""
        tvars = _TEMPLATE_VAR_RE.findall(text)
        return text, _detect_role_from_content(text) or "system", tvars

    # from_messages: positional_args[0] is list[list[role, text]]
    if not call.positional_args or not isinstance(call.positional_args[0], list):
        return "", "system", []

    parts: list[str] = []
    for msg in call.positional_args[0]:
        if isinstance(msg, list) and len(msg) == 2:
            msg_text = msg[1] if isinstance(msg[1], str) and not str(msg[1]).startswith("$") else ""
            if msg_text:
                parts.append(msg_text)

    content = "\n".join(parts)
    tvars = _TEMPLATE_VAR_RE.findall(content)
    return content, _detect_role_from_content(content) or "system", tvars


def _clean(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip("'\"` ")
    if s.startswith("$") or s in {"<complex>", "<lambda>", "<dict>", "<list>"}:
        return ""
    return s


def _positional_str(call: Any, idx: int) -> str:
    if call.positional_args and len(call.positional_args) > idx:
        v = call.positional_args[idx]
        if isinstance(v, str) and not v.startswith("$"):
            return v.strip("'\"")
    return ""


def _infer_var_from_source(source: str, line: int) -> str | None:
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", lines[line - 1])
        if m:
            return m.group(1)
    return None


def _prompt_display_name(content: str, context: str, line: int) -> str:
    """Derive a human-readable name for a detected prompt."""
    ctx = context.strip()
    if ctx:
        # Split camelCase/PascalCase into words before lowercasing
        ctx_words = re.sub(r"([a-z])([A-Z])", r"\1_\2", ctx)
        slug = re.sub(r"[^a-z0-9_]", "_", ctx_words.lower()).strip("_")
        if slug and slug not in {"prompt", "template", "message", "content", "text", "str"}:
            return slug.replace("_", " ").title()
    cl = content.lower()[:400]
    if re.search(r"\byou are\s", cl):
        return "System Prompt"
    if any(k in cl for k in ["answer the question", "given the context"]):
        return "RAG Prompt"
    if any(k in cl for k in ["example:", "input:", "output:"]):
        return "Few Shot Prompt"
    if "summarize" in cl:
        return "Summarize Prompt"
    if "translate" in cl:
        return "Translate Prompt"
    return f"Prompt {line}"


def _detect_role(class_name: str) -> str | None:
    if "System" in class_name:
        return "system"
    if "Human" in class_name:
        return "user"
    if "AI" in class_name:
        return "assistant"
    return None


def _detect_role_from_content(text: str) -> str | None:
    tl = text.lower()
    markers = {
        "system": ["system:", "you are", "as an ai", "your role"],
        "user": ["user:", "human:", "question:"],
        "assistant": ["assistant:", "ai:"],
    }
    for role, tokens in markers.items():
        if any(t in tl for t in tokens):
            return role
    return None


def _is_prompt_literal(text: str, context: str) -> bool:
    tl = text.lower()
    ctx = context.lower()
    # Tier 1 — explicit role markers in content (high confidence, no context needed)
    if any(m in tl for m in ["system:", "user:", "assistant:", "you are a ", "your task is"]):
        return True
    # Tier 2 — prompt-building context + template variables + length
    prompt_ctx = any(h in ctx for h in ["prompt", "system", "template"])
    non_prompt_ctx = any(
        h in ctx
        for h in [
            "description",
            "summary",
            "readme",
            "license",
            "doc",
            "log",
            "error",
        ]
    )
    if non_prompt_ctx:
        return False
    template_vars = _TEMPLATE_VAR_RE.findall(text)
    return prompt_ctx and bool(template_vars) and len(text) > 120


def _build_symbol_table(parse_result: Any, source: str) -> dict[str, list[str]]:
    """Build a mapping of variable-name → list of string values from module-level constants.

    Used to resolve path-constant references in FAISS calls and tool lists.
    For a module-level assignment like ``DOCS_INDEX_PATH = "data/docs_index"``,
    this returns ``{"DOCS_INDEX_PATH": ["data/docs_index"]}``.

    Lists of variable references (e.g. ``tools = [search, fetch]``) are stored
    as a list of the referenced variable names.
    """
    table: dict[str, list[str]] = {}
    for lit in parse_result.string_literals:
        ctx = lit.context or ""
        if ctx and not lit.is_docstring and lit.value:
            existing = table.get(ctx)
            if existing is None:
                table[ctx] = [lit.value]
            else:
                existing.append(lit.value)
    return table


def _resolve_faiss_store_name(call: Any, symbol_table: dict[str, list[str]], source: str) -> str:
    """Derive a human-readable datastore name for a FAISS call.

    Resolution order (most specific first):
    1. The first positional argument when it is a known path-constant name
       (e.g. ``DOCS_INDEX_PATH`` → ``docs_index``).  Preferred because the
       constant name encodes the store identity precisely.
    2. A string literal path (e.g. ``"data/docs_index"`` → ``docs_index``).
    3. The variable the call is assigned to, cleaned of generic suffixes
       (e.g. ``docs_retriever`` → ``docs``).  Used only when no path/constant
       is available, so generic names like ``chat_vectorstore → chat`` are not
       promoted over a proper constant resolution.
    """
    # 1 & 2 — Path constant or string literal (highest specificity)
    if call.positional_args:
        first = call.positional_args[0]
        if isinstance(first, str):
            if first.startswith("$"):
                # Variable reference — look up in symbol table or derive from name
                const_name = first[1:]
                # The constant name itself often encodes the store identity
                m = _INDEX_PATH_CONST_RE.search(const_name)
                if m:
                    raw = m.group(1).lower()
                    raw = _PATH_CONST_CLEANUP_RE.sub("", raw).strip("_")
                    if raw:
                        return raw
                # Fall back to symbol table value (path string)
                vals = symbol_table.get(const_name, [])
                if vals:
                    first = vals[0]  # fall through to path-string branch
                elif const_name in _PYTHON_BUILTIN_NAMES:
                    # e.g. str(MY_CONST) — function-wrapper call, not a real name
                    pass  # fall through to assigned-variable resolution
                else:
                    return const_name.lower()
            # String path literal (or symbol table value after dereferencing)
            if isinstance(first, str) and not first.startswith("$"):
                path_parts = re.split(r"[/\\]", first.strip("'\"`"))
                for part in reversed(path_parts):
                    cleaned = re.sub(r"[_ ]*(path|folder|dir)$", "", part.lower()).strip("_")
                    if cleaned:
                        return cleaned

    # 3. Assigned variable name (fallback only — may be generic)
    if call.assigned_to:
        var = call.assigned_to
        cleaned = re.sub(
            r"[_ ]*(retriever|store|vectorstore|vector_store|db|index)$", "", var.lower()
        ).strip("_")
        if cleaned:
            return cleaned if not cleaned.endswith("_path") else cleaned[:-5].rstrip("_") + "_index"

    return ""
