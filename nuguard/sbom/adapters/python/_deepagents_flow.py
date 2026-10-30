"""Sound cross-file dependency flow for Deep Agent component bundles.

The index deliberately recognizes a small Python subset.  It follows an exact
typed component attribute back to one project-local dataclass construction and
then follows only statically enumerable model, tool, and subagent values.
Unknown collection items are ignored; ambiguous constructors, imports, writes,
and call sites suppress the affected flow instead of inventing relationships.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from ...normalization import canonicalize_text
from .._test_paths import looks_like_test_path
from ..models_kb import LANGCHAIN_LLM_CLASS_PROVIDERS

_MAX_DEPTH = 8
_FACTORIES = {
    ("deepagents", "create_deep_agent"),
    ("managed_deepagents", "define_deep_agent"),
}
_COLLECTION_WRAPPERS = {"list", "tuple"}
_MODEL_IMPORT_ROOTS: dict[str, frozenset[str]] = {
    "ChatOpenAI": frozenset({"langchain_openai"}),
    "AzureChatOpenAI": frozenset({"langchain_openai"}),
    "ChatAnthropic": frozenset({"langchain_anthropic"}),
    "ChatGoogleGenerativeAI": frozenset({"langchain_google_genai"}),
    "ChatVertexAI": frozenset({"langchain_google_vertexai"}),
    "ChatOllama": frozenset({"langchain_ollama", "langchain_community"}),
    "ChatMistralAI": frozenset({"langchain_mistralai"}),
    "ChatCohere": frozenset({"langchain_cohere"}),
    "ChatGroq": frozenset({"langchain_groq"}),
    "ChatBedrock": frozenset({"langchain_aws", "langchain_community"}),
    "BedrockChat": frozenset({"langchain_aws", "langchain_community"}),
    "Bedrock": frozenset({"langchain_aws", "langchain_community"}),
    "BedrockLLM": frozenset({"langchain_aws", "langchain_community"}),
}


@dataclass(frozen=True)
class DeepAgentFlowTarget:
    component_type: Literal["MODEL", "TOOL", "AGENT"]
    canonical_name: str
    display_name: str
    file_path: str
    line: int
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DeepAgentComponentFlow:
    models: tuple[DeepAgentFlowTarget, ...] = ()
    tools: tuple[DeepAgentFlowTarget, ...] = ()
    subagents: tuple[DeepAgentFlowTarget, ...] = ()


@dataclass(frozen=True)
class _ImportRef:
    module: str
    name: str | None


@dataclass
class _Module:
    path: str
    module_name: str
    tree: ast.Module
    imports: dict[str, _ImportRef]
    classes: dict[str, ast.ClassDef]
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]


class DeepAgentComponentFlowIndex:
    """Immutable lookup keyed by unambiguous ``(file, line, factory)``."""

    def __init__(
        self,
        flows: dict[tuple[str, int, str], DeepAgentComponentFlow] | None = None,
    ) -> None:
        self._flows = dict(flows or {})

    def resolve(self, file_path: str, line: int, factory: str) -> DeepAgentComponentFlow | None:
        return self._flows.get((_norm(file_path), line, factory))


EMPTY_DEEP_AGENT_COMPONENT_FLOW = DeepAgentComponentFlowIndex()


def build_deep_agent_component_flow_index(
    sources: dict[str, str],
) -> DeepAgentComponentFlowIndex:
    modules = _parse_modules(sources)
    resolver = _FlowResolver(modules)
    return DeepAgentComponentFlowIndex(resolver.build())


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _module_name(path: str) -> str:
    parts = list(PurePosixPath(_norm(path)).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if parts and parts[0] in {"src", "lib"}:
        parts.pop(0)
    return ".".join(parts)


def _parse_modules(sources: dict[str, str]) -> dict[str, _Module]:
    modules: dict[str, _Module] = {}
    for raw_path, source in sources.items():
        path = _norm(raw_path)
        if looks_like_test_path(path):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        imports: dict[str, _ImportRef] = {}
        classes: dict[str, ast.ClassDef] = {}
        functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        name = _module_name(path)
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    local = alias.asname or alias.name.split(".")[0]
                    imports[local] = _ImportRef(alias.name, None)
            elif isinstance(statement, ast.ImportFrom):
                module = _absolute_import(name, statement.level, statement.module or "")
                for alias in statement.names:
                    if alias.name != "*":
                        imports[alias.asname or alias.name] = _ImportRef(module, alias.name)
            elif isinstance(statement, ast.ClassDef):
                classes[statement.name] = statement
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[statement.name] = statement
        binding_counts = _module_scope_binding_counts(tree)
        imports = {
            local: ref
            for local, ref in imports.items()
            if binding_counts.get(local) == 1 and not binding_counts.get("*")
        }
        classes = {
            local: node
            for local, node in classes.items()
            if binding_counts.get(local) == 1 and not binding_counts.get("*")
        }
        functions = {
            local: node
            for local, node in functions.items()
            if binding_counts.get(local) == 1 and not binding_counts.get("*")
        }
        modules[path] = _Module(path, name, tree, imports, classes, functions)
    return modules


def _absolute_import(current: str, level: int, module: str) -> str:
    if level == 0:
        return module
    package = current.split(".")[:-1]
    keep = max(0, len(package) - (level - 1))
    return ".".join([*package[:keep], *([module] if module else [])]).strip(".")


def _module_scope_binding_counts(tree: ast.Module) -> dict[str, int]:
    """Count bindings evaluated in module scope, excluding nested bodies."""
    counts: dict[str, int] = {}

    def bind(name: str | None) -> None:
        if name:
            counts[name] = counts.get(name, 0) + 1

    class Visitor(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                bind(alias.asname or alias.name.split(".")[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                bind(alias.asname or alias.name)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            bind(node.name)
            for decorator in node.decorator_list:
                self.visit(decorator)
            self.visit(node.args)
            if node.returns is not None:
                self.visit(node.returns)
            for type_param in getattr(node, "type_params", ()):
                self.visit(type_param)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            bind(node.name)
            for decorator in node.decorator_list:
                self.visit(decorator)
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword.value)
            for type_param in getattr(node, "type_params", ()):
                self.visit(type_param)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            self.visit(node.args)

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bind(node.id)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            bind(node.name)
            if node.type is not None:
                self.visit(node.type)
            for statement in node.body:
                self.visit(statement)

        def visit_MatchAs(self, node: ast.MatchAs) -> None:
            bind(node.name)
            if node.pattern is not None:
                self.visit(node.pattern)

        def visit_MatchStar(self, node: ast.MatchStar) -> None:
            bind(node.name)

        def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
            bind(node.rest)
            self.generic_visit(node)

    Visitor().visit(tree)
    return counts


def _module_attribute_is_stable(module: _Module, local: str, attribute: str) -> bool:
    """Reject syntactic mutation of a member reached through an imported module."""
    aliases = {local}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                continue
            targets: list[ast.expr]
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            else:
                targets = [node.target]
                value = node.value
            if not isinstance(value, ast.Name) or value.id not in aliases:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True

    for node in ast.walk(module.tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
            and node.attr == attribute
        ):
            return False
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "__dict__"
                and isinstance(value.value, ast.Name)
                and value.value.id in aliases
                and _literal_string(node.slice) in {None, attribute}
            ):
                return False
        if not isinstance(node, ast.Call) or len(node.args) < 1:
            continue
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in {"setattr", "delattr"}
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in aliases
            and (len(node.args) < 2 or _literal_string(node.args[1]) in {None, attribute})
        ):
            return False
    return True


class _FlowResolver:
    def __init__(self, modules: dict[str, _Module]) -> None:
        self.modules = modules
        self.by_name: dict[str, list[_Module]] = {}
        for module in modules.values():
            self.by_name.setdefault(module.module_name, []).append(module)
        self.parent_maps = {path: _parents(module.tree) for path, module in modules.items()}
        self._constructor_cache: dict[
            tuple[str, str],
            tuple[_Module, ast.FunctionDef | ast.AsyncFunctionDef, ast.Call] | None,
        ] = {}
        self._tool_cache: dict[tuple[str, str], tuple[DeepAgentFlowTarget, ...]] = {}
        self._subagent_cache: dict[tuple[str, str], tuple[DeepAgentFlowTarget, ...]] = {}

    def build(self) -> dict[tuple[str, int, str], DeepAgentComponentFlow]:
        pending: dict[tuple[str, int, str], list[DeepAgentComponentFlow]] = {}
        for module in self.modules.values():
            parents = self.parent_maps[module.path]
            for node in ast.walk(module.tree):
                if not isinstance(node, ast.Call):
                    continue
                function = _enclosing_function(node, parents)
                factory = self._deep_agent_factory(module, node.func, function)
                if factory is None:
                    continue
                flow = self._flow_for_call(module, node, parents)
                if flow is None:
                    continue
                key = (module.path, node.lineno, factory)
                pending.setdefault(key, []).append(flow)
        # ParsedCall exposes a line but not a column.  Same-line peers are
        # therefore intentionally rejected rather than correlated by order.
        return {key: values[0] for key, values in pending.items() if len(values) == 1}

    def _deep_agent_factory(
        self,
        module: _Module,
        expr: ast.expr,
        function: ast.FunctionDef | ast.AsyncFunctionDef | None,
    ) -> str | None:
        if isinstance(expr, ast.Name):
            if function is not None and _function_binds_name(function, expr.id):
                return None
            ref = module.imports.get(expr.id)
            if ref and ref.name and (ref.module, ref.name) in _FACTORIES:
                return ref.name
            return None
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            if function is not None and _function_binds_name(function, expr.value.id):
                return None
            ref = module.imports.get(expr.value.id)
            if (
                ref
                and ref.name is None
                and (ref.module, expr.attr) in _FACTORIES
                and _module_attribute_is_stable(module, expr.value.id, expr.attr)
            ):
                return expr.attr
        return None

    def _flow_for_call(
        self,
        module: _Module,
        call: ast.Call,
        parents: dict[ast.AST, ast.AST],
    ) -> DeepAgentComponentFlow | None:
        function = _enclosing_function(call, parents)
        if function is None:
            return None
        parameter_types = self._component_parameters(module, function)
        if not parameter_types:
            return None
        kwargs = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
        resolved: dict[str, tuple[DeepAgentFlowTarget, ...]] = {}
        for field in ("model", "tools", "subagents"):
            attribute = _component_attribute(module, function, kwargs.get(field), field)
            if attribute is None:
                continue
            variable, attribute_name = attribute
            component = parameter_types.get(variable)
            if component is None or attribute_name != field:
                continue
            if not _component_reference_is_stable(function, variable, field):
                continue
            constructor = self._unique_component_constructor(*component)
            if constructor is None:
                continue
            constructor_module, constructor_function, constructor_call = constructor
            field_expr = _call_keyword(constructor_call, field)
            if field_expr is None:
                continue
            if field == "model":
                target = self._resolve_model(
                    constructor_module, constructor_function, field_expr, 0, frozenset()
                )
                resolved[field] = (target,) if target else ()
            elif field == "tools":
                resolved[field] = self._resolve_collection(
                    constructor_module,
                    constructor_function,
                    field_expr,
                    "tool",
                    0,
                    frozenset(),
                )
            else:
                resolved[field] = self._resolve_collection(
                    constructor_module,
                    constructor_function,
                    field_expr,
                    "subagent",
                    0,
                    frozenset(),
                )
        if not resolved:
            return None
        return DeepAgentComponentFlow(
            models=_dedup(resolved.get("model", ())),
            tools=_dedup(resolved.get("tools", ())),
            subagents=_dedup(resolved.get("subagents", ())),
        )

    def _component_parameters(
        self, module: _Module, function: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> dict[str, tuple[_Module, ast.ClassDef]]:
        result: dict[str, tuple[_Module, ast.ClassDef]] = {}
        arguments = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
        for argument in arguments:
            target = self._resolve_class_annotation(module, argument.annotation)
            if (
                target
                and _is_frozen_dataclass(target[0], target[1])
                and _component_fields(target[0], target[1])
                == {
                    "model",
                    "tools",
                    "subagents",
                }
            ):
                result[argument.arg] = target
        return result

    def _resolve_class_annotation(
        self, module: _Module, annotation: ast.expr | None
    ) -> tuple[_Module, ast.ClassDef] | None:
        if isinstance(annotation, ast.Name):
            if annotation.id in module.classes:
                return module, module.classes[annotation.id]
            ref = module.imports.get(annotation.id)
            if ref and ref.name:
                target_module = self._one_module(ref.module)
                if target_module and ref.name in target_module.classes:
                    return target_module, target_module.classes[ref.name]
        elif isinstance(annotation, ast.Attribute) and isinstance(annotation.value, ast.Name):
            ref = module.imports.get(annotation.value.id)
            if ref and ref.name is None:
                target_module = self._one_module(ref.module)
                if target_module and annotation.attr in target_module.classes:
                    return target_module, target_module.classes[annotation.attr]
        return None

    def _unique_component_constructor(
        self, class_module: _Module, class_node: ast.ClassDef
    ) -> tuple[_Module, ast.FunctionDef | ast.AsyncFunctionDef, ast.Call] | None:
        key = (class_module.path, class_node.name)
        if key in self._constructor_cache:
            return self._constructor_cache[key]
        matches: list[tuple[_Module, ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]] = []
        for module in self.modules.values():
            parents = self.parent_maps[module.path]
            for node in ast.walk(module.tree):
                if not isinstance(node, ast.Call):
                    continue
                function = _enclosing_function(node, parents)
                if (
                    function is None
                    or not self._call_is_class(
                        module, function, node.func, class_module, class_node.name
                    )
                    or not isinstance(parents.get(node), ast.Return)
                    or node not in _function_returns(function)
                ):
                    continue
                matches.append((module, function, node))
        result = matches[0] if len(matches) == 1 else None
        self._constructor_cache[key] = result
        return result

    def _call_is_class(
        self,
        module: _Module,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        expr: ast.expr,
        class_module: _Module,
        class_name: str,
    ) -> bool:
        if isinstance(expr, ast.Name):
            if _function_binds_name(function, expr.id):
                return False
            if module is class_module and expr.id == class_name:
                return True
            ref = module.imports.get(expr.id)
            return bool(
                ref and ref.name == class_name and self._one_module(ref.module) is class_module
            )
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            if _function_binds_name(function, expr.value.id):
                return False
            ref = module.imports.get(expr.value.id)
            return bool(
                ref
                and ref.name is None
                and expr.attr == class_name
                and self._one_module(ref.module) is class_module
            )
        return False

    def _resolve_model(
        self,
        module: _Module,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        expr: ast.expr,
        depth: int,
        seen: frozenset[tuple[str, str]],
    ) -> DeepAgentFlowTarget | None:
        if depth >= _MAX_DEPTH:
            return None
        if isinstance(expr, ast.Name):
            assignment = _single_function_assignment(function, expr.id)
            if assignment is None:
                return None
            return self._resolve_model(module, function, assignment, depth + 1, seen)
        if not isinstance(expr, ast.Call):
            return None
        class_name = _call_terminal(expr.func)
        if class_name not in LANGCHAIN_LLM_CLASS_PROVIDERS:
            return None
        if not self._exact_model_import(module, function, expr.func, class_name):
            return None
        values = {keyword.arg: keyword.value for keyword in expr.keywords if keyword.arg}
        raw = next(
            (
                _literal_string(values.get(key))
                for key in ("model", "model_name", "model_id", "deployment_name")
                if _literal_string(values.get(key))
            ),
            None,
        )
        name = raw or class_name
        return DeepAgentFlowTarget(
            "MODEL",
            canonicalize_text(name.lower()),
            name,
            module.path,
            expr.lineno,
            (("class_name", class_name), ("provider", LANGCHAIN_LLM_CLASS_PROVIDERS[class_name])),
        )

    def _exact_model_import(
        self,
        module: _Module,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        expr: ast.expr,
        class_name: str,
    ) -> bool:
        if isinstance(expr, ast.Name):
            if _function_binds_name(function, expr.id):
                return False
            ref = module.imports.get(expr.id)
            return bool(
                ref
                and ref.name == class_name
                and ref.module.split(".")[0] in _MODEL_IMPORT_ROOTS.get(class_name, ())
            )
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            if _function_binds_name(function, expr.value.id):
                return False
            ref = module.imports.get(expr.value.id)
            return bool(
                ref
                and ref.name is None
                and expr.attr == class_name
                and ref.module.split(".")[0] in _MODEL_IMPORT_ROOTS.get(class_name, ())
            )
        return False

    def _resolve_collection(
        self,
        module: _Module,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        expr: ast.expr,
        kind: Literal["tool", "subagent"],
        depth: int,
        seen: frozenset[tuple[str, str]],
    ) -> tuple[DeepAgentFlowTarget, ...]:
        if depth >= _MAX_DEPTH:
            return ()
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            if expr.func.id in _COLLECTION_WRAPPERS and _builtin_is_live(
                module, function, expr.func.id
            ):
                if len(expr.args) == 1:
                    return self._resolve_collection(
                        module, function, expr.args[0], kind, depth + 1, seen
                    )
        if kind == "tool" and isinstance(expr, ast.ListComp):
            return _structured_tool_comprehension_targets(module, function, expr)
        if isinstance(expr, (ast.List, ast.Tuple)):
            found: list[DeepAgentFlowTarget] = []
            for item in expr.elts:
                if isinstance(item, ast.Starred):
                    found.extend(
                        self._resolve_collection(
                            module, function, item.value, kind, depth + 1, seen
                        )
                    )
                else:
                    target = self._resolve_item(module, function, item, kind, depth, seen)
                    if target:
                        found.extend(target)
            return _dedup(found)
        if isinstance(expr, ast.Name):
            key = (module.path, f"{function.name}:{expr.id}:{kind}")
            if key in seen:
                return ()
            states = _collection_states(function, expr.id)
            if states is None:
                return ()
            found: list[DeepAgentFlowTarget] = []
            next_seen = seen | {key}
            for value in states.assignments:
                found.extend(
                    self._resolve_collection(module, function, value, kind, depth + 1, next_seen)
                )
            for call in states.appends:
                if call.func.attr == "append" and len(call.args) == 1:
                    target = self._resolve_item(
                        module, function, call.args[0], kind, depth + 1, next_seen
                    )
                    if target:
                        found.extend(target)
                elif call.func.attr == "extend" and len(call.args) == 1:
                    found.extend(
                        self._resolve_collection(
                            module, function, call.args[0], kind, depth + 1, next_seen
                        )
                    )
            return _dedup(found)
        if isinstance(expr, ast.Call):
            if kind == "tool":
                target = _structured_tool_target(module, function, expr)
                if target:
                    return (target,)
            function_target = self._resolve_function_call(module, expr)
            if function_target is None:
                return ()
            target_module, target_function = function_target
            key = (target_module.path, f"{target_function.name}:{kind}")
            if key in seen:
                return ()
            cache = self._tool_cache if kind == "tool" else self._subagent_cache
            if key in cache:
                return cache[key]
            found: list[DeepAgentFlowTarget] = []
            for returned in _function_returns(target_function):
                found.extend(
                    self._resolve_collection(
                        target_module,
                        target_function,
                        returned,
                        kind,
                        depth + 1,
                        seen | {key},
                    )
                )
            result = _dedup(found)
            cache[key] = result
            return result
        return ()

    def _resolve_item(
        self,
        module: _Module,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        expr: ast.expr,
        kind: Literal["tool", "subagent"],
        depth: int,
        seen: frozenset[tuple[str, str]],
    ) -> tuple[DeepAgentFlowTarget, ...]:
        if kind == "subagent" and isinstance(expr, ast.Dict):
            return self._subagent_dict_targets(module, function, expr)
        if kind == "tool" and isinstance(expr, ast.Call):
            target = _structured_tool_target(module, function, expr)
            if target:
                return (target,)
            return self._resolve_collection(module, function, expr, kind, depth + 1, seen)
        if isinstance(expr, ast.Name):
            assignment = _single_function_assignment(function, expr.id)
            if assignment is not None:
                return self._resolve_item(module, function, assignment, kind, depth + 1, seen)
        return ()

    def _subagent_dict_targets(
        self,
        module: _Module,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        expr: ast.Dict,
    ) -> tuple[DeepAgentFlowTarget, ...]:
        pairs = list(zip(expr.keys, expr.values, strict=True))
        name_indexes = [
            index for index, (key, _) in enumerate(pairs) if _literal_string(key) == "name"
        ]
        if len(name_indexes) != 1:
            return ()
        name_index = name_indexes[0]
        # Earlier unpacked or computed keys are overwritten by the explicit
        # name.  Anything ambiguous after it could replace that name at runtime.
        if any(
            key is None or not isinstance(key, ast.Constant) for key, _ in pairs[name_index + 1 :]
        ):
            return ()
        name_expr = pairs[name_index][1]
        names = self._resolve_subagent_names(module, function, expr, name_expr)
        return tuple(
            DeepAgentFlowTarget(
                "AGENT",
                canonicalize_text(f"langgraph:{name}"),
                name,
                module.path,
                expr.lineno,
                (("is_subagent", "true"),),
            )
            for name in names
        )

    def _resolve_subagent_names(
        self,
        module: _Module,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        node: ast.AST,
        expr: ast.expr | None,
    ) -> tuple[str, ...]:
        literal = _literal_string(expr)
        if literal:
            return (literal,)
        if isinstance(expr, ast.Name):
            value = _module_string_constant(module, expr.id)
            return (value,) if value else ()
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            parents = _parents(function)
            current: ast.AST | None = node
            while current is not None and not isinstance(current, ast.For):
                current = parents.get(current)
            if not isinstance(current, ast.For) or not isinstance(current.target, ast.Name):
                return ()
            if current.target.id != expr.value.id or not isinstance(current.iter, ast.Name):
                return ()
            return _frozen_dataclass_tuple_field(module, current.iter.id, expr.attr)
        return ()

    def _resolve_function_call(
        self, module: _Module, call: ast.Call
    ) -> tuple[_Module, ast.FunctionDef | ast.AsyncFunctionDef] | None:
        parents = self.parent_maps[module.path]
        function = _enclosing_function(call, parents)
        if function is None:
            return None
        if isinstance(call.func, ast.Name):
            if _function_binds_name(function, call.func.id):
                return None
            if call.func.id in module.functions:
                return module, module.functions[call.func.id]
            ref = module.imports.get(call.func.id)
            if ref and ref.name:
                target = self._one_module(ref.module)
                if target and ref.name in target.functions:
                    return target, target.functions[ref.name]
        elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
            if _function_binds_name(function, call.func.value.id):
                return None
            ref = module.imports.get(call.func.value.id)
            if ref and ref.name is None:
                target = self._one_module(ref.module)
                if target and call.func.attr in target.functions:
                    return target, target.functions[call.func.attr]
        return None

    def _one_module(self, name: str) -> _Module | None:
        matches = self.by_name.get(name, [])
        return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class _CollectionStates:
    assignments: tuple[ast.expr, ...]
    appends: tuple[ast.Call, ...]


def _collection_states(
    function: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> _CollectionStates | None:
    parents = _parents(function)
    assignments: list[tuple[tuple[int, int], bool, ast.expr]] = []
    appends: list[tuple[tuple[int, int], ast.Call]] = []
    invalid = False

    class Visitor(ast.NodeVisitor):
        def visit_block(self, statements: list[ast.stmt]) -> None:
            for statement in statements:
                self.visit(statement)
                if _statement_definitely_terminates(statement):
                    break

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nonlocal invalid
            if node is function:
                self.visit_block(node.body)
            elif node.name == name:
                invalid = True

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            nonlocal invalid
            if node is function:
                self.visit_block(node.body)
            elif node.name == name:
                invalid = True

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            nonlocal invalid
            if node.name == name:
                invalid = True

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_If(self, node: ast.If) -> None:
            self.visit(node.test)
            truth = _static_truthiness(node.test)
            if truth is not False:
                self.visit_block(node.body)
            if truth is not True:
                self.visit_block(node.orelse)

        def visit_For(self, node: ast.For) -> None:
            self.visit(node.iter)
            if not _statically_empty_iterable(node.iter):
                self.visit(node.target)
                self.visit_block(node.body)
            self.visit_block(node.orelse)

        visit_AsyncFor = visit_For

        def visit_While(self, node: ast.While) -> None:
            self.visit(node.test)
            truth = _static_truthiness(node.test)
            if truth is not False:
                self.visit_block(node.body)
            if truth is not True:
                self.visit_block(node.orelse)

        def visit_With(self, node: ast.With) -> None:
            for item in node.items:
                self.visit(item.context_expr)
                if item.optional_vars is not None:
                    self.visit(item.optional_vars)
            self.visit_block(node.body)

        visit_AsyncWith = visit_With

        def visit_Try(self, node: ast.Try) -> None:
            self.visit_block(node.body)
            for handler in node.handlers:
                self.visit(handler)
            self.visit_block(node.orelse)
            self.visit_block(node.finalbody)

        visit_TryStar = visit_Try

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            nonlocal invalid
            if node.name == name:
                invalid = True
            if node.type is not None:
                self.visit(node.type)
            self.visit_block(node.body)

        def visit_Match(self, node: ast.Match) -> None:
            self.visit(node.subject)
            for case in node.cases:
                self.visit(case.pattern)
                if case.guard is not None:
                    self.visit(case.guard)
                self.visit_block(case.body)

        def visit_Name(self, node: ast.Name) -> None:
            nonlocal invalid
            if node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
                invalid = True

        def visit_Import(self, node: ast.Import) -> None:
            nonlocal invalid
            if any((alias.asname or alias.name.split(".")[0]) == name for alias in node.names):
                invalid = True

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            nonlocal invalid
            if any((alias.asname or alias.name) == name for alias in node.names):
                invalid = True

        def visit_MatchAs(self, node: ast.MatchAs) -> None:
            nonlocal invalid
            if node.name == name:
                invalid = True
            self.generic_visit(node)

        def visit_MatchStar(self, node: ast.MatchStar) -> None:
            nonlocal invalid
            if node.name == name:
                invalid = True

        def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
            nonlocal invalid
            if node.rest == name:
                invalid = True
            self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> None:
            nonlocal invalid
            targets = [target for target in node.targets if isinstance(target, ast.Name)]
            if any(target.id == name for target in targets):
                assignments.append(
                    ((node.lineno, node.col_offset), parents.get(node) is function, node.value)
                )
            if any(
                _target_contains(target, name) for target in node.targets if target not in targets
            ):
                invalid = True
            self.visit(node.value)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            nonlocal invalid
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value:
                assignments.append(
                    ((node.lineno, node.col_offset), parents.get(node) is function, node.value)
                )
            elif _target_contains(node.target, name):
                invalid = True
            if node.value:
                self.visit(node.value)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:
            nonlocal invalid
            if _target_contains(node.target, name):
                invalid = True
            self.visit(node.value)

        def visit_Call(self, node: ast.Call) -> None:
            nonlocal invalid
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id == name:
                    if node.func.attr in {"append", "extend"}:
                        appends.append(((node.lineno, node.col_offset), node))
                    else:
                        invalid = True
            self.generic_visit(node)

    Visitor().visit(function)
    if invalid or not assignments:
        return None
    direct_positions = [position for position, direct, _ in assignments if direct]
    cutoff = max(direct_positions) if direct_positions else (-1, -1)
    active_assignments = tuple(value for position, _, value in assignments if position >= cutoff)
    active_appends = tuple(call for position, call in appends if position >= cutoff)
    return _CollectionStates(active_assignments, active_appends)


def _statement_definitely_terminates(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
        return True
    if isinstance(statement, ast.If):
        truth = _static_truthiness(statement.test)
        if truth is True:
            return _block_definitely_terminates(statement.body)
        if truth is False:
            return bool(statement.orelse) and _block_definitely_terminates(statement.orelse)
        if statement.orelse:
            return _block_definitely_terminates(statement.body) and _block_definitely_terminates(
                statement.orelse
            )
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return _block_definitely_terminates(statement.body)
    if isinstance(statement, (ast.Try, ast.TryStar)):
        if statement.finalbody and _block_definitely_terminates(statement.finalbody):
            return True
        if not statement.handlers:
            return False
        normal = statement.orelse or statement.body
        return _block_definitely_terminates(normal) and all(
            _block_definitely_terminates(handler.body) for handler in statement.handlers
        )
    return False


def _static_truthiness(expr: ast.expr) -> bool | None:
    """Evaluate only literal truthiness; unknown expressions stay reachable."""
    if isinstance(expr, ast.Constant) and isinstance(
        expr.value, (type(None), bool, int, float, complex, str, bytes)
    ):
        return bool(expr.value)
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
        return bool(expr.elts if not isinstance(expr, ast.Dict) else expr.keys)
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        value = _static_truthiness(expr.operand)
        return None if value is None else not value
    if isinstance(expr, ast.BoolOp):
        values = [_static_truthiness(value) for value in expr.values]
        if isinstance(expr.op, ast.And):
            if False in values:
                return False
            return True if all(value is True for value in values) else None
        if isinstance(expr.op, ast.Or):
            if True in values:
                return True
            return False if all(value is False for value in values) else None
    return None


def _statically_empty_iterable(expr: ast.expr) -> bool:
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        return not expr.elts
    if isinstance(expr, ast.Dict):
        return not expr.keys
    return isinstance(expr, ast.Constant) and isinstance(expr.value, (str, bytes)) and not expr.value


def _block_definitely_terminates(statements: list[ast.stmt]) -> bool:
    return any(_statement_definitely_terminates(statement) for statement in statements)


def _target_contains(target: ast.expr, name: str) -> bool:
    root: ast.expr = target
    while isinstance(root, (ast.Attribute, ast.Subscript)):
        root = root.value
    return isinstance(root, ast.Name) and root.id == name


def _component_fields(module: _Module, node: ast.ClassDef) -> set[str]:
    if not any(_is_dataclass_decorator(module, decorator) for decorator in node.decorator_list):
        return set()
    annotations: dict[str, ast.expr] = {
        statement.target.id: statement.annotation
        for statement in node.body
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
    }
    wanted = {
        "model": ("BaseChatModel", frozenset({"langchain_core.language_models"})),
        "tools": ("BaseTool", frozenset({"langchain_core.tools"})),
        "subagents": (
            "SubAgent",
            frozenset({"deepagents", "deepagents.middleware.subagents"}),
        ),
    }
    return {
        field
        for field, (symbol, allowed_modules) in wanted.items()
        if field in annotations
        and _annotation_has_import(module, annotations[field], symbol, allowed_modules)
    }


def _annotation_has_import(
    module: _Module,
    annotation: ast.expr,
    symbol: str,
    allowed_modules: frozenset[str],
) -> bool:
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name):
            ref = module.imports.get(node.id)
            if ref and ref.name == symbol and ref.module in allowed_modules:
                return True
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            ref = module.imports.get(node.value.id)
            if ref and ref.name is None and ref.module in allowed_modules and node.attr == symbol:
                return True
    return False


def _component_attribute(
    module: _Module,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    expr: ast.expr | None,
    field: str,
) -> tuple[str, str] | None:
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.Or) and len(expr.values) == 2:
        if isinstance(expr.values[1], ast.Constant) and expr.values[1].value is None:
            expr = expr.values[0]
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        if (
            expr.func.id in _COLLECTION_WRAPPERS
            and _builtin_is_live(module, function, expr.func.id)
            and len(expr.args) == 1
        ):
            expr = expr.args[0]
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.attr == field:
        return expr.value.id, expr.attr
    return None


def _component_reference_is_stable(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    variable: str,
    field: str,
) -> bool:
    """Reject local rebinding or mutation of a proven component reference."""
    stable = True
    aliases: set[str] = {variable}
    alias_assignments: set[ast.AST] = set()
    parents = _parents(function)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(function):
            target: ast.Name | None = None
            value: ast.expr | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0] if isinstance(node.targets[0], ast.Name) else None
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target = node.target
                value = node.value
            elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                target = node.target
                value = node.value
            if (
                target is not None
                and isinstance(value, ast.Name)
                and value.id in aliases
                and target.id not in aliases
            ):
                aliases.add(target.id)
                alias_assignments.add(node)
                changed = True

    def rooted_at_component(expr: ast.AST) -> bool:
        current = expr
        attributes: list[str] = []
        while isinstance(current, (ast.Attribute, ast.Subscript)):
            if isinstance(current, ast.Attribute):
                attributes.append(current.attr)
            current = current.value
        return isinstance(current, ast.Name) and current.id in aliases and field in attributes

    def whole_component(expr: ast.AST) -> bool:
        return isinstance(expr, ast.Name) and expr.id in aliases

    def contains_whole_component(expr: ast.AST) -> bool:
        expression_parents = _parents(expr)
        for child in ast.walk(expr):
            if not (
                isinstance(child, ast.Name)
                and child.id in aliases
                and isinstance(child.ctx, ast.Load)
            ):
                continue
            parent = expression_parents.get(child)
            if isinstance(parent, (ast.Attribute, ast.Subscript)) and parent.value is child:
                continue
            return True
        return False

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nonlocal stable
            if node is function:
                for statement in node.body:
                    self.visit(statement)
            elif node.name == variable:
                stable = False
            elif any(
                isinstance(child, ast.Name)
                and child.id in aliases
                and isinstance(child.ctx, ast.Load)
                for child in ast.walk(node)
            ):
                stable = False

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            nonlocal stable
            if node.name == variable:
                stable = False

        def visit_Lambda(self, node: ast.Lambda) -> None:
            nonlocal stable
            if any(
                isinstance(child, ast.Name)
                and child.id in aliases
                and isinstance(child.ctx, ast.Load)
                for child in ast.walk(node.body)
            ):
                stable = False

        def visit_Name(self, node: ast.Name) -> None:
            nonlocal stable
            if node.id in aliases and isinstance(node.ctx, (ast.Store, ast.Del)):
                parent = parents.get(node)
                if parent not in alias_assignments:
                    stable = False

        def visit_Attribute(self, node: ast.Attribute) -> None:
            nonlocal stable
            if isinstance(node.ctx, (ast.Store, ast.Del)) and rooted_at_component(node):
                stable = False
            self.generic_visit(node)

        def visit_Subscript(self, node: ast.Subscript) -> None:
            nonlocal stable
            if isinstance(node.ctx, (ast.Store, ast.Del)) and rooted_at_component(node):
                stable = False
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            nonlocal stable
            recognized_mutator = False
            if isinstance(node.func, ast.Attribute) and rooted_at_component(node.func.value):
                stable = False
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"setattr", "delattr"}
                and node.args
                and whole_component(node.args[0])
            ):
                recognized_mutator = True
                if len(node.args) < 2 or _literal_string(node.args[1]) in {None, field}:
                    stable = False
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "object"
                and node.func.attr in {"__setattr__", "__delattr__"}
                and node.args
                and whole_component(node.args[0])
            ):
                recognized_mutator = True
                if len(node.args) < 2 or _literal_string(node.args[1]) in {None, field}:
                    stable = False
            if not recognized_mutator and (
                any(contains_whole_component(arg) for arg in node.args)
                or any(contains_whole_component(keyword.value) for keyword in node.keywords)
            ):
                stable = False
            self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> None:
            nonlocal stable
            if node not in alias_assignments and contains_whole_component(node.value):
                stable = False
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            nonlocal stable
            if (
                node not in alias_assignments
                and node.value is not None
                and contains_whole_component(node.value)
            ):
                stable = False
            self.generic_visit(node)

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
            nonlocal stable
            if node not in alias_assignments and contains_whole_component(node.value):
                stable = False
            self.generic_visit(node)

        def visit_Return(self, node: ast.Return) -> None:
            nonlocal stable
            if node.value is not None and contains_whole_component(node.value):
                stable = False
            self.generic_visit(node)

        def visit_Yield(self, node: ast.Yield) -> None:
            nonlocal stable
            if node.value is not None and contains_whole_component(node.value):
                stable = False
            self.generic_visit(node)

        visit_YieldFrom = visit_Yield

        def visit_Import(self, node: ast.Import) -> None:
            nonlocal stable
            if any((alias.asname or alias.name.split(".")[0]) == variable for alias in node.names):
                stable = False

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            nonlocal stable
            if any((alias.asname or alias.name) == variable for alias in node.names):
                stable = False

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            nonlocal stable
            if node.name == variable:
                stable = False
            self.generic_visit(node)

        def visit_MatchAs(self, node: ast.MatchAs) -> None:
            nonlocal stable
            if node.name == variable:
                stable = False
            self.generic_visit(node)

        def visit_MatchStar(self, node: ast.MatchStar) -> None:
            nonlocal stable
            if node.name == variable:
                stable = False

        def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
            nonlocal stable
            if node.rest == variable:
                stable = False
            self.generic_visit(node)

    Visitor().visit(function)
    return stable


def _call_keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    result: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            result[child] = parent
    return result


def _enclosing_function(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _single_function_assignment(
    function: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> ast.expr | None:
    matches: list[ast.expr] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is function:
                self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node is function:
                self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Assign(self, node: ast.Assign) -> None:
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                matches.append(node.value)
            self.visit(node.value)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == name
                and node.value is not None
            ):
                matches.append(node.value)
            if node.value is not None:
                self.visit(node.value)

    Visitor().visit(function)
    return matches[0] if len(matches) == 1 else None


def _function_returns(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.expr, ...]:
    values: list[ast.expr] = []

    class Visitor(ast.NodeVisitor):
        def visit_block(self, statements: list[ast.stmt]) -> None:
            for statement in statements:
                self.visit(statement)
                if _statement_definitely_terminates(statement):
                    break

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is function:
                self.visit_block(node.body)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node is function:
                self.visit_block(node.body)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Return(self, node: ast.Return) -> None:
            if node.value is not None:
                values.append(node.value)

        def visit_If(self, node: ast.If) -> None:
            truth = _static_truthiness(node.test)
            if truth is not False:
                self.visit_block(node.body)
            if truth is not True:
                self.visit_block(node.orelse)

        def visit_For(self, node: ast.For) -> None:
            if not _statically_empty_iterable(node.iter):
                self.visit_block(node.body)
            self.visit_block(node.orelse)

        visit_AsyncFor = visit_For

        def visit_While(self, node: ast.While) -> None:
            truth = _static_truthiness(node.test)
            if truth is not False:
                self.visit_block(node.body)
            if truth is not True:
                self.visit_block(node.orelse)

        def visit_With(self, node: ast.With) -> None:
            self.visit_block(node.body)

        visit_AsyncWith = visit_With

        def visit_Try(self, node: ast.Try) -> None:
            self.visit_block(node.body)
            for handler in node.handlers:
                self.visit_block(handler.body)
            self.visit_block(node.orelse)
            self.visit_block(node.finalbody)

        visit_TryStar = visit_Try

        def visit_Match(self, node: ast.Match) -> None:
            for case in node.cases:
                self.visit_block(case.body)

    Visitor().visit(function)
    return tuple(values)


def _structured_tool_target(
    module: _Module,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    call: ast.Call,
) -> DeepAgentFlowTarget | None:
    if not (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "from_function"
        and isinstance(call.func.value, ast.Name)
    ):
        return None
    if _function_binds_name(function, call.func.value.id):
        return None
    ref = module.imports.get(call.func.value.id)
    if not (ref and ref.name == "StructuredTool" and ref.module == "langchain_core.tools"):
        return None
    name_expr = _call_keyword(call, "name")
    name = _literal_string(name_expr)
    if not name and isinstance(name_expr, ast.Name):
        name = _module_string_constant(module, name_expr.id)
    if not name:
        return None
    return DeepAgentFlowTarget(
        "TOOL",
        canonicalize_text(f"langchain:tool:{name}"),
        name,
        module.path,
        call.lineno,
        (("tool_type", "structured_tool"),),
    )


def _structured_tool_comprehension_targets(
    module: _Module,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    node: ast.ListComp,
) -> tuple[DeepAgentFlowTarget, ...]:
    if len(node.generators) != 1 or node.generators[0].ifs:
        return ()
    generator = node.generators[0]
    if not (
        isinstance(generator.target, ast.Name)
        and isinstance(generator.iter, ast.Name)
        and isinstance(node.elt, ast.Call)
        and isinstance(node.elt.func, ast.Attribute)
        and node.elt.func.attr == "from_function"
        and isinstance(node.elt.func.value, ast.Name)
    ):
        return ()
    if _function_binds_name(function, node.elt.func.value.id):
        return ()
    ref = module.imports.get(node.elt.func.value.id)
    if not (ref and ref.module == "langchain_core.tools" and ref.name == "StructuredTool"):
        return ()
    name_expr = _call_keyword(node.elt, "name")
    callable_expr = _call_keyword(node.elt, "coroutine") or _call_keyword(node.elt, "func")
    if not (
        isinstance(name_expr, ast.Attribute)
        and isinstance(name_expr.value, ast.Name)
        and name_expr.value.id == generator.target.id
        and name_expr.attr == "__name__"
        and isinstance(callable_expr, ast.Name)
        and callable_expr.id == generator.target.id
    ):
        return ()
    assignment = _single_function_assignment(function, generator.iter.id)
    if not isinstance(assignment, (ast.Tuple, ast.List)):
        return ()
    nested_functions = {
        statement.name: statement
        for statement in function.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    targets: list[DeepAgentFlowTarget] = []
    for item in assignment.elts:
        if not isinstance(item, ast.Name) or item.id not in nested_functions:
            return ()
        definition = nested_functions[item.id]
        targets.append(
            DeepAgentFlowTarget(
                "TOOL",
                canonicalize_text(f"langchain:tool:{item.id}"),
                item.id,
                module.path,
                definition.lineno,
                (("tool_type", "structured_tool"),),
            )
        )
    return tuple(targets)


def _module_string_constant(module: _Module, name: str) -> str | None:
    if name in module.imports:
        return None
    values: list[ast.expr] = []
    for statement in module.tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in statement.targets
        ):
            values.append(statement.value)
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
            and statement.value is not None
        ):
            values.append(statement.value)
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        elif any(
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
            for node in ast.walk(statement)
        ):
            return None
    if len(values) != 1 or _name_write_count(module.tree, name) != 1:
        return None
    return _literal_string(values[0])


def _frozen_dataclass_tuple_field(
    module: _Module, constant: str, field_name: str
) -> tuple[str, ...]:
    values: list[ast.expr] = []
    for statement in module.tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == constant for target in statement.targets
        ):
            values.append(statement.value)
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == constant
            and statement.value is not None
        ):
            values.append(statement.value)
    if (
        len(values) != 1
        or _name_write_count(module.tree, constant) != 1
        or not isinstance(values[0], ast.Tuple)
    ):
        return ()
    names: list[str] = []
    expected_class: str | None = None
    for item in values[0].elts:
        if not isinstance(item, ast.Call) or not isinstance(item.func, ast.Name):
            return ()
        class_node = module.classes.get(item.func.id)
        if (
            class_node is None
            or item.func.id in module.imports
            or _name_write_count(module.tree, item.func.id) != 0
            or sum(
                isinstance(statement, ast.ClassDef) and statement.name == item.func.id
                for statement in module.tree.body
            )
            != 1
            or not _is_frozen_dataclass(module, class_node)
        ):
            return ()
        if expected_class is None:
            expected_class = item.func.id
        elif expected_class != item.func.id:
            return ()
        fields = [
            statement.target.id
            for statement in class_node.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        ]
        if field_name not in fields:
            return ()
        index = fields.index(field_name)
        keywords = {kw.arg: kw.value for kw in item.keywords if kw.arg}
        value = keywords.get(field_name)
        if value is None and index < len(item.args):
            value = item.args[index]
        name = _literal_string(value)
        if not name:
            return ()
        names.append(name)
    return tuple(names)


def _name_write_count(tree: ast.AST, name: str) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == name
        and isinstance(node.ctx, (ast.Store, ast.Del))
    )


def _is_frozen_dataclass(module: _Module, node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call) and _is_dataclass_decorator(module, decorator.func):
            frozen = _call_keyword(decorator, "frozen")
            return isinstance(frozen, ast.Constant) and frozen.value is True
    return False


def _is_dataclass_decorator(module: _Module, expr: ast.expr) -> bool:
    if isinstance(expr, ast.Call):
        return _is_dataclass_decorator(module, expr.func)
    if isinstance(expr, ast.Name):
        ref = module.imports.get(expr.id)
        return bool(ref and ref.module == "dataclasses" and ref.name == "dataclass")
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        ref = module.imports.get(expr.value.id)
        return bool(
            ref and ref.module == "dataclasses" and ref.name is None and expr.attr == "dataclass"
        )
    return False


def _call_terminal(expr: ast.expr) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return ""


def _literal_string(expr: ast.expr | None) -> str | None:
    return expr.value if isinstance(expr, ast.Constant) and isinstance(expr.value, str) else None


def _builtin_is_live(
    module: _Module, function: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> bool:
    if name in module.imports or name in module.classes or name in module.functions:
        return False
    return not _function_binds_name(function, name)


def _function_binds_name(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    arguments = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
    if any(argument.arg == name for argument in arguments):
        return True
    if function.args.vararg and function.args.vararg.arg == name:
        return True
    if function.args.kwarg and function.args.kwarg.arg == name:
        return True
    bound = False

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nonlocal bound
            if node is function:
                self.generic_visit(node)
            elif node.name == name:
                bound = True

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            nonlocal bound
            if node is function:
                self.generic_visit(node)
            elif node.name == name:
                bound = True

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            nonlocal bound
            if node.name == name:
                bound = True

        def visit_Name(self, node: ast.Name) -> None:
            nonlocal bound
            if node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound = True

        def visit_Import(self, node: ast.Import) -> None:
            nonlocal bound
            if any((alias.asname or alias.name.split(".")[0]) == name for alias in node.names):
                bound = True

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            nonlocal bound
            if any((alias.asname or alias.name) == name for alias in node.names):
                bound = True

    Visitor().visit(function)
    return bound


def _dedup(targets: object) -> tuple[DeepAgentFlowTarget, ...]:
    result: list[DeepAgentFlowTarget] = []
    seen: set[tuple[str, str]] = set()
    for target in targets:  # type: ignore[union-attr]
        key = (target.component_type, target.canonical_name)
        if key not in seen:
            seen.add(key)
            result.append(target)
    return tuple(result)
