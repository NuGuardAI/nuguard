"""Python AST-based parser for extracting semantic information from source files.

Uses only stdlib ``ast`` — no external dependencies required.
Extracts imports, class instantiations, function calls, and string literals
to provide rich context for framework-specific adapters.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedImport:
    module: str  # e.g. "langgraph.graph" or "openai"
    names: list[str]  # e.g. ["StateGraph"] for `from X import Y`
    alias: str | None  # import X as Y -> Y
    line: int


@dataclass
class ParsedInstantiation:
    class_name: str
    args: dict[str, Any]  # keyword arguments (string/int values resolved)
    positional_args: list[Any]  # positional arguments
    assigned_to: str | None  # variable the result is assigned to
    line: int
    line_end: int
    # Enclosing function/class name (mirrors ParsedStringLiteral.context) — set
    # for `return SomeClass(...)` inside a factory function so adapters can
    # name the produced node after the factory when no other identity is
    # available (e.g. the class is built and returned, not assigned to a var).
    context: str | None = None
    arg_locations: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    positional_arg_locations: dict[int, tuple[int, int, int, int]] = field(default_factory=dict)
    nested_arg_locations: dict[tuple[str | int, ...], tuple[int, int, int, int]] = field(
        default_factory=dict
    )


@dataclass
class ParsedCall:
    function_name: str  # e.g. "add_node"
    receiver: str | None  # e.g. "workflow" in `workflow.add_node(...)`
    args: dict[str, Any]
    positional_args: list[Any]
    assigned_to: str | None
    line: int
    line_end: int
    # Enclosing function/class name — see ParsedInstantiation.context above.
    context: str | None = None
    arg_locations: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    positional_arg_locations: dict[int, tuple[int, int, int, int]] = field(default_factory=dict)
    nested_arg_locations: dict[tuple[str | int, ...], tuple[int, int, int, int]] = field(
        default_factory=dict
    )


@dataclass
class ParsedStringLiteral:
    value: str
    line: int
    context: str | None  # enclosing function/class name
    is_docstring: bool
    # True when this literal was captured from a module-level assignment
    # (visit_Assign with empty scope_stack), False for function-body literals
    # whose context is the enclosing func/class name, not the variable name.
    is_module_assignment: bool = False
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None


@dataclass
class ParseResult:
    imports: list[ParsedImport] = field(default_factory=list)
    instantiations: list[ParsedInstantiation] = field(default_factory=list)
    function_calls: list[ParsedCall] = field(default_factory=list)
    string_literals: list[ParsedStringLiteral] = field(default_factory=list)
    source: str = ""
    parse_error: str | None = None
    # Variable names of Agent instances that are invoked inside @input_guardrail functions
    guardrail_agent_vars: set[str] = field(default_factory=set)


class _AstExtractor(ast.NodeVisitor):
    """Walk an AST tree and collect structured extraction data."""

    def __init__(self, source: str) -> None:
        self.source = source
        self._source_lines = source.splitlines(keepends=True)
        self.imports: list[ParsedImport] = []
        self.instantiations: list[ParsedInstantiation] = []
        self.function_calls: list[ParsedCall] = []
        self.string_literals: list[ParsedStringLiteral] = []
        self._scope_stack: list[str] = []
        self._in_input_guardrail: bool = False
        self.guardrail_agent_vars: set[str] = set()

    def _codepoint_column(self, line: int, byte_column: int) -> int:
        """Convert Python AST's UTF-8 byte offset to a 1-based character column."""
        if not (1 <= line <= len(self._source_lines)):
            return byte_column + 1
        encoded = self._source_lines[line - 1].encode("utf-8")
        try:
            return len(encoded[:byte_column].decode("utf-8")) + 1
        except UnicodeDecodeError:
            return byte_column + 1

    def _string_location(self, node: ast.Constant) -> tuple[int, int, int, int]:
        end_line = getattr(node, "end_lineno", node.lineno)
        end_byte_column = getattr(node, "end_col_offset", node.col_offset + 1)
        return (
            node.lineno,
            self._codepoint_column(node.lineno, node.col_offset),
            end_line,
            self._codepoint_column(end_line, end_byte_column),
        )

    def _collect_string_locations(
        self,
        node: ast.expr,
        path: tuple[str | int, ...],
        locations: dict[tuple[str | int, ...], tuple[int, int, int, int]],
    ) -> None:
        """Record exact paths for string literals nested in call arguments."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            locations[path] = self._string_location(node)
        elif isinstance(node, (ast.List, ast.Tuple)):
            extracted_index = 0
            for item in node.elts:
                # ``_extract_value`` omits unsupported elements.  Mirror that
                # indexing here so a later literal keeps the same path in the
                # extracted list and in ``nested_arg_locations``.
                if not self._has_extractable_value(item):
                    continue
                self._collect_string_locations(item, (*path, extracted_index), locations)
                extracted_index += 1
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and isinstance(key.value, (str, int)):
                    self._collect_string_locations(value, (*path, key.value), locations)

    # ------------------------------------------------------------------
    # Import handling
    # ------------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(
                ParsedImport(
                    module=alias.name,
                    names=[],
                    alias=alias.asname,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        names = [alias.name for alias in node.names if alias.name and alias.name != "*"]
        self.imports.append(
            ParsedImport(
                module=module,
                names=names,
                alias=None,
                line=node.lineno,
            )
        )
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Scope tracking
    # ------------------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Capture decorators — both bare (@function_tool) and call-style (@function_tool(args))
        is_input_guardrail = False
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name):
                dname = decorator.id
                self.function_calls.append(
                    ParsedCall(
                        function_name=dname,
                        receiver=None,
                        args={},
                        positional_args=[],
                        assigned_to=node.name,
                        line=decorator.lineno,
                        line_end=decorator.lineno,
                    )
                )
                if dname == "input_guardrail":
                    is_input_guardrail = True
            elif isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
                # @decorator(keyword=value, ...) — e.g. @function_tool(name_override="foo")
                dname = decorator.func.id
                dargs: dict[str, Any] = {}
                for kw in decorator.keywords:
                    if kw.arg:
                        v = self._extract_value(kw.value)
                        if v is not None:
                            dargs[kw.arg] = v
                dpos = [
                    v for v in (self._extract_value(a) for a in decorator.args) if v is not None
                ]
                self.function_calls.append(
                    ParsedCall(
                        function_name=dname,
                        receiver=None,
                        args=dargs,
                        positional_args=dpos,
                        assigned_to=node.name,
                        line=decorator.lineno,
                        line_end=decorator.lineno,
                    )
                )
                if dname == "input_guardrail":
                    is_input_guardrail = True
            elif isinstance(decorator, ast.Attribute):
                # @obj.method — e.g. @app.entrypoint, @app.async_task
                receiver = decorator.value.id if isinstance(decorator.value, ast.Name) else None
                dname = decorator.attr
                self.function_calls.append(
                    ParsedCall(
                        function_name=dname,
                        receiver=receiver,
                        args={},
                        positional_args=[],
                        assigned_to=node.name,
                        line=decorator.lineno,
                        line_end=decorator.lineno,
                    )
                )
            elif isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                # @obj.method(args) — e.g. @app.route("/path"), @agent.tool(description="…")
                recv_node = decorator.func.value
                receiver = recv_node.id if isinstance(recv_node, ast.Name) else None
                dname = decorator.func.attr
                dargs2: dict[str, Any] = {}
                for kw in decorator.keywords:
                    if kw.arg:
                        v = self._extract_value(kw.value)
                        if v is not None:
                            dargs2[kw.arg] = v
                dpos2 = [
                    v for v in (self._extract_value(a) for a in decorator.args) if v is not None
                ]
                self.function_calls.append(
                    ParsedCall(
                        function_name=dname,
                        receiver=receiver,
                        args=dargs2,
                        positional_args=dpos2,
                        assigned_to=node.name,
                        line=decorator.lineno,
                        line_end=decorator.lineno,
                    )
                )

        prev_guardrail = self._in_input_guardrail
        self._in_input_guardrail = is_input_guardrail or self._in_input_guardrail
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()
        self._in_input_guardrail = prev_guardrail

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Return(self, node: ast.Return) -> None:
        """Capture class instantiations and f-strings returned from functions.

        Handles two patterns:
        - ``return SomeClass(...)`` → records instantiation so framework adapters
          (e.g. CrewAI) can detect agents defined in factory methods such as
          ``def _create_agent(self): return Agent(role=...)``.
        - ``return f"..."`` → records large f-string as a string literal for
          prompt-factory function detection.
        """
        if node.value:
            if isinstance(node.value, ast.Call):
                self._visit_call(node.value, assigned_to=None)
            elif isinstance(node.value, ast.JoinedStr):
                parts: list[str] = []
                for part in node.value.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        parts.append(part.value)
                    else:
                        parts.append("{\u2026}")
                text = "".join(parts)
                if len(text) >= 100:
                    self.string_literals.append(
                        ParsedStringLiteral(
                            value=text,
                            line=node.lineno,
                            context=self._scope_stack[-1] if self._scope_stack else None,
                            is_docstring=False,
                        )
                    )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    # ------------------------------------------------------------------
    # Assignment + call handling
    # ------------------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        assigned_to: str | None = None
        if len(node.targets) == 1:
            assigned_to = self._get_name(node.targets[0])
        if isinstance(node.value, ast.Call):
            self._visit_call(node.value, assigned_to=assigned_to)
        elif isinstance(node.value, ast.Await) and isinstance(node.value.value, ast.Call):
            # Handle `result = await Runner.run(...)` patterns
            self._visit_call(node.value.value, assigned_to=assigned_to)
        elif isinstance(node.value, ast.List):
            # List assignment: visit each element to capture class instantiations.
            # e.g. `tools = [ToolA(), ToolB()]` → ToolA and ToolB register as instantiations
            # so framework adapters can detect them without needing an explicit assigned_to.
            for elt in node.value.elts:
                if isinstance(elt, ast.Call):
                    self._visit_call(elt, assigned_to=None)
        elif (
            isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and assigned_to
            and not self._scope_stack  # module-level only
        ):
            # Capture module-level string constants with the variable name as context
            # so adapters can find them by name (e.g. BILLING_INSTRUCTIONS = "...")
            val = node.value.value
            # Always capture ALL_CAPS constants (e.g. MODEL_ID, BUCKET_NAME, API_KEY);
            # otherwise require 40+ chars to avoid low-signal noise.
            is_constant_name = bool(assigned_to and re.match(r"^[A-Z][A-Z0-9_]{1,}$", assigned_to))
            if is_constant_name or len(val) >= 40:
                self.string_literals.append(
                    ParsedStringLiteral(
                        value=val,
                        line=node.value.lineno,
                        context=assigned_to,
                        is_docstring=False,
                        is_module_assignment=True,
                        column=self._string_location(node.value)[1],
                        end_line=self._string_location(node.value)[2],
                        end_column=self._string_location(node.value)[3],
                    )
                )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        assigned_to = self._get_name(node.target) if node.target else None
        if node.value and isinstance(node.value, ast.Call):
            self._visit_call(node.value, assigned_to=assigned_to)
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Call):
            self._visit_call(node.value, assigned_to=None)
        elif isinstance(node.value, ast.Await) and isinstance(node.value.value, ast.Call):
            # Handle `await some_call(...)` — e.g. `await Runner.run(...)`
            self._visit_call(node.value.value, assigned_to=None)
        elif isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            # Module-level or function-level docstrings
            value = node.value.value
            if len(value) >= 40:
                self.string_literals.append(
                    ParsedStringLiteral(
                        value=value,
                        line=node.value.lineno,
                        context=self._scope_stack[-1] if self._scope_stack else None,
                        is_docstring=True,
                    )
                )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Catch string literals that appear inside expressions
        # (e.g. assigned to variables, passed as keyword args)
        # Only capture non-trivial ones not already captured as docstrings.
        if isinstance(node.value, str) and len(node.value) >= 40:
            self.string_literals.append(
                ParsedStringLiteral(
                    value=node.value,
                    line=node.lineno,
                    context=self._scope_stack[-1] if self._scope_stack else None,
                    is_docstring=False,
                    column=self._string_location(node)[1],
                    end_line=self._string_location(node)[2],
                    end_column=self._string_location(node)[3],
                )
            )

    # ------------------------------------------------------------------
    # Core call dispatch
    # ------------------------------------------------------------------

    # Fluent builder methods that wrap a core call without changing identity.
    # When `graph = builder.compile(...).with_config(...)`, propagate `assigned_to`
    # through with_config so that compile() records `assigned_to="graph"`.
    _FLUENT_PASS_THROUGH = frozenset(
        {
            "with_config",
            "with_retry",
            "with_fallbacks",
            "bind_tools",
            "bind",
            "configurable_fields",
            "configurable_alternatives",
        }
    )

    def _visit_call(self, node: ast.Call, assigned_to: str | None) -> None:
        func_name = self._get_call_name(node)
        if not func_name:
            return

        # Propagate assigned_to through fluent wrapper methods so that the core
        # call (e.g. compile()) records the outer variable name.
        # e.g. graph = builder.compile(name=...).with_config({...})
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Call)
            and func_name.split(".")[-1] in self._FLUENT_PASS_THROUGH
        ):
            self._visit_call(node.func.value, assigned_to=assigned_to)

        receiver = self._get_receiver(node)
        positional: list[Any] = []
        positional_arg_locations: dict[int, tuple[int, int, int, int]] = {}
        nested_arg_locations: dict[tuple[str | int, ...], tuple[int, int, int, int]] = {}
        for arg in node.args:
            value = self._extract_value(arg)
            if value is None:
                continue
            index = len(positional)
            positional.append(value)
            self._collect_string_locations(arg, (index,), nested_arg_locations)
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                positional_arg_locations[index] = self._string_location(arg)
        kwargs: dict[str, Any] = {}
        arg_locations: dict[str, tuple[int, int, int, int]] = {}
        for kw in node.keywords:
            if kw.arg:
                v = self._extract_value(kw.value)
                if v is not None:
                    kwargs[kw.arg] = v
                    self._collect_string_locations(kw.value, (kw.arg,), nested_arg_locations)
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        arg_locations[kw.arg] = self._string_location(kw.value)

        line = node.lineno
        line_end: int = getattr(node, "end_lineno", line)

        # Track variables passed as first arg to Runner.run() inside @input_guardrail functions
        if self._in_input_guardrail and func_name.split(".")[-1] == "run":
            for arg in node.args[:1]:
                if isinstance(arg, ast.Name):
                    self.guardrail_agent_vars.add(arg.id)

        context = self._scope_stack[-1] if self._scope_stack else None

        # Heuristic: Title-case top-level names are class instantiations
        top = func_name.split(".")[-1]
        if top and top[0].isupper():
            self.instantiations.append(
                ParsedInstantiation(
                    class_name=top,
                    args=kwargs,
                    positional_args=positional,
                    assigned_to=assigned_to,
                    line=line,
                    line_end=line_end,
                    arg_locations=arg_locations,
                    positional_arg_locations=positional_arg_locations,
                    nested_arg_locations=nested_arg_locations,
                    context=context,
                )
            )
        else:
            self.function_calls.append(
                ParsedCall(
                    function_name=func_name.split(".")[-1],
                    receiver=receiver,
                    args=kwargs,
                    positional_args=positional,
                    assigned_to=assigned_to,
                    line=line,
                    line_end=line_end,
                    arg_locations=arg_locations,
                    positional_arg_locations=positional_arg_locations,
                    nested_arg_locations=nested_arg_locations,
                    context=context,
                )
            )

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    def _get_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _get_call_name(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            # Return "receiver.method" to preserve chaining context
            receiver = self._get_receiver(node)
            if receiver:
                return f"{receiver}.{node.func.attr}"
            return node.func.attr
        if isinstance(node.func, ast.Subscript):
            # Handle Agent[T](...) — generic subscript syntax
            inner = node.func.value
            if isinstance(inner, ast.Name):
                return inner.id
            if isinstance(inner, ast.Attribute):
                obj = inner.value
                recv = obj.id if isinstance(obj, ast.Name) else getattr(obj, "attr", None)
                if recv:
                    return f"{recv}.{inner.attr}"
                return inner.attr
        return None

    def _get_receiver(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Attribute):
            obj = node.func.value
            if isinstance(obj, ast.Name):
                return obj.id
            if isinstance(obj, ast.Attribute):
                return obj.attr
        return None

    def _extract_value(self, node: ast.expr) -> Any:
        """Extract a simple value from an AST expression node."""
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple)):
            items = [self._extract_value(e) for e in node.elts]
            return [v for v in items if v is not None]
        if isinstance(node, ast.Dict):
            result: dict[Any, Any] = {}
            for key, value in zip(node.keys, node.values, strict=True):
                extracted_key = self._extract_dict_key(key) if key is not None else None
                extracted_value = self._extract_value(value)
                if extracted_key is not None and extracted_value is not None:
                    result[extracted_key] = extracted_value
            return result
        if isinstance(node, ast.Name):
            return f"${node.id}"  # Variable reference marker
        if isinstance(node, ast.Attribute):
            return f"${node.attr}"
        if isinstance(node, ast.Call):
            # Also visit the nested call so it gets recorded as instantiation/call
            self._visit_call(node, assigned_to=None)
            name = self._get_call_name(node)
            return f"${name}" if name else None
        if isinstance(node, ast.JoinedStr):
            # F-string: extract static text parts, replacing {expr} with {…}
            parts: list[str] = []
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    parts.append(part.value)
                else:
                    parts.append("{…}")
            text = "".join(parts)
            return text if text.strip() else None
        return None

    def _extract_dict_key(self, node: ast.expr) -> Any:
        """Extract a hashable dict key, preserving literal tuple semantics."""
        if isinstance(node, ast.Tuple):
            items: list[Any] = []
            for item in node.elts:
                value = (
                    self._extract_dict_key(item)
                    if isinstance(item, ast.Tuple)
                    else self._extract_value(item)
                )
                if value is None and not (
                    isinstance(item, ast.Constant) and item.value is None
                ):
                    return None
                items.append(value)
            extracted: Any = tuple(items)
        else:
            extracted = self._extract_value(node)
        if extracted is None:
            return None
        try:
            hash(extracted)
        except TypeError:
            return None
        return extracted

    def _has_extractable_value(self, node: ast.expr) -> bool:
        """Pure counterpart to ``_extract_value`` for collection indexing."""
        if isinstance(node, ast.Constant):
            return node.value is not None
        if isinstance(node, (ast.List, ast.Tuple, ast.Dict, ast.Name, ast.Attribute)):
            return True
        if isinstance(node, ast.Call):
            return self._get_call_name(node) is not None
        if isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
                else "{…}"
                for part in node.values
            )
            return bool(text.strip())
        return False


def parse(source: str) -> ParseResult:
    """Parse a Python source string and return structured extraction data.

    Falls back gracefully if the source is not valid Python.
    """
    result = ParseResult(source=source)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        result.parse_error = str(exc)
        return result

    extractor = _AstExtractor(source)
    extractor.visit(tree)

    result.imports = extractor.imports
    result.instantiations = extractor.instantiations
    result.function_calls = extractor.function_calls
    result.guardrail_agent_vars = extractor.guardrail_agent_vars

    # De-duplicate string literals (visit_Constant fires for every node,
    # including those already captured by visit_Expr for docstrings).
    seen: set[tuple[Any, ...]] = set()
    for lit in extractor.string_literals:
        # Exact spans distinguish repeated same-line bindings.  Context is a
        # fallback only for legacy/synthetic literals that lack column data.
        key = (
            lit.line,
            lit.column,
            lit.end_line,
            lit.end_column,
            lit.value,
            lit.context if lit.column is None else None,
        )
        if key not in seen:
            seen.add(key)
            result.string_literals.append(lit)

    return result
