"""LangGraph framework adapter.

Extracts AI assets from LangGraph-based applications:
- ``StateGraph`` / ``MessageGraph`` instantiation → AGENT nodes
- ``.add_node()`` calls → AGENT nodes
- ``.add_edge()`` / ``.add_conditional_edges()`` → graph AGENT-INVOKES-AGENT hints
- ``ToolNode`` → TOOL nodes (with underlying tool resolution)
- ``ChatOpenAI``, ``ChatAnthropic``, etc. → MODEL nodes (via models_kb)
- ``create_react_agent`` / ``create_supervisor`` / factory functions → AGENT nodes
- ``@tool`` decorated functions → TOOL nodes
- ``StructuredTool(...)`` / ``StructuredTool.from_function(...)`` / ``Tool.from_function(...)``
  (including inside factory functions that build and return a tool programmatically) → TOOL nodes
- ``FAISS.load_local`` / ``FAISS.from_texts`` / ``FAISS.save_local`` → DATASTORE nodes
- ``SystemMessage`` / chat prompt templates / large string literals → PROMPT nodes
"""

from __future__ import annotations

import ast
import re
from typing import Any

from ...normalization import canonicalize_text, humanize_context_name
from ...types import ComponentType
from .._test_paths import looks_like_test_path
from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint
from ..models_kb import (
    LANGCHAIN_LLM_CLASS_PROVIDERS,
    get_model_details,
)

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
    "deepagents",
    "managed_deepagents",
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

# langgraph.types.interrupt() — human-in-the-loop pause inside a graph node,
# treated as a guardrail-equivalent control. Relies on the generic file-level
# GUARDRAIL->AGENT fallback for wiring (a node function isn't itself an AGENT
# canonical name in this adapter's model, so no explicit hint is resolvable).
_INTERRUPT_FUNC_NAME = "interrupt"

_SUBAGENT_MAPPING_TYPES = {"SubAgent", "CompiledSubAgent", "AsyncSubAgent"}
_SUBAGENT_MODULES = {"deepagents", "managed_deepagents"}
_GENERIC_CONTAINER_KINDS = {
    "list": "sequence",
    "List": "sequence",
    "Sequence": "sequence",
    "tuple": "tuple",
    "Tuple": "tuple",
    "dict": "mapping",
    "Dict": "mapping",
    "Mapping": "mapping",
}
_GENERIC_MODULE_MEMBERS = {
    "typing": {
        name: _GENERIC_CONTAINER_KINDS[name]
        for name in ("List", "Sequence", "Tuple", "Dict", "Mapping")
    },
    "collections.abc": {
        name: _GENERIC_CONTAINER_KINDS[name] for name in ("Sequence", "Mapping")
    },
}


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
        imported_modules = _absolute_imported_modules(content)
        has_langgraph = any(
            m == "langgraph" or m.startswith("langgraph.") for m in imported_modules
        )
        has_langchain = any(
            m == "langchain"
            or m.startswith("langchain.")
            or m.startswith("langchain_")
            for m in imported_modules
        )
        has_legacy_framework = has_langgraph or has_langchain
        legacy_function_calls = (
            parse_result.function_calls if has_legacy_framework else ()
        )
        legacy_instantiations = (
            parse_result.instantiations if has_legacy_framework else ()
        )
        legacy_string_literals = (
            parse_result.string_literals if has_legacy_framework else ()
        )
        # Emit the correct framework node
        detected: list[ComponentDetection] = []
        if has_langgraph:
            detected.append(self._framework_node(file_path))
        elif has_langchain:
            # Only langchain imported — emit framework:langchain, not framework:langgraph
            from ...types import ComponentType as _CT

            detected.append(
                ComponentDetection(
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
            )

        # Track whether langgraph-specific patterns are found during scanning.
        # Used at the end to emit framework:langgraph even when no direct
        # `import langgraph` is present (e.g. code uses add_node / StateGraph
        # but the orchestrator only imports langchain_* packages).
        _langgraph_pattern_count = 0
        _typed_subagent_count = 0

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
        for call in legacy_function_calls:
            if call.function_name in _AGENT_FACTORY_FUNCTIONS and call.assigned_to:
                agent_factory_vars.add(call.assigned_to)

        # 0. @tool-decorated functions → TOOL nodes
        for call in legacy_function_calls:
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
        for call in legacy_function_calls:
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
            _langgraph_pattern_count += 1

        # 2b. StateGraph / MessageGraph instantiation → confirms langgraph usage
        for inst in legacy_instantiations:
            if inst.class_name in _STATEGRAPH_CLASSES:
                _langgraph_pattern_count += 1

        # 3. .add_edge() / .add_conditional_edges() → RelationshipHints
        for call in legacy_function_calls:
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
                    _langgraph_pattern_count += 1
            elif call.function_name == "add_conditional_edges":
                _langgraph_pattern_count += 1

        # 4. ToolNode instantiations → TOOL
        # Prefer emitting the concrete tools passed into ToolNode over a generic wrapper.
        for inst in legacy_instantiations:
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
                    for i in legacy_instantiations
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
                _langgraph_pattern_count += 1

        # 4b. Tool-class constructors imported from *.tools.* modules → TOOL
        # e.g. `from langchain_community.tools import TavilySearchResults`
        # then  `tavily_tool = TavilySearchResults(max_results=5)` → TOOL tavily_tool
        # (tool_classes_from_imports was pre-computed above; reuse here)
        for inst in legacy_instantiations:
            if inst.class_name not in tool_classes_from_imports:
                continue
            if inst.class_name == "StructuredTool":
                # Handled by section 4c below, which extracts the actual
                # name= kwarg instead of falling back to the assigned
                # variable name — skip here to avoid a duplicate TOOL node
                # for the same instantiation.
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

        # 4c. Dynamically-constructed StructuredTool/Tool.from_function → TOOL
        # e.g. a factory function that builds and returns a tool programmatically:
        #   def create_live_api_tool(name, description, ...):
        #       return StructuredTool.from_function(
        #           func=..., name=name, description=description,
        #       )
        # Neither the bare `StructuredTool(...)` constructor nor the
        # `.from_function(...)` factory method were previously matched by any
        # tool-detection heuristic — only `@tool`-decorated functions and
        # static tool-class instantiations (section 4b) were.
        for inst in legacy_instantiations:
            if inst.class_name != "StructuredTool":
                continue
            tool_name, is_dynamic = _resolve_dynamic_tool_name(
                inst.args.get("name"), inst.context, inst.assigned_to, inst.line
            )
            canon = canonicalize_text(f"langchain:tool:{tool_name}")
            meta: dict[str, Any] = {"tool_type": "structured_tool", "framework": "langchain"}
            if is_dynamic:
                meta["dynamic_name"] = True
            det = ComponentDetection(
                component_type=ComponentType.TOOL,
                canonical_name=canon,
                display_name=tool_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.75 if is_dynamic else 0.88,
                metadata=meta,
                file_path=file_path,
                line=inst.line,
                snippet="StructuredTool(...)",
                evidence_kind="ast_instantiation",
            )
            detected.append(det)
            tool_canonicals.append(canon)

        for call in legacy_function_calls:
            if call.function_name != "from_function" or call.receiver not in (
                "StructuredTool",
                "Tool",
            ):
                continue
            tool_name, is_dynamic = _resolve_dynamic_tool_name(
                call.args.get("name"), call.context, call.assigned_to, call.line
            )
            canon = canonicalize_text(f"langchain:tool:{tool_name}")
            meta = {
                "tool_type": "structured_tool",
                "framework": "langchain",
                "tool_class": call.receiver,
            }
            if is_dynamic:
                meta["dynamic_name"] = True
            det = ComponentDetection(
                component_type=ComponentType.TOOL,
                canonical_name=canon,
                display_name=tool_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.75 if is_dynamic else 0.88,
                metadata=meta,
                file_path=file_path,
                line=call.line,
                snippet=f"{call.receiver}.from_function(...)",
                evidence_kind="ast_call",
            )
            detected.append(det)
            tool_canonicals.append(canon)

        # 5. LangChain LLM wrapper instantiations → MODEL
        for inst in legacy_instantiations:
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

        # 5b. Deep Agents typed literal mappings → AGENT
        # Keep this AST path local to explicit SubAgent-family annotations. The
        # generic parser deliberately continues treating arbitrary dicts as
        # opaque, and dynamic builders/loops are not inferred here.
        if not looks_like_test_path(file_path):
            known_models = set(model_canonicals)
            known_tools = set(tool_canonicals)
            for subagent in _extract_typed_subagent_mappings(content):
                name, annotation, line, model_name, tool_names = subagent
                canon = canonicalize_text(f"langgraph:{name}")
                relationships: list[RelationshipHint] = []
                if model_name:
                    model_canon = canonicalize_text(model_name.lower())
                    if model_canon in known_models:
                        relationships.append(
                            RelationshipHint(
                                source_canonical=canon,
                                source_type=ComponentType.AGENT,
                                target_canonical=model_canon,
                                target_type=ComponentType.MODEL,
                                relationship_type="USES",
                            )
                        )
                for tool_name in tool_names:
                    tool_canon = canonicalize_text(f"langchain:tool:{tool_name}")
                    if tool_canon not in known_tools:
                        continue
                    relationships.append(
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
                        display_name=name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.95,
                        metadata={
                            "framework": "deepagents",
                            "subagent_type": annotation,
                            "is_agent_graph": True,
                        },
                        file_path=file_path,
                        line=line,
                        snippet=f'{annotation} mapping name="{name}"',
                        evidence_kind="ast_literal_mapping",
                        relationships=relationships,
                    )
                )
                _typed_subagent_count += 1

        # 6. Agent factory functions (create_react_agent, etc.)
        for call in legacy_function_calls:
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
            _langgraph_pattern_count += 1

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
        for call in legacy_function_calls:
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
        for inst in legacy_instantiations:
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
            prompt_rels: list[RelationshipHint] = [
                RelationshipHint(
                    source_canonical=agent_canon,
                    source_type=ComponentType.AGENT,
                    target_canonical=canon,
                    target_type=ComponentType.PROMPT,
                    relationship_type="USES",
                )
                for agent_canon in agent_canonicals
            ]
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
                    relationships=prompt_rels,
                )
            )

        # 7b. ChatPromptTemplate.from_messages() / from_template() calls → PROMPT (assignment-aware)
        for call in legacy_function_calls:
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
            prompt_rels = [
                RelationshipHint(
                    source_canonical=agent_canon,
                    source_type=ComponentType.AGENT,
                    target_canonical=canon,
                    target_type=ComponentType.PROMPT,
                    relationship_type="USES",
                )
                for agent_canon in agent_canonicals
            ]
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
                    relationships=prompt_rels,
                )
            )

        # 8. FAISS vector-store operations → DATASTORE (with identity from assignment or path constant)
        faiss_imported = any(
            ("faiss" in imp.module.lower() or imp.module == "langchain_community.vectorstores")
            for imp in parse_result.imports
        ) or any("faiss" in name.lower() for imp in parse_result.imports for name in imp.names)
        for call in legacy_function_calls:
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
        if faiss_imported and has_legacy_framework:
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

        # 9. langgraph.types.interrupt() calls → GUARDRAIL (human-in-the-loop)
        interrupt_imported = any(
            "interrupt" in (imp.names or []) and (imp.module or "").startswith("langgraph")
            for imp in parse_result.imports
        )
        if has_legacy_framework and (interrupt_imported or has_langgraph):
            for call in legacy_function_calls:
                if call.function_name != _INTERRUPT_FUNC_NAME or call.receiver is not None:
                    continue
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.GUARDRAIL,
                        canonical_name=canonicalize_text(f"langgraph:guardrail:interrupt:{file_path}:{call.line}"),
                        display_name="LangGraph Human-in-the-Loop",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.75,
                        metadata={
                            "framework": "langgraph",
                            "guardrail_type": "human_in_the_loop",
                            "detection_kind": "framework_native",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet="interrupt(...)",
                        evidence_kind="ast_call",
                    )
                )

        # Large string literals that look like prompts
        for lit in legacy_string_literals:
            if lit.is_docstring or len(lit.value) < 200:
                continue
            if not _is_prompt_literal(lit.value, lit.context or ""):
                continue
            template_vars = _TEMPLATE_VAR_RE.findall(lit.value)
            dname = _prompt_display_name(lit.value, lit.context or "", lit.line)
            canon = canonicalize_text(dname.lower())
            prompt_rels = [
                RelationshipHint(
                    source_canonical=agent_canon,
                    source_type=ComponentType.AGENT,
                    target_canonical=canon,
                    target_type=ComponentType.PROMPT,
                    relationship_type="USES",
                )
                for agent_canon in agent_canonicals
            ]
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
                    relationships=prompt_rels,
                )
            )

        # If langgraph-specific patterns were found (add_node, StateGraph,
        # ToolNode, create_react_agent, add_edge) but no direct `import langgraph`
        # was seen, also emit a framework:langgraph node at AST-backed confidence.
        # This handles orchestrators that import only from langchain_* packages
        # but rely on langgraph for graph wiring.
        if (
            _langgraph_pattern_count > 0 or _typed_subagent_count > 0
        ) and not has_langgraph:
            from ...types import ComponentType as _CT2

            detected.append(
                ComponentDetection(
                    component_type=_CT2.FRAMEWORK,
                    canonical_name="langgraph",
                    display_name="langgraph",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={"framework": "langgraph"},
                    file_path=file_path,
                    line=0,
                    snippet=(
                        "langgraph patterns detected "
                        f"({_langgraph_pattern_count + _typed_subagent_count})"
                    ),
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


def _resolve_dynamic_tool_name(
    raw_name: Any, context: str | None, assigned_to: str | None, line: int
) -> tuple[str, bool]:
    """Return ``(tool_name, dynamic_name)`` for a StructuredTool/Tool.from_function call.

    Prefers a resolvable string-literal ``name=`` kwarg. When the name is only
    known at runtime (built from a variable/expression — e.g. the ``name``
    kwarg is itself a parameter of the enclosing factory function), falls back
    to the enclosing factory function's name, then the assigned variable, then
    a line-based placeholder — flagging ``dynamic_name=True`` so downstream
    consumers know the identity is approximate rather than sourced directly
    from the code, rather than silently skipping the tool entirely.
    """
    cleaned = _clean(raw_name)
    if cleaned:
        return cleaned, False
    if context:
        return context, True
    if assigned_to:
        return assigned_to, True
    return f"structured_tool_{line}", True


def _clean(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip("'\"` ")
    if s.startswith("$") or s in {"<complex>", "<lambda>", "<dict>", "<list>"}:
        return ""
    return s


def _trusted_runtime_condition(
    node: ast.AST,
    type_checking_symbols: set[str],
    typing_modules: set[str],
) -> bool | None:
    """Evaluate only boolean conditions whose runtime value is proven."""
    if isinstance(node, ast.Constant):
        # Literal constants have a stable truth value.  Restrict this to AST
        # constants rather than calling ``bool`` on arbitrary expressions.
        return bool(node.value)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
        # Empty displays are always false.  A display with an explicit item is
        # non-empty if evaluation finishes; unpack-only displays may still be
        # empty and therefore remain unknown.
        if isinstance(node, ast.Dict):
            if not node.keys:
                return False
            return True if any(key is not None for key in node.keys) else None
        if not node.elts:
            return False
        return True if any(not isinstance(item, ast.Starred) for item in node.elts) else None
    if isinstance(node, ast.Name) and node.id in type_checking_symbols:
        return False
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in typing_modules
        and node.attr == "TYPE_CHECKING"
    ):
        return False
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _trusted_runtime_condition(
            node.operand, type_checking_symbols, typing_modules
        )
        return None if value is None else not value
    if isinstance(node, ast.BoolOp):
        values = [
            _trusted_runtime_condition(value, type_checking_symbols, typing_modules)
            for value in node.values
        ]
        if isinstance(node.op, ast.And):
            if False in values:
                return False
            return True if all(value is True for value in values) else None
        if isinstance(node.op, ast.Or):
            if True in values:
                return True
            return False if all(value is False for value in values) else None
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and len(node.comparators) == 1
        and isinstance(node.ops[0], (ast.Is, ast.IsNot, ast.Eq, ast.NotEq))
    ):
        def comparison_value(part: ast.AST) -> tuple[bool, Any]:
            try:
                return True, ast.literal_eval(part)
            except (ValueError, TypeError, SyntaxError):
                pass
            if isinstance(part, ast.Name) and part.id in type_checking_symbols:
                return True, False
            if (
                isinstance(part, ast.Attribute)
                and isinstance(part.value, ast.Name)
                and part.value.id in typing_modules
                and part.attr == "TYPE_CHECKING"
            ):
                return True, False
            if isinstance(part, (ast.UnaryOp, ast.Compare)):
                value = _trusted_runtime_condition(
                    part, type_checking_symbols, typing_modules
                )
                return value is not None, value
            return False, None

        left_known, left = comparison_value(node.left)
        right_known, right = comparison_value(node.comparators[0])
        if not left_known or not right_known:
            return None
        equal = (
            left is right
            if isinstance(node.ops[0], (ast.Is, ast.IsNot))
            else left == right
        )
        return not equal if isinstance(node.ops[0], (ast.IsNot, ast.NotEq)) else equal
    return None


def _proven_empty_iterable(node: ast.AST) -> bool:
    """True only for literal iterable displays proven to contain no items."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (str, bytes))
        and not node.value
    )


def _absolute_imported_modules(source: str) -> set[str]:
    """Return modules from absolute imports on potentially live paths."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    modules: set[str] = set()

    State = tuple[set[str], set[str], set[str]]

    def copied(state: State) -> State:
        return set(state[0]), set(state[1]), set(state[2])

    def merged(states: list[State]) -> State:
        if not states:
            return set(), set(), set()
        type_symbols = set.intersection(*(state[0] for state in states))
        typing_names = set.intersection(*(state[1] for state in states))
        builtin_names = set.intersection(*(state[2] for state in states))
        return type_symbols, typing_names, builtin_names

    def bound_names(nodes: list[ast.AST]) -> set[str]:
        names: set[str] = set()
        stack = list(reversed(nodes))
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                continue
            if isinstance(node, ast.Lambda):
                continue
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                # Comprehension targets are local to the comprehension.  A
                # walrus target is represented outside that target scope and
                # is found by walking the value/filters below.
                parts: list[ast.AST] = []
                if isinstance(node, ast.DictComp):
                    parts.extend((node.key, node.value))
                else:
                    parts.append(node.elt)
                for generator in node.generators:
                    parts.append(generator.iter)
                    parts.extend(generator.ifs)
                stack.extend(reversed(parts))
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update(
                    alias.asname or alias.name.split(".", 1)[0]
                    for alias in node.names
                    if alias.name != "*"
                )
                continue
            if isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
            if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                names.add(node.name)
            if isinstance(node, ast.MatchMapping) and node.rest:
                names.add(node.rest)
            stack.extend(reversed(list(ast.iter_child_nodes(node))))
        return names

    def clear_effects(nodes: list[ast.AST], state: State) -> None:
        for name in bound_names(nodes):
            state[0].discard(name)
            state[1].discard(name)
            state[2].discard(name)
        mutated_namespaces: set[str] = set()
        for root in nodes:
            for node in ast.walk(root):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.ctx, (ast.Store, ast.Del))
                    and node.attr == "TYPE_CHECKING"
                    and isinstance(node.value, ast.Name)
                ):
                    mutated_namespaces.add(node.value.id)
                elif (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.ctx, (ast.Store, ast.Del))
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "__dict__"
                    and isinstance(node.value.value, ast.Name)
                    and isinstance(node.slice, ast.Constant)
                    and node.slice.value == "TYPE_CHECKING"
                ):
                    mutated_namespaces.add(node.value.value.id)
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in state[2]
                    and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Name)
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == "TYPE_CHECKING"
                ):
                    mutated_namespaces.add(node.args[0].id)
        for name in mutated_namespaces:
            state[1].discard(name)

    def definition_headers(
        statement: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> list[ast.AST]:
        headers: list[ast.AST] = list(statement.decorator_list)
        headers.extend(getattr(statement, "type_params", ()))
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            headers.extend(statement.args.defaults)
            headers.extend(value for value in statement.args.kw_defaults if value is not None)
            headers.extend(
                arg.annotation
                for arg in (
                    *statement.args.posonlyargs,
                    *statement.args.args,
                    *statement.args.kwonlyargs,
                )
                if arg.annotation is not None
            )
            if statement.args.vararg and statement.args.vararg.annotation is not None:
                headers.append(statement.args.vararg.annotation)
            if statement.args.kwarg and statement.args.kwarg.annotation is not None:
                headers.append(statement.args.kwarg.annotation)
            if statement.returns is not None:
                headers.append(statement.returns)
        else:
            headers.extend(statement.bases)
            headers.extend(keyword.value for keyword in statement.keywords)
        return headers

    def function_locals(statement: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        args = statement.args
        names = {
            arg.arg
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        }
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)
        names.update(bound_names(list(statement.body)))
        globals_: set[str] = set()
        nonlocals: set[str] = set()
        stack: list[ast.AST] = list(reversed(statement.body))
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Global):
                globals_.update(node.names)
            elif isinstance(node, ast.Nonlocal):
                nonlocals.update(node.names)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            stack.extend(reversed(list(ast.iter_child_nodes(node))))
        return names - globals_ - nonlocals

    def apply_import(statement: ast.Import | ast.ImportFrom, state: State) -> None:
        if isinstance(statement, ast.Import):
            modules.update(alias.name for alias in statement.names)
            for alias in statement.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                state[0].discard(local)
                state[1].discard(local)
                state[2].discard(local)
                if alias.name == "typing":
                    state[1].add(local)
            return
        if statement.level == 0 and statement.module:
            modules.add(statement.module)
        for alias in statement.names:
            local = alias.asname or alias.name
            state[0].discard(local)
            state[1].discard(local)
            state[2].discard(local)
            if (
                statement.level == 0
                and statement.module == "typing"
                and alias.name == "TYPE_CHECKING"
            ):
                state[0].add(local)

    def scan(statements: list[ast.stmt], state: State) -> State:
        for statement in statements:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                apply_import(statement, state)
                continue
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                clear_effects(definition_headers(statement), state)
                function_state = copied(state)
                for name in function_locals(statement):
                    function_state[0].discard(name)
                    function_state[1].discard(name)
                    function_state[2].discard(name)
                scan(statement.body, function_state)
                state[0].discard(statement.name)
                state[1].discard(statement.name)
                state[2].discard(statement.name)
                continue
            if isinstance(statement, ast.ClassDef):
                clear_effects(definition_headers(statement), state)
                scan(statement.body, copied(state))
                state[0].discard(statement.name)
                state[1].discard(statement.name)
                state[2].discard(statement.name)
                continue
            if isinstance(statement, ast.If):
                runtime_value = _trusted_runtime_condition(
                    statement.test, state[0], state[1]
                )
                branch_state = copied(state)
                clear_effects([statement.test], branch_state)
                if runtime_value is True:
                    state = scan(statement.body, branch_state)
                elif runtime_value is False:
                    state = scan(statement.orelse, branch_state)
                else:
                    state = merged(
                        [
                            scan(statement.body, copied(branch_state)),
                            scan(statement.orelse, copied(branch_state)),
                        ]
                    )
                continue
            if isinstance(statement, ast.While):
                runtime_value = _trusted_runtime_condition(
                    statement.test, state[0], state[1]
                )
                loop_state = copied(state)
                clear_effects([statement.test], loop_state)
                if runtime_value is False:
                    state = scan(statement.orelse, loop_state)
                elif runtime_value is True:
                    state = merged([loop_state, scan(statement.body, copied(loop_state))])
                else:
                    body_state = scan(statement.body, copied(loop_state))
                    else_state = scan(statement.orelse, copied(loop_state))
                    state = merged([loop_state, body_state, else_state])
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                loop_state = copied(state)
                clear_effects([statement.iter, statement.target], loop_state)
                body_state = scan(statement.body, copied(loop_state))
                # The loop may execute zero times.  Its else suite follows any
                # normal completion, with or without an iteration.
                normal_state = merged([loop_state, body_state])
                else_state = scan(statement.orelse, copied(normal_state))
                state = merged([normal_state, else_state])
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith)):
                with_state = copied(state)
                header_nodes: list[ast.AST] = []
                for item in statement.items:
                    header_nodes.append(item.context_expr)
                    if item.optional_vars is not None:
                        header_nodes.append(item.optional_vars)
                clear_effects(header_nodes, with_state)
                state = scan(statement.body, with_state)
                continue
            if isinstance(statement, ast.Match):
                match_state = copied(state)
                clear_effects([statement.subject], match_state)
                case_states: list[State] = [copied(match_state)]
                for case in statement.cases:
                    case_state = copied(match_state)
                    header_nodes = [case.pattern]
                    if case.guard is not None:
                        header_nodes.append(case.guard)
                    clear_effects(header_nodes, case_state)
                    case_states.append(scan(case.body, case_state))
                state = merged(case_states)
                continue
            if isinstance(statement, (ast.Try, ast.TryStar)):
                entry = copied(state)
                body_state = scan(statement.body, copied(entry))
                normal_state = scan(statement.orelse, copied(body_state))
                handler_states: list[State] = []
                for handler in statement.handlers:
                    handler_state = copied(entry)
                    if handler.type is not None:
                        clear_effects([handler.type], handler_state)
                    if handler.name:
                        handler_state[0].discard(handler.name)
                        handler_state[1].discard(handler.name)
                    handler_states.append(scan(handler.body, handler_state))
                paths = [normal_state, *handler_states] if handler_states else [normal_state]
                before_finally = merged(paths)
                state = scan(statement.finalbody, before_finally)
                continue
            clear_effects([statement], state)
        return state

    scan(tree.body, (set(), set(), {"setattr", "delattr"}))
    return modules


def _annotation_provenance(
    tree: ast.Module, site: ast.AST, parents: dict[ast.AST, ast.AST]
) -> tuple[dict[str, str], set[str], dict[str, str], dict[str, str]]:
    """Replay trusted type/container bindings visible at one annotation site."""
    symbols: dict[str, str] = {}
    modules: set[str] = set()
    containers = {name: kind for name, kind in _GENERIC_CONTAINER_KINDS.items() if name.islower()}
    generic_modules: dict[str, str] = {}
    type_checking_symbols: set[str] = set()
    typing_modules: set[str] = set()
    builtin_mutators: set[str] = {"setattr", "delattr", "vars"}

    def definition_header_nodes(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> list[ast.AST]:
        """Expressions executed in the enclosing scope when a definition is made."""
        headers: list[ast.AST] = list(node.decorator_list)
        headers.extend(getattr(node, "type_params", ()))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            headers.extend(node.args.defaults)
            headers.extend(value for value in node.args.kw_defaults if value is not None)
            headers.extend(
                arg.annotation
                for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if arg.annotation is not None
            )
            if node.args.vararg and node.args.vararg.annotation is not None:
                headers.append(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation is not None:
                headers.append(node.args.kwarg.annotation)
            if node.returns is not None:
                headers.append(node.returns)
        else:
            headers.extend(node.bases)
            headers.extend(keyword.value for keyword in node.keywords)
        return headers

    def binding_effects(nodes: list[ast.AST]) -> tuple[set[str], set[str]]:
        names: set[str] = set()
        mutated_namespaces: set[str] = set()
        stack = list(reversed(nodes))
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and (
                    node.attr in _SUBAGENT_MAPPING_TYPES
                    or node.attr in _GENERIC_CONTAINER_KINDS
                    or node.attr == "TYPE_CHECKING"
                )
            ):
                base = node.value
                while isinstance(base, ast.Attribute):
                    base = base.value
                if isinstance(base, ast.Name):
                    mutated_namespaces.add(base.id)
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "__dict__"
                and isinstance(node.value.value, ast.Name)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value
                in {*_SUBAGENT_MAPPING_TYPES, *_GENERIC_CONTAINER_KINDS, "TYPE_CHECKING"}
            ):
                mutated_namespaces.add(node.value.value.id)
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "vars"
                and node.value.func.id in builtin_mutators
                and len(node.value.args) == 1
                and isinstance(node.value.args[0], ast.Name)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value
                in {*_SUBAGENT_MAPPING_TYPES, *_GENERIC_CONTAINER_KINDS, "TYPE_CHECKING"}
            ):
                mutated_namespaces.add(node.value.args[0].id)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"}
            ):
                namespace_name: str | None = None
                receiver = node.func.value
                if (
                    isinstance(receiver, ast.Attribute)
                    and receiver.attr == "__dict__"
                    and isinstance(receiver.value, ast.Name)
                ):
                    namespace_name = receiver.value.id
                elif (
                    isinstance(receiver, ast.Call)
                    and isinstance(receiver.func, ast.Name)
                    and receiver.func.id == "vars"
                    and receiver.func.id in builtin_mutators
                    and len(receiver.args) == 1
                    and isinstance(receiver.args[0], ast.Name)
                ):
                    namespace_name = receiver.args[0].id
                if namespace_name is not None:
                    mutated_namespaces.add(namespace_name)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in builtin_mutators
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value
                in {*_SUBAGENT_MAPPING_TYPES, *_GENERIC_CONTAINER_KINDS, "TYPE_CHECKING"}
            ):
                mutated_namespaces.add(node.args[0].id)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                stack.extend(reversed(definition_header_nodes(node)))
                continue
            if isinstance(
                node,
                (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
            ):
                # Generator targets are local to the comprehension. Expressions,
                # filters, and iterables still execute in the enclosing scope and
                # can contain walrus bindings or namespace mutation calls.
                parts: list[ast.AST] = []
                if isinstance(node, ast.DictComp):
                    parts.extend((node.key, node.value))
                else:
                    parts.append(node.elt)
                for generator in node.generators:
                    parts.append(generator.iter)
                    parts.extend(generator.ifs)
                stack.extend(reversed(parts))
                continue
            if isinstance(node, ast.Lambda):
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update(
                    alias.asname or alias.name.split(".", 1)[0]
                    for alias in node.names
                    if alias.name != "*"
                )
                continue
            if isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
            if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                names.add(node.name)
            if isinstance(node, ast.MatchMapping) and node.rest:
                names.add(node.rest)
            stack.extend(reversed(list(ast.iter_child_nodes(node))))
        return names, mutated_namespaces

    def clear_binding(name: str) -> None:
        symbols.pop(name, None)
        modules.discard(name)
        containers.pop(name, None)
        generic_modules.pop(name, None)
        type_checking_symbols.discard(name)
        typing_modules.discard(name)
        builtin_mutators.discard(name)

    ProvenanceState = tuple[
        dict[str, str],
        set[str],
        dict[str, str],
        dict[str, str],
        set[str],
        set[str],
        set[str],
    ]

    def capture_state() -> ProvenanceState:
        return (
            dict(symbols),
            set(modules),
            dict(containers),
            dict(generic_modules),
            set(type_checking_symbols),
            set(typing_modules),
            set(builtin_mutators),
        )

    def restore_state(state: ProvenanceState) -> None:
        symbols.clear()
        symbols.update(state[0])
        modules.clear()
        modules.update(state[1])
        containers.clear()
        containers.update(state[2])
        generic_modules.clear()
        generic_modules.update(state[3])
        type_checking_symbols.clear()
        type_checking_symbols.update(state[4])
        typing_modules.clear()
        typing_modules.update(state[5])
        builtin_mutators.clear()
        builtin_mutators.update(state[6])

    def join_states(states: list[ProvenanceState]) -> ProvenanceState:
        """Keep only provenance that is identical on every reachable path."""
        if not states:
            return {}, set(), {}, {}, set(), set(), set()

        def joined_mapping(values: list[dict[str, str]]) -> dict[str, str]:
            first = values[0]
            return {
                key: value
                for key, value in first.items()
                if all(mapping.get(key) == value for mapping in values[1:])
            }

        def joined_set(values: list[set[str]]) -> set[str]:
            return set.intersection(*values)

        return (
            joined_mapping([state[0] for state in states]),
            joined_set([state[1] for state in states]),
            joined_mapping([state[2] for state in states]),
            joined_mapping([state[3] for state in states]),
            joined_set([state[4] for state in states]),
            joined_set([state[5] for state in states]),
            joined_set([state[6] for state in states]),
        )

    def apply_import(statement: ast.Import | ast.ImportFrom) -> None:
        if isinstance(statement, ast.ImportFrom):
            module = statement.module or ""
            root = module.split(".", 1)[0]
            for alias in statement.names:
                local_name = alias.asname or alias.name
                clear_binding(local_name)
                if statement.level != 0:
                    continue
                if root in _SUBAGENT_MODULES and alias.name in _SUBAGENT_MAPPING_TYPES:
                    symbols[local_name] = alias.name
                elif module == "typing" and alias.name == "TYPE_CHECKING":
                    type_checking_symbols.add(local_name)
                elif module in {"typing", "collections.abc"}:
                    kind = _GENERIC_MODULE_MEMBERS[module].get(alias.name)
                    if kind is not None:
                        containers[local_name] = kind
                elif module == "collections" and alias.name == "abc":
                    generic_modules[local_name] = "collections.abc"
            return
        for alias in statement.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            clear_binding(local_name)
            root = alias.name.split(".", 1)[0]
            if root in _SUBAGENT_MODULES:
                modules.add(local_name)
            elif alias.name in {"typing", "collections", "collections.abc"}:
                module_name = alias.name
                if alias.name == "collections.abc" and not alias.asname:
                    module_name = "collections"
                generic_modules[local_name] = module_name
                if alias.name == "typing":
                    typing_modules.add(local_name)

    site_ancestors: set[ast.AST] = set()
    current = parents.get(site)
    while current is not None:
        site_ancestors.add(current)
        current = parents.get(current)

    def apply_effects(nodes: list[ast.AST]) -> None:
        names, mutated_namespaces = binding_effects(nodes)
        for name in names | mutated_namespaces:
            clear_binding(name)

    def run_block(
        statements: list[ast.stmt], initial: ProvenanceState
    ) -> tuple[ProvenanceState, bool]:
        restore_state(initial)
        falls_through = True
        for statement in statements:
            falls_through = apply_statement(statement)
            if not falls_through:
                break
        return capture_state(), falls_through

    def possible_prefix_state(
        statements: list[ast.stmt], initial: ProvenanceState
    ) -> ProvenanceState:
        """Join states after every prefix that could transfer to a handler."""
        states = [initial]
        restore_state(initial)
        for statement in statements:
            before = capture_state()
            falls_through = apply_statement(statement)
            after = capture_state()
            # A handler can be entered after expression effects have run but
            # before a statement completes.  Include both the normal result
            # and a conservative exceptional result carrying every write in
            # the statement (notably NamedExpr targets in ``raise``/branches).
            states.append(after)
            restore_state(before)
            # A failed import does not replace an existing local binding;
            # assignment occurs only after import resolution succeeds.
            if not isinstance(statement, (ast.Import, ast.ImportFrom)):
                apply_effects([statement])
            states.append(capture_state())
            restore_state(after)
            if not falls_through:
                break
        return join_states(states)

    def loop_body_paths(
        statements: list[ast.stmt], initial: ProvenanceState
    ) -> tuple[
        list[ProvenanceState],
        list[ProvenanceState],
        list[ProvenanceState],
        list[ProvenanceState],
        list[ProvenanceState],
    ]:
        """Return normal, break, continue, return, and raise exit states."""
        normal = [initial]
        breaks: list[ProvenanceState] = []
        continues: list[ProvenanceState] = []
        returns: list[ProvenanceState] = []
        raises: list[ProvenanceState] = []
        for statement in statements:
            next_normal: list[ProvenanceState] = []
            for state in normal:
                restore_state(state)
                if isinstance(statement, ast.Break):
                    breaks.append(capture_state())
                    continue
                if isinstance(statement, ast.Continue):
                    continues.append(capture_state())
                    continue
                if isinstance(statement, ast.Return):
                    apply_effects([statement])
                    returns.append(capture_state())
                    continue
                if isinstance(statement, ast.Raise):
                    apply_effects([statement])
                    raises.append(capture_state())
                    continue
                if isinstance(statement, ast.If):
                    runtime_value = _trusted_runtime_condition(
                        statement.test, type_checking_symbols, typing_modules
                    )
                    apply_effects([statement.test])
                    branch_entry = capture_state()
                    branches = (
                        [statement.body]
                        if runtime_value is True
                        else [statement.orelse]
                        if runtime_value is False
                        else [statement.body, statement.orelse]
                    )
                    for branch in branches:
                        (
                            branch_normal,
                            branch_breaks,
                            branch_continues,
                            branch_returns,
                            branch_raises,
                        ) = loop_body_paths(branch, branch_entry)
                        next_normal.extend(branch_normal)
                        breaks.extend(branch_breaks)
                        continues.extend(branch_continues)
                        returns.extend(branch_returns)
                        raises.extend(branch_raises)
                    continue
                if isinstance(statement, (ast.With, ast.AsyncWith)):
                    headers: list[ast.AST] = []
                    for item in statement.items:
                        headers.append(item.context_expr)
                        if item.optional_vars is not None:
                            headers.append(item.optional_vars)
                    apply_effects(headers)
                    body_flow = loop_body_paths(statement.body, capture_state())
                    next_normal.extend(body_flow[0])
                    breaks.extend(body_flow[1])
                    continues.extend(body_flow[2])
                    returns.extend(body_flow[3])
                    raises.extend(body_flow[4])
                    continue
                if isinstance(statement, ast.Match):
                    apply_effects([statement.subject])
                    match_entry = capture_state()
                    exhaustive = False
                    for case in statement.cases:
                        restore_state(match_entry)
                        guard_value = case_guard_value(case)
                        apply_effects(
                            [case.pattern, *([case.guard] if case.guard is not None else [])]
                        )
                        if guard_value is not False:
                            case_flow = loop_body_paths(case.body, capture_state())
                            next_normal.extend(case_flow[0])
                            breaks.extend(case_flow[1])
                            continues.extend(case_flow[2])
                            returns.extend(case_flow[3])
                            raises.extend(case_flow[4])
                        if irrefutable_pattern(case.pattern) and guard_value is True:
                            exhaustive = True
                            break
                    if not exhaustive:
                        next_normal.append(match_entry)
                    continue
                if isinstance(statement, (ast.Try, ast.TryStar)):
                    try_entry = capture_state()
                    body_flow = loop_body_paths(statement.body, try_entry)
                    pre_final: list[tuple[str, ProvenanceState]] = []
                    for body_normal in body_flow[0]:
                        else_flow = loop_body_paths(statement.orelse, body_normal)
                        pre_final.extend(("normal", item) for item in else_flow[0])
                        pre_final.extend(("break", item) for item in else_flow[1])
                        pre_final.extend(("continue", item) for item in else_flow[2])
                        pre_final.extend(("return", item) for item in else_flow[3])
                        pre_final.extend(("raise", item) for item in else_flow[4])
                    pre_final.extend(("break", item) for item in body_flow[1])
                    pre_final.extend(("continue", item) for item in body_flow[2])
                    pre_final.extend(("return", item) for item in body_flow[3])
                    pre_final.extend(("raise", item) for item in body_flow[4])

                    handler_entry = possible_prefix_state(statement.body, try_entry)
                    # Any statement may raise before completing; an unmatched
                    # exception still runs ``finally`` with this joined prefix.
                    pre_final.append(("raise", handler_entry))
                    for handler in statement.handlers:
                        restore_state(handler_entry)
                        if handler.type is not None:
                            apply_effects([handler.type])
                        if handler.name:
                            clear_binding(handler.name)
                        handler_flow = loop_body_paths(handler.body, capture_state())
                        pre_final.extend(("normal", item) for item in handler_flow[0])
                        pre_final.extend(("break", item) for item in handler_flow[1])
                        pre_final.extend(("continue", item) for item in handler_flow[2])
                        pre_final.extend(("return", item) for item in handler_flow[3])
                        pre_final.extend(("raise", item) for item in handler_flow[4])

                    for kind, predecessor in pre_final:
                        final_flow = loop_body_paths(statement.finalbody, predecessor)
                        for final_normal in final_flow[0]:
                            if kind == "normal":
                                next_normal.append(final_normal)
                            elif kind == "break":
                                breaks.append(final_normal)
                            elif kind == "continue":
                                continues.append(final_normal)
                            elif kind == "return":
                                returns.append(final_normal)
                            else:
                                raises.append(final_normal)
                        breaks.extend(final_flow[1])
                        continues.extend(final_flow[2])
                        returns.extend(final_flow[3])
                        raises.extend(final_flow[4])
                    continue
                if apply_statement(statement):
                    next_normal.append(capture_state())
            normal = next_normal
            if not normal:
                break
        return normal, breaks, continues, returns, raises

    def irrefutable_pattern(pattern: ast.pattern) -> bool:
        if isinstance(pattern, ast.MatchAs):
            return pattern.pattern is None or irrefutable_pattern(pattern.pattern)
        if isinstance(pattern, ast.MatchOr):
            return any(irrefutable_pattern(item) for item in pattern.patterns)
        return False

    def case_guard_value(case: ast.match_case) -> bool | None:
        if case.guard is None:
            return True
        return _trusted_runtime_condition(
            case.guard, type_checking_symbols, typing_modules
        )

    def apply_statement(statement: ast.stmt) -> bool:
        """Apply one statement and report whether its suite can fall through."""
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            apply_import(statement)
            return True
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            apply_effects(definition_header_nodes(statement))
            clear_binding(statement.name)
            return True
        if isinstance(statement, ast.If):
            runtime_value = _trusted_runtime_condition(
                statement.test, type_checking_symbols, typing_modules
            )
            apply_effects([statement.test])
            entry = capture_state()
            if runtime_value is True:
                state, falls_through = run_block(statement.body, entry)
                restore_state(state)
                return falls_through
            if runtime_value is False:
                state, falls_through = run_block(statement.orelse, entry)
                restore_state(state)
                return falls_through
            branch_exits: list[ProvenanceState] = []
            body_state, body_falls = run_block(statement.body, entry)
            if body_falls:
                branch_exits.append(body_state)
            else_state, else_falls = run_block(statement.orelse, entry)
            if else_falls:
                branch_exits.append(else_state)
            restore_state(join_states(branch_exits))
            return bool(branch_exits)
        if isinstance(statement, ast.While):
            runtime_value = _trusted_runtime_condition(
                statement.test, type_checking_symbols, typing_modules
            )
            apply_effects([statement.test])
            entry = capture_state()
            if runtime_value is False:
                state, falls_through = run_block(statement.orelse, entry)
                restore_state(state)
                return falls_through
            (
                body_normal,
                body_breaks,
                body_continues,
                _body_returns,
                _body_raises,
            ) = loop_body_paths(statement.body, entry)
            normal_inputs = [entry]
            normal_inputs.extend(body_normal)
            normal_inputs.extend(body_continues)
            normal_input = join_states(normal_inputs)
            else_state, else_falls = run_block(statement.orelse, normal_input)
            loop_exits: list[ProvenanceState] = []
            if runtime_value is not True and else_falls:
                loop_exits.append(else_state)
            loop_exits.extend(body_breaks)
            restore_state(join_states(loop_exits))
            return bool(loop_exits)
        if isinstance(statement, (ast.For, ast.AsyncFor)):
            if _proven_empty_iterable(statement.iter):
                apply_effects([statement.iter])
                state, falls_through = run_block(
                    statement.orelse, capture_state()
                )
                restore_state(state)
                return falls_through
            apply_effects([statement.iter, statement.target])
            entry = capture_state()
            (
                body_normal,
                body_breaks,
                body_continues,
                _body_returns,
                _body_raises,
            ) = loop_body_paths(statement.body, entry)
            normal_inputs = [entry]
            normal_inputs.extend(body_normal)
            normal_inputs.extend(body_continues)
            normal_input = join_states(normal_inputs)
            else_state, else_falls = run_block(statement.orelse, normal_input)
            for_exits = [else_state] if else_falls else []
            for_exits.extend(body_breaks)
            restore_state(join_states(for_exits))
            return bool(for_exits)
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            with_headers: list[ast.AST] = []
            for item in statement.items:
                with_headers.append(item.context_expr)
                if item.optional_vars is not None:
                    with_headers.append(item.optional_vars)
            apply_effects(with_headers)
            state, falls_through = run_block(statement.body, capture_state())
            restore_state(state)
            return falls_through
        if isinstance(statement, ast.Match):
            apply_effects([statement.subject])
            entry = capture_state()
            match_exits: list[ProvenanceState] = []
            exhaustive = False
            for case in statement.cases:
                restore_state(entry)
                guard_value = case_guard_value(case)
                apply_effects(
                    [case.pattern, *([case.guard] if case.guard is not None else [])]
                )
                if guard_value is not False:
                    case_state, case_falls = run_block(case.body, capture_state())
                    if case_falls:
                        match_exits.append(case_state)
                if irrefutable_pattern(case.pattern) and guard_value is True:
                    exhaustive = True
                    break
            if not exhaustive:
                match_exits.append(entry)
            restore_state(join_states(match_exits))
            return bool(match_exits)
        if isinstance(statement, (ast.Try, ast.TryStar)):
            entry = capture_state()
            body_state, body_falls = run_block(statement.body, entry)
            try_exits: list[ProvenanceState] = []
            if body_falls:
                normal_state, normal_falls = run_block(statement.orelse, body_state)
                if normal_falls:
                    try_exits.append(normal_state)
            handler_entry = possible_prefix_state(statement.body, entry)
            for handler in statement.handlers:
                restore_state(handler_entry)
                handler_headers: list[ast.AST] = []
                if handler.type is not None:
                    handler_headers.append(handler.type)
                apply_effects(handler_headers)
                if handler.name:
                    clear_binding(handler.name)
                handler_state, handler_falls = run_block(
                    handler.body, capture_state()
                )
                if handler_falls:
                    try_exits.append(handler_state)
            if not try_exits:
                restore_state(join_states([]))
                return False
            joined = join_states(try_exits)
            final_state, final_falls = run_block(statement.finalbody, joined)
            restore_state(final_state)
            return final_falls
        apply_effects([statement])
        return not isinstance(
            statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)
        )

    def apply_path_header(node: ast.AST) -> None:
        """Apply bindings evaluated before a nested statement body."""
        header_nodes: list[ast.AST] = []
        if isinstance(node, (ast.If, ast.While)):
            header_nodes.append(node.test)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            header_nodes.extend((node.iter, node.target))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                header_nodes.append(item.context_expr)
                if item.optional_vars is not None:
                    header_nodes.append(item.optional_vars)
        elif isinstance(node, ast.Match):
            header_nodes.append(node.subject)
        elif isinstance(node, ast.ExceptHandler):
            if node.type is not None:
                header_nodes.append(node.type)
            if node.name:
                clear_binding(node.name)
        elif isinstance(node, ast.match_case):
            header_nodes.append(node.pattern)
            if node.guard is not None:
                header_nodes.append(node.guard)
        if header_nodes:
            apply_effects(header_nodes)

    site_is_nonruntime = False

    def mark_nonruntime_branch(node: ast.If | ast.While) -> None:
        nonlocal site_is_nonruntime
        runtime_value = _trusted_runtime_condition(
            node.test, type_checking_symbols, typing_modules
        )
        if runtime_value is None:
            return
        in_body = any(item is site or item in site_ancestors for item in node.body)
        in_orelse = any(item is site or item in site_ancestors for item in node.orelse)
        if (in_body and not runtime_value) or (in_orelse and runtime_value):
            site_is_nonruntime = True

    def replay_path(node: ast.AST) -> None:
        """Replay only the statement path leading to the annotation site."""
        nonlocal site_is_nonruntime
        if isinstance(node, (ast.If, ast.While)):
            mark_nonruntime_branch(node)
            if site_is_nonruntime:
                return
        if (
            isinstance(node, (ast.For, ast.AsyncFor))
            and _proven_empty_iterable(node.iter)
            and any(item is site or item in site_ancestors for item in node.body)
        ):
            site_is_nonruntime = True
            return
        if isinstance(node, ast.Match):
            apply_effects([node.subject])
            target_index = next(
                (
                    index
                    for index, case in enumerate(node.cases)
                    if case is site or case in site_ancestors
                ),
                None,
            )
            if target_index is None:
                return
            for case in node.cases[:target_index]:
                guard_value = case_guard_value(case)
                apply_effects(
                    [case.pattern, *([case.guard] if case.guard is not None else [])]
                )
                if irrefutable_pattern(case.pattern) and guard_value is True:
                    site_is_nonruntime = True
                    return
            target = node.cases[target_index]
            if case_guard_value(target) is False:
                site_is_nonruntime = True
                return
            replay_path(target)
            return
        if isinstance(node, (ast.Try, ast.TryStar)):
            target_handler = next(
                (
                    handler
                    for handler in node.handlers
                    if handler is site or handler in site_ancestors
                ),
                None,
            )
            if target_handler is not None:
                handler_entry = possible_prefix_state(node.body, capture_state())
                restore_state(handler_entry)
                replay_path(target_handler)
                return
        if isinstance(node, (ast.While, ast.For, ast.AsyncFor)) and any(
            item is site or item in site_ancestors for item in node.orelse
        ):
            runtime_value = (
                _trusted_runtime_condition(
                    node.test, type_checking_symbols, typing_modules
                )
                if isinstance(node, ast.While)
                else None
            )
            if isinstance(node, (ast.For, ast.AsyncFor)) and _proven_empty_iterable(
                node.iter
            ):
                apply_effects([node.iter])
            else:
                apply_path_header(node)
            entry = capture_state()
            if runtime_value is False:
                normal_input = entry
            else:
                (
                    body_normal,
                    _body_breaks,
                    body_continues,
                    _body_returns,
                    _body_raises,
                ) = loop_body_paths(node.body, entry)
                normal_inputs = [entry]
                normal_inputs.extend(body_normal)
                normal_inputs.extend(body_continues)
                normal_input = join_states(normal_inputs)
            restore_state(normal_input)
            replay(node.orelse)
            return
        if isinstance(node, (ast.Try, ast.TryStar)) and any(
            item is site or item in site_ancestors for item in node.orelse
        ):
            # Entering ``else`` proves every statement in the try body
            # completed without raising, so its bindings are visible here.
            replay(node.body)
            replay(node.orelse)
            return
        if isinstance(node, (ast.Try, ast.TryStar)) and any(
            item is site or item in site_ancestors for item in node.finalbody
        ):
            # Join the actual predecessor states of ``finally``.  This keeps
            # identical trusted imports made on every path while invalidating
            # writes, deletes, and namespace mutations that differ by path.
            entry = capture_state()
            body_flow = loop_body_paths(node.body, entry)
            predecessors: list[ProvenanceState] = []
            for normal_predecessor in body_flow[0]:
                else_flow = loop_body_paths(node.orelse, normal_predecessor)
                for states in else_flow:
                    predecessors.extend(states)
            for states in body_flow[1:]:
                predecessors.extend(states)
            handler_entry = possible_prefix_state(node.body, entry)
            # ``finally`` also runs for an exception not matched by any
            # handler, including one raised before a binding completes.
            predecessors.append(handler_entry)
            for handler in node.handlers:
                restore_state(handler_entry)
                if handler.type is not None:
                    apply_effects([handler.type])
                if handler.name:
                    clear_binding(handler.name)
                handler_flow = loop_body_paths(handler.body, capture_state())
                for states in handler_flow:
                    predecessors.extend(states)
            restore_state(join_states(predecessors))
            replay(node.finalbody)
            return
        apply_path_header(node)
        for _field, value in ast.iter_fields(node):
            if isinstance(value, list):
                path_items = [
                    item
                    for item in value
                    if isinstance(item, ast.AST)
                    and (item is site or item in site_ancestors)
                ]
                if not path_items:
                    continue
                statements = [item for item in value if isinstance(item, ast.stmt)]
                if statements and len(statements) == len(value):
                    replay(statements)
                else:
                    replay_path(path_items[0])
                return
            if isinstance(value, ast.AST) and (
                value is site or value in site_ancestors
            ):
                if value is not site:
                    replay_path(value)
                return

    def replay(statements: list[ast.stmt]) -> None:
        nonlocal site_is_nonruntime
        for statement in statements:
            if statement is site:
                return
            if statement in site_ancestors:
                if isinstance(
                    statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    apply_effects(definition_header_nodes(statement))
                    clear_binding(statement.name)
                    return
                replay_path(statement)
                return
            if not apply_statement(statement):
                site_is_nonruntime = True
                return

    replay(tree.body)

    scopes: list[ast.AST] = []
    current = parents.get(site)
    while current is not None and not isinstance(current, ast.Module):
        if isinstance(
            current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            scopes.append(current)
        current = parents.get(current)

    def declared_outer_names(
        scope: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    ) -> set[str]:
        """Return ``global``/``nonlocal`` names declared by this function."""
        body = scope.body
        stack: list[ast.AST] = (
            list(reversed(body)) if isinstance(body, list) else [body]
        )
        declared: set[str] = set()
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                declared.update(node.names)
                continue
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                continue
            stack.extend(reversed(list(ast.iter_child_nodes(node))))
        return declared

    for scope in reversed(scopes):
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = scope.args
            parameters = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            if args.vararg:
                parameters.append(args.vararg)
            if args.kwarg:
                parameters.append(args.kwarg)
            body = scope.body
            body_nodes: list[ast.AST] = list(body) if isinstance(body, list) else [body]
            local_names = {parameter.arg for parameter in parameters}
            bound_names, _ = binding_effects(body_nodes)
            local_names.update(bound_names)
            # A Store/Del for a declared global/nonlocal updates an outer
            # binding; it does not shadow that binding from function entry.
            # Replay below still invalidates it when the write precedes the
            # annotation site.
            local_names.difference_update(declared_outer_names(scope))
            for name in local_names:
                clear_binding(name)
            if isinstance(body, list):
                replay(body)
        elif isinstance(scope, ast.ClassDef):
            replay(scope.body)

    if site_is_nonruntime:
        return {}, set(), {}, {}
    return symbols, modules, containers, generic_modules


def _trusted_subagent_type(
    node: ast.AST, symbols: dict[str, str], modules: set[str]
) -> str | None:
    if isinstance(node, ast.Name):
        return symbols.get(node.id)
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in modules
        and node.attr in _SUBAGENT_MAPPING_TYPES
    ):
        return node.attr
    return None


def _mentions_trusted_subagent(
    node: ast.AST, symbols: dict[str, str], modules: set[str]
) -> bool:
    return any(
        _trusted_subagent_type(part, symbols, modules) is not None
        for part in ast.walk(node)
    )


def _generic_container_kind(
    node: ast.AST, containers: dict[str, str], generic_modules: dict[str, str]
) -> str | None:
    if isinstance(node, ast.Name):
        return containers.get(node.id)
    if not isinstance(node, ast.Attribute):
        return None
    module = ""
    if isinstance(node.value, ast.Name):
        module = generic_modules.get(node.value.id, "")
    elif (
        isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.attr == "abc"
        and generic_modules.get(node.value.value.id) == "collections"
    ):
        module = "collections.abc"
    if module not in {"typing", "collections.abc"}:
        return None
    return _GENERIC_MODULE_MEMBERS[module].get(node.attr)


def _typed_subagent_annotation(
    annotation: ast.AST,
    symbols: dict[str, str],
    modules: set[str],
    containers: dict[str, str],
    generic_modules: dict[str, str],
) -> tuple[str, str] | None:
    """Return ``(SubAgent type, shape)`` for one exact supported annotation."""
    direct = _trusted_subagent_type(annotation, symbols, modules)
    if direct is not None:
        return direct, "direct"
    if not isinstance(annotation, ast.Subscript):
        return None
    if any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
        for node in ast.walk(annotation)
    ):
        return None
    shape = _generic_container_kind(annotation.value, containers, generic_modules)
    if shape is None:
        return None

    if shape == "sequence":
        item_type = _trusted_subagent_type(annotation.slice, symbols, modules)
        return (item_type, "sequence") if item_type is not None else None

    if shape == "tuple":
        if isinstance(annotation.slice, ast.Tuple):
            items = annotation.slice.elts
            if not (
                len(items) == 2
                and isinstance(items[1], ast.Constant)
                and items[1].value is Ellipsis
            ):
                return None
            item_node = items[0]
        else:
            item_node = annotation.slice
        item_type = _trusted_subagent_type(item_node, symbols, modules)
        return (item_type, "sequence") if item_type is not None else None

    if shape == "mapping":
        items = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else []
        if len(items) != 2 or _mentions_trusted_subagent(items[0], symbols, modules):
            return None
        value_type = _trusted_subagent_type(items[1], symbols, modules)
        return (value_type, "mapping") if value_type is not None else None

    return None


def _literal_subagent_mapping(
    node: ast.Dict, annotation: str
) -> tuple[str, str, int, str | None, tuple[str, ...]] | None:
    fields: dict[str, ast.AST] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        if key.value in fields:
            return None
        fields[key.value] = value
    raw_name = fields.get("name")
    if not isinstance(raw_name, ast.Constant) or not isinstance(raw_name.value, str):
        return None
    if not raw_name.value.strip():
        return None
    raw_model = fields.get("model")
    model_name = (
        raw_model.value
        if isinstance(raw_model, ast.Constant)
        and isinstance(raw_model.value, str)
        and raw_model.value.strip()
        else None
    )
    tool_names: list[str] = []
    raw_tools = fields.get("tools")
    if isinstance(raw_tools, (ast.List, ast.Tuple, ast.Set)):
        for item in raw_tools.elts:
            if (
                isinstance(item, ast.Constant)
                and isinstance(item.value, str)
                and item.value.strip()
            ):
                tool_names.append(item.value)
            elif isinstance(item, ast.Name):
                tool_names.append(item.id)
    return raw_name.value, annotation, node.lineno, model_name, tuple(tool_names)


def _extract_typed_subagent_mappings(
    source: str,
) -> list[tuple[str, str, int, str | None, tuple[str, ...]]]:
    """Extract only direct literal mappings with explicit SubAgent-family types."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    found: list[tuple[str, str, int, str | None, tuple[str, ...]]] = []
    for assignment in (node for node in ast.walk(tree) if isinstance(node, ast.AnnAssign)):
        symbols, modules, containers, generic_modules = _annotation_provenance(
            tree, assignment, parents
        )
        if not symbols and not modules:
            continue
        annotation = _typed_subagent_annotation(
            assignment.annotation,
            symbols,
            modules,
            containers,
            generic_modules,
        )
        if annotation is None or assignment.value is None:
            continue
        annotation_name, shape = annotation
        candidates: list[ast.Dict] = []
        if shape == "direct" and isinstance(assignment.value, ast.Dict):
            candidates.append(assignment.value)
        elif shape == "sequence" and isinstance(assignment.value, (ast.List, ast.Tuple)):
            candidates.extend(
                item for item in assignment.value.elts if isinstance(item, ast.Dict)
            )
        elif shape == "mapping" and isinstance(assignment.value, ast.Dict):
            candidates.extend(
                value
                for key, value in zip(
                    assignment.value.keys, assignment.value.values, strict=True
                )
                if isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Dict)
            )
        for candidate in candidates:
            item = _literal_subagent_mapping(candidate, annotation_name)
            if item is not None:
                found.append(item)
    return found


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
    humanized = humanize_context_name(context)
    if humanized:
        return humanized
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
