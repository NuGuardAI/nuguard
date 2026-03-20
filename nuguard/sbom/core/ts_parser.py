"""
TypeScript/JavaScript AST Parsing Infrastructure for AIBOM Extraction

Provides TypeScript and JavaScript code parsing using tree-sitter for
accurate AST-based extraction with regex fallback when tree-sitter is unavailable.

Extracts structured information for AI asset detection:
- Import statements (ES6 and CommonJS)
- Class definitions and instantiations
- Function calls with arguments
- Object literal configurations
- String literals (for prompt detection)

This module mirrors the Python ast_parser.py structure for consistency.
"""

import re
import structlog
from typing import List, Optional, Dict, Any, Set, Tuple
from dataclasses import dataclass, field
from pathlib import Path

logger = structlog.get_logger()

# Try to import tree-sitter for better parsing
try:
    import tree_sitter
    import tree_sitter_javascript as tsjs
    import tree_sitter_typescript as tsts

    HAS_TREE_SITTER = True
#    logger.info("tree-sitter available for TypeScript/JavaScript parsing")
except ImportError:
    HAS_TREE_SITTER = False
    logger.info("tree-sitter not available, using regex-based TypeScript parser")


# =============================================================================
# Data Classes (mirroring Python ast_parser.py)
# =============================================================================


@dataclass
class TSImportInfo:
    """Information about a TypeScript/JavaScript import statement"""

    module: str  # e.g., "openai", "@langchain/core"
    names: List[str]  # e.g., ["Agent", "OpenAI"]
    default_import: Optional[str] = None  # Default import name
    namespace_import: Optional[str] = None  # e.g., "* as openai"
    is_require: bool = False  # True for CommonJS require()
    is_dynamic: bool = False  # True for dynamic import()
    line_number: int = 0

    def full_path(self, name: str) -> str:
        """Get full import path for a name"""
        return f"{self.module}/{name}"


@dataclass
class TSClassInstantiation:
    """Information about a class instantiation (new ClassName())"""

    class_name: str
    import_path: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    positional_args: List[Any] = field(default_factory=list)
    resolved_arguments: Dict[str, Any] = field(default_factory=dict)
    type_annotation: Optional[str] = None
    line_start: int = 0
    line_end: int = 0
    source_snippet: Optional[str] = None


@dataclass
class TSFunctionCall:
    """Information about a function call"""

    function_name: str
    import_path: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    positional_args: List[Any] = field(default_factory=list)
    resolved_arguments: Dict[str, Any] = field(default_factory=dict)
    receiver: Optional[str] = None  # Object the method is called on
    receiver_chain: List[str] = field(default_factory=list)
    method_name: Optional[str] = None
    type_annotation: Optional[str] = None
    is_method_call: bool = False  # True for obj.method()
    line_start: int = 0
    line_end: int = 0
    source_snippet: Optional[str] = None


@dataclass
class TSObjectLiteral:
    """Information about an object literal (for config detection)"""

    variable_name: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    is_exported: bool = False
    is_const: bool = False
    line_start: int = 0
    line_end: int = 0


@dataclass
class TSStringLiteral:
    """Information about a string literal (for prompt detection)"""

    value: str
    is_template: bool = False  # Template literal with backticks
    has_interpolation: bool = False  # Contains ${...}
    context: Optional[str] = None  # Variable it's assigned to
    line_number: int = 0
    char_count: int = 0
    enclosing_function: Optional[str] = None  # Nearest enclosing function/method name

    @property
    def is_potential_prompt(self) -> bool:
        """Heuristic: long strings are likely prompts"""
        return self.char_count > 100 or any(
            keyword in self.value.lower()
            for keyword in ["you are", "system:", "assistant:", "user:", "instructions:"]
        )


@dataclass
class TSDecoratedItem:
    """Information about a decorated class or method (TypeScript decorators)"""

    item_name: str
    item_type: str  # "class" or "method"
    decorators: List[str]
    decorator_args: Dict[str, Any] = field(default_factory=dict)
    line_start: int = 0
    line_end: int = 0


@dataclass
class TSSymbolEntry:
    """A single variable/constant binding tracked in the symbol table."""

    name: str
    value: Any = None  # Resolved literal value (str, int, float, bool, dict, list)
    raw_value: str = ""  # Original source text of the RHS
    scope: str = "module"  # "module", "class:Name", "function:name"
    kind: str = "const"  # "const", "let", "var", "field", "parameter"
    type_annotation: Optional[str] = None
    line_number: int = 0


@dataclass
class TSSymbolTable:
    """Symbol table for variable resolution built from AST."""

    entries: Dict[str, TSSymbolEntry] = field(default_factory=dict)
    this_attrs: Dict[str, TSSymbolEntry] = field(default_factory=dict)
    _object_entries: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    _MAX_RESOLVE_DEPTH = 10

    def resolve(self, identifier: str) -> Optional[str]:
        """Resolve an identifier to its string value, following reference chains."""
        if not identifier:
            return None
        # Handle this.x
        if identifier.startswith("this."):
            attr = identifier[5:]
            entry = self.this_attrs.get(attr)
            if entry and isinstance(entry.value, str):
                return self._follow_chain(entry.value, 0)
            return None
        return self._follow_chain(identifier, 0)

    def _follow_chain(self, value: str, depth: int) -> Optional[str]:
        if depth > self._MAX_RESOLVE_DEPTH:
            return None
        # Already a literal (contains quotes or looks like a real value)
        stripped = value.strip().strip("'\"`")
        if stripped != value.strip():
            return stripped
        # Look up as identifier
        entry = self.entries.get(value)
        if not entry:
            return value if not re.match(r"^[A-Za-z_]\w*$", value) else None
        if isinstance(entry.value, str):
            inner = entry.value.strip().strip("'\"`")
            if inner != entry.value.strip():
                return inner
            # Reference to another symbol
            if re.match(r"^[A-Za-z_]\w*$", inner):
                return self._follow_chain(inner, depth + 1)
            return inner
        if isinstance(entry.value, (int, float, bool)):
            return str(entry.value)
        return None

    def resolve_object(self, identifier: str) -> Dict[str, Any]:
        """Resolve an identifier to an object literal's properties."""
        obj = self._object_entries.get(identifier)
        if obj:
            return dict(obj)
        entry = self.entries.get(identifier)
        if entry and isinstance(entry.value, dict):
            return dict(entry.value)
        return {}


@dataclass
class TSArrayLiteral:
    """Information about an array literal assignment."""

    variable_name: Optional[str] = None
    elements: List[Any] = field(default_factory=list)
    line_start: int = 0
    line_end: int = 0


@dataclass
@dataclass
class TSJSDocComment:
    """A JSDoc/TSDoc block comment extracted from source."""

    text: str
    line_start: int
    line_end: int
    tags: Dict[str, str] = field(default_factory=dict)  # @param, @returns, etc.


@dataclass
class TSParseResult:
    """Result of parsing a TypeScript/JavaScript file"""

    imports: List[TSImportInfo] = field(default_factory=list)
    instantiations: List[TSClassInstantiation] = field(default_factory=list)
    function_calls: List[TSFunctionCall] = field(default_factory=list)
    object_literals: List[TSObjectLiteral] = field(default_factory=list)
    string_literals: List[TSStringLiteral] = field(default_factory=list)
    decorated_items: List[TSDecoratedItem] = field(default_factory=list)
    array_literals: List[TSArrayLiteral] = field(default_factory=list)
    jsdoc_comments: List[TSJSDocComment] = field(default_factory=list)
    symbol_table: TSSymbolTable = field(default_factory=TSSymbolTable)
    errors: List[str] = field(default_factory=list)

    # Optional: file path and source for reference (matches ParseResult interface)
    file_path: Optional[str] = None
    source: Optional[str] = None

    def __bool__(self) -> bool:
        return bool(
            self.imports
            or self.instantiations
            or self.function_calls
            or self.object_literals
            or self.string_literals
        )


# =============================================================================
# Regex Patterns for TypeScript/JavaScript
# =============================================================================

# ES6 import patterns
IMPORT_PATTERNS = {
    # import { X, Y } from 'module'
    "named_import": re.compile(
        r"import\s*\{\s*([^}]+)\s*\}\s*from\s*['\"]([^'\"]+)['\"]", re.MULTILINE
    ),
    # import X from 'module'
    "default_import": re.compile(r"import\s+(\w+)\s+from\s*['\"]([^'\"]+)['\"]", re.MULTILINE),
    # import * as X from 'module'
    "namespace_import": re.compile(
        r"import\s*\*\s*as\s+(\w+)\s+from\s*['\"]([^'\"]+)['\"]", re.MULTILINE
    ),
    # const X = require('module')
    "require": re.compile(
        r"(?:const|let|var)\s+(\w+)\s*=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", re.MULTILINE
    ),
    # const { X, Y } = require('module')
    "require_destructure": re.compile(
        r"(?:const|let|var)\s*\{\s*([^}]+)\s*\}\s*=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
        re.MULTILINE,
    ),
}

# Class instantiation pattern: new ClassName(...)
NEW_INSTANCE_PATTERN = re.compile(r"new\s+(\w+)\s*\(", re.MULTILINE)

# Function call patterns
FUNCTION_CALL_PATTERN = re.compile(r"(\w+(?:\.\w+)*)\s*\(", re.MULTILINE)

# Object literal assignment: const config = { ... }
OBJECT_LITERAL_PATTERN = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*\{", re.MULTILINE
)

# Template literal (backtick strings)
TEMPLATE_LITERAL_PATTERN = re.compile(r"`([^`]*)`", re.DOTALL)

# Regular string literals
STRING_LITERAL_PATTERN = re.compile(
    r"['\"]([^'\"]{50,})['\"]",  # Strings longer than 50 chars
    re.MULTILINE,
)


# =============================================================================
# TypeScript Parser Implementation
# =============================================================================


class TypeScriptParser:
    """
    Parser for TypeScript and JavaScript files.

    Uses tree-sitter for accurate AST-based extraction when available,
    with regex fallback for environments without tree-sitter.
    """

    def __init__(self) -> None:
        self._use_tree_sitter = HAS_TREE_SITTER
        self._js_parser: Optional[Any] = None
        self._ts_parser: Optional[Any] = None
        self._tsx_parser: Optional[Any] = None

        if self._use_tree_sitter:
            self._init_tree_sitter()

    def _init_tree_sitter(self) -> None:
        """Initialize tree-sitter parsers for JS/TS"""
        try:
            # JavaScript parser
            js_lang = tree_sitter.Language(tsjs.language())
            self._js_parser = tree_sitter.Parser(js_lang)

            # TypeScript parser
            ts_lang = tree_sitter.Language(tsts.language_typescript())
            self._ts_parser = tree_sitter.Parser(ts_lang)

            # TSX parser
            tsx_lang = tree_sitter.Language(tsts.language_tsx())
            self._tsx_parser = tree_sitter.Parser(tsx_lang)

            logger.debug("tree_sitter_parsers_initialized")
        except Exception as e:
            logger.warning("tree_sitter_init_failed", error=str(e))
            self._use_tree_sitter = False

    def _get_parser_for_file(self, file_path: Optional[str]) -> Optional[Any]:
        """Get appropriate parser based on file extension"""
        if not self._use_tree_sitter:
            return None

        if file_path:
            ext = Path(file_path).suffix.lower()
            if ext == ".tsx":
                return self._tsx_parser
            elif ext == ".ts":
                return self._ts_parser
            elif ext in (".js", ".jsx", ".mjs", ".cjs"):
                return self._js_parser

        # Default to TypeScript (superset of JS)
        return self._ts_parser

    def parse(self, source: str, file_path: Optional[str] = None) -> TSParseResult:
        """
        Parse TypeScript/JavaScript source code.

        Args:
            source: Source code string
            file_path: Optional file path for context

        Returns:
            TSParseResult with extracted information
        """
        result = TSParseResult()
        result.file_path = file_path
        result.source = source

        try:
            parser = self._get_parser_for_file(file_path)

            if parser and self._use_tree_sitter:
                # Use tree-sitter for accurate parsing
                result = self._parse_with_tree_sitter(source, parser, file_path)
                result.file_path = file_path
                result.source = source
            else:
                # Fallback to regex
                result = self._parse_with_regex(source)
                result.file_path = file_path
                result.source = source

        except Exception as e:
            result.errors.append(f"Parse error: {str(e)}")
            logger.error("ts_parse_error", error=str(e), file=file_path)

        # Extract JSDoc/TSDoc comments (works regardless of parser mode)
        result.jsdoc_comments = self._extract_jsdoc_comments(source)

        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_jsdoc_comments(source: str) -> List[TSJSDocComment]:
        """Extract JSDoc/TSDoc block comments (/** ... */) from source."""
        comments: List[TSJSDocComment] = []
        pattern = re.compile(r"/\*\*(.*?)\*/", re.DOTALL)
        for m in pattern.finditer(source):
            raw = m.group(1)
            # Clean leading * from each line
            lines = [ln.strip().lstrip("*").strip() for ln in raw.splitlines()]
            text = "\n".join(ln for ln in lines if ln).strip()
            if not text:
                continue

            line_start = source[: m.start()].count("\n") + 1
            line_end = source[: m.end()].count("\n") + 1

            # Extract @tags
            tags: Dict[str, str] = {}
            for tag_match in re.finditer(r"@(\w+)\s+(.*?)(?=@\w|\Z)", text, re.DOTALL):
                tags[tag_match.group(1)] = tag_match.group(2).strip()

            comments.append(
                TSJSDocComment(
                    text=text,
                    line_start=line_start,
                    line_end=line_end,
                    tags=tags,
                )
            )
        return comments

    def _parse_with_tree_sitter(
        self, source: str, parser: Any, file_path: Optional[str] = None
    ) -> TSParseResult:
        """Parse using tree-sitter AST"""
        result = TSParseResult()

        try:
            tree = parser.parse(source.encode("utf-8"))
            root = tree.root_node

            # Extract imports
            result.imports = self._ts_extract_imports(root, source)

            # Extract class instantiations
            result.instantiations = self._ts_extract_instantiations(root, source)

            # Extract function calls
            result.function_calls = self._ts_extract_function_calls(root, source)

            # Extract object literals
            result.object_literals = self._ts_extract_object_literals(root, source)

            # Extract string literals
            result.string_literals = self._ts_extract_string_literals(root, source)

            # Extract decorators
            result.decorated_items = self._ts_extract_decorators(root, source)

            # Extract array literals
            result.array_literals = self._ts_extract_array_literals(root, source)

            # Build symbol table
            result.symbol_table = self._ts_build_symbol_table(root, source, result.object_literals)

            # Post-processing: resolve arguments and decompose method chains
            self._resolve_all_arguments(result)
            self._decompose_method_chains(result)

        except Exception as e:
            result.errors.append(f"Tree-sitter parse error: {str(e)}")
            logger.warning("tree_sitter_parse_error", error=str(e), file=file_path)
            # Fallback to regex on error
            return self._parse_with_regex(source)

        return result

    def _parse_with_regex(self, source: str) -> TSParseResult:
        """Parse using regex patterns (fallback)"""
        result = TSParseResult()

        result.imports = self._extract_imports(source)
        result.instantiations = self._extract_instantiations(source)
        result.function_calls = self._extract_function_calls(source)
        result.object_literals = self._extract_object_literals(source)
        result.string_literals = self._extract_string_literals(source)

        # Build basic symbol table from regex-extracted data
        result.symbol_table = self._build_regex_symbol_table(source, result.object_literals)

        # Post-processing (same as tree-sitter path)
        self._resolve_all_arguments(result)
        self._decompose_method_chains(result)

        return result

    def _build_regex_symbol_table(
        self, source: str, object_literals: List[TSObjectLiteral]
    ) -> TSSymbolTable:
        """Build a basic symbol table using regex patterns."""
        table = TSSymbolTable()
        for obj in object_literals:
            if obj.variable_name:
                table._object_entries[obj.variable_name] = obj.properties

        # const/let/var NAME = "value"
        for m in re.finditer(
            r'(?:const|let|var)\s+([A-Za-z_]\w*)\s*(?::[^=]+)?=\s*["\']([^"\']+)["\']',
            source,
        ):
            name, val = m.group(1), m.group(2)
            table.entries[name] = TSSymbolEntry(
                name=name,
                value=val,
                raw_value=m.group(0),
                scope="module",
                kind="const",
                line_number=source[: m.start()].count("\n") + 1,
            )

        # const/let/var NAME = number
        for m in re.finditer(
            r"(?:const|let|var)\s+([A-Za-z_]\w*)\s*(?::[^=]+)?=\s*(\d+(?:\.\d+)?)\s*[;\n]",
            source,
        ):
            name, val = m.group(1), m.group(2)
            table.entries[name] = TSSymbolEntry(
                name=name,
                value=float(val) if "." in val else int(val),
                raw_value=val,
                scope="module",
                kind="const",
                line_number=source[: m.start()].count("\n") + 1,
            )

        # this.NAME = "value"
        for m in re.finditer(
            r'this\.([A-Za-z_]\w*)\s*=\s*["\']([^"\']+)["\']',
            source,
        ):
            attr, val = m.group(1), m.group(2)
            table.this_attrs[attr] = TSSymbolEntry(
                name=attr,
                value=val,
                raw_value=m.group(0),
                scope="module",
                kind="field",
                line_number=source[: m.start()].count("\n") + 1,
            )

        # this.NAME = identifier (reference)
        for m in re.finditer(
            r"this\.([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*[;\n]",
            source,
        ):
            attr, ref = m.group(1), m.group(2)
            if attr not in table.this_attrs:
                table.this_attrs[attr] = TSSymbolEntry(
                    name=attr,
                    value=ref,
                    raw_value=ref,
                    scope="module",
                    kind="field",
                    line_number=source[: m.start()].count("\n") + 1,
                )

        return table

    # =========================================================================
    # Tree-sitter extraction methods
    # =========================================================================

    def _ts_extract_imports(self, root: Any, source: str) -> List[TSImportInfo]:
        """Extract imports using tree-sitter AST"""
        imports = []

        def visit(node: Any) -> None:
            # ES6 import statement
            if node.type == "import_statement":
                imports.append(self._parse_import_statement(node, source))

            # CommonJS require
            elif node.type == "call_expression":
                callee = node.child_by_field_name("function")
                if callee and self._get_node_text(callee, source) == "require":
                    imp = self._parse_require_call(node, source)
                    if imp:
                        imports.append(imp)

            # Recurse into children
            for child in node.children:
                visit(child)

        visit(root)
        return [i for i in imports if i is not None]

    def _parse_import_statement(self, node: Any, source: str) -> Optional[TSImportInfo]:
        """Parse an ES6 import statement node"""
        module = None
        names = []
        default_import = None
        namespace_import = None

        for child in node.children:
            if child.type == "string":
                # Remove quotes from module path
                module = self._get_node_text(child, source).strip("'\"")

            elif child.type == "import_clause":
                for clause_child in child.children:
                    if clause_child.type == "identifier":
                        default_import = self._get_node_text(clause_child, source)

                    elif clause_child.type == "named_imports":
                        for spec in clause_child.children:
                            if spec.type == "import_specifier":
                                name_node = spec.child_by_field_name("name")
                                if name_node:
                                    names.append(self._get_node_text(name_node, source))

                    elif clause_child.type == "namespace_import":
                        for ns_child in clause_child.children:
                            if ns_child.type == "identifier":
                                namespace_import = self._get_node_text(ns_child, source)

        if module:
            return TSImportInfo(
                module=module,
                names=names,
                default_import=default_import,
                namespace_import=namespace_import,
                line_number=node.start_point[0] + 1,
            )
        return None

    def _parse_require_call(self, node: Any, source: str) -> Optional[TSImportInfo]:
        """Parse a CommonJS require() call"""
        args = node.child_by_field_name("arguments")
        if not args:
            return None

        for arg in args.children:
            if arg.type == "string":
                module = self._get_node_text(arg, source).strip("'\"")

                # Try to find variable assignment
                parent = node.parent
                var_name = None
                if parent and parent.type == "variable_declarator":
                    name_node = parent.child_by_field_name("name")
                    if name_node:
                        if name_node.type == "identifier":
                            var_name = self._get_node_text(name_node, source)
                        elif name_node.type == "object_pattern":
                            # Destructured require
                            names = []
                            for prop in name_node.children:
                                if prop.type == "shorthand_property_identifier_pattern":
                                    names.append(self._get_node_text(prop, source))
                            return TSImportInfo(
                                module=module,
                                names=names,
                                is_require=True,
                                line_number=node.start_point[0] + 1,
                            )

                return TSImportInfo(
                    module=module,
                    names=[],
                    default_import=var_name,
                    is_require=True,
                    line_number=node.start_point[0] + 1,
                )
        return None

    def _ts_extract_instantiations(self, root: Any, source: str) -> List[TSClassInstantiation]:
        """Extract class instantiations using tree-sitter"""
        instantiations = []

        def visit(node: Any) -> None:
            if node.type == "new_expression":
                inst = self._parse_new_expression(node, source)
                if inst:
                    instantiations.append(inst)

            for child in node.children:
                visit(child)

        visit(root)
        return instantiations

    def _parse_new_expression(self, node: Any, source: str) -> Optional[TSClassInstantiation]:
        """Parse a new expression node"""
        constructor = node.child_by_field_name("constructor")
        if not constructor:
            return None

        class_name = self._get_node_text(constructor, source)

        # Skip common builtins
        if class_name in {"Date", "Array", "Map", "Set", "Promise", "Error", "RegExp", "Object"}:
            return None

        # Extract arguments
        args_node = node.child_by_field_name("arguments")
        arguments = {}
        positional_args = []

        if args_node:
            for i, arg in enumerate(args_node.children):
                if arg.type == "object":
                    arguments = self._extract_object_properties(arg, source)
                elif arg.type not in ("(", ")", ","):
                    positional_args.append(self._get_node_text(arg, source))

        # Extract type annotation from parent variable declarator
        type_annotation = None
        parent = node.parent
        if parent and parent.type == "variable_declarator":
            ta_node = parent.child_by_field_name("type")
            if ta_node:
                type_annotation = self._get_node_text(ta_node, source).lstrip(":").strip()

        return TSClassInstantiation(
            class_name=class_name,
            arguments=arguments,
            positional_args=positional_args,
            type_annotation=type_annotation,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            source_snippet=self._get_node_text(node, source)[:100],
        )

    def _ts_extract_function_calls(self, root: Any, source: str) -> List[TSFunctionCall]:
        """Extract function calls using tree-sitter"""
        function_calls = []
        seen: Set[Tuple[int, str]] = set()

        def visit(node: Any) -> None:
            if node.type == "call_expression":
                call = self._parse_call_expression(node, source)
                if call:
                    key = (call.line_start, call.function_name)
                    if key not in seen:
                        seen.add(key)
                        function_calls.append(call)

            for child in node.children:
                visit(child)

        visit(root)
        return function_calls

    def _parse_call_expression(self, node: Any, source: str) -> Optional[TSFunctionCall]:
        """Parse a function call expression"""
        func_node = node.child_by_field_name("function")
        if not func_node:
            return None

        func_name = self._get_node_text(func_node, source)

        # Skip require (handled in imports) and common keywords
        skip_list = {
            "require",
            "if",
            "for",
            "while",
            "switch",
            "function",
            "return",
            "throw",
            "catch",
        }
        if func_name in skip_list:
            return None

        is_method = func_node.type == "member_expression"
        receiver = None
        if is_method:
            obj_node = func_node.child_by_field_name("object")
            if obj_node:
                receiver = self._get_node_text(obj_node, source)

        # Extract arguments
        args_node = node.child_by_field_name("arguments")
        arguments = {}
        positional_args = []

        if args_node:
            for arg in args_node.children:
                if arg.type == "object":
                    arguments = self._extract_object_properties(arg, source)
                elif arg.type == "spread_element":
                    text = self._get_node_text(arg, source).strip()
                    if text.startswith("..."):
                        arguments.setdefault("__args_expanded__", []).append(f"${text[3:].strip()}")
                elif arg.type not in ("(", ")", ","):
                    positional_args.append(self._get_node_text(arg, source))

        return TSFunctionCall(
            function_name=func_name,
            receiver=receiver,
            is_method_call=is_method,
            arguments=arguments,
            positional_args=positional_args,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            source_snippet=self._get_node_text(node, source)[:100],
        )

    def _ts_extract_object_literals(self, root: Any, source: str) -> List[TSObjectLiteral]:
        """Extract object literal assignments"""
        objects = []

        def visit(node: Any) -> None:
            if node.type == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value_node = node.child_by_field_name("value")

                if name_node and value_node and value_node.type == "object":
                    var_name = self._get_node_text(name_node, source)
                    properties = self._extract_object_properties(value_node, source)

                    # Check if exported or const
                    parent = node.parent
                    is_exported = False
                    is_const = False
                    while parent:
                        if parent.type == "export_statement":
                            is_exported = True
                        if parent.type == "lexical_declaration":
                            # Check for const keyword
                            for child in parent.children:
                                if child.type == "const":
                                    is_const = True
                                    break
                            break
                        parent = parent.parent

                    objects.append(
                        TSObjectLiteral(
                            variable_name=var_name,
                            properties=properties,
                            is_exported=is_exported,
                            is_const=is_const,
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                        )
                    )

            for child in node.children:
                visit(child)

        visit(root)
        return objects

    def _ts_extract_string_literals(self, root: Any, source: str) -> List[TSStringLiteral]:
        """Extract string literals using tree-sitter with enhanced context capture"""
        strings = []

        def find_context(node: Any) -> Optional[str]:
            """Walk up the tree to find context for a string literal"""
            parent = node.parent
            while parent:
                # Variable declaration: const PROMPT = "..."
                if parent.type == "variable_declarator":
                    name_node = parent.child_by_field_name("name")
                    if name_node:
                        return self._get_node_text(name_node, source)

                # Property in object/pair: { systemPrompt: "..." }
                if parent.type == "pair":
                    key_node = parent.child_by_field_name("key")
                    if key_node:
                        return self._get_node_text(key_node, source).strip("'\"")

                # Assignment expression: this.prompt = "..." or obj.field = "..."
                if parent.type == "assignment_expression":
                    left = parent.child_by_field_name("left")
                    if left:
                        text = self._get_node_text(left, source)
                        # Extract last identifier (this.prompt -> prompt)
                        if "." in text:
                            return text.split(".")[-1]
                        return text

                # Function/method definition: function getPrompt() { return "..." }
                if parent.type in (
                    "function_declaration",
                    "method_definition",
                    "arrow_function",
                    "function",
                ):
                    name_node = parent.child_by_field_name("name")
                    if name_node:
                        return self._get_node_text(name_node, source)
                    # For arrow functions, check if assigned to variable
                    gparent = parent.parent
                    if gparent and gparent.type == "variable_declarator":
                        name_node = gparent.child_by_field_name("name")
                        if name_node:
                            return self._get_node_text(name_node, source)

                parent = parent.parent
            return None

        def visit(node: Any) -> None:
            if node.type in ("string", "template_string"):
                text = self._get_node_text(node, source)
                # Remove quotes
                if node.type == "string":
                    text = text[1:-1] if len(text) >= 2 else text
                elif node.type == "template_string":
                    text = text[1:-1] if len(text) >= 2 else text

                if len(text) > 50:  # Only capture long strings
                    context = find_context(node)

                    strings.append(
                        TSStringLiteral(
                            value=text[:500],
                            is_template=node.type == "template_string",
                            has_interpolation="${" in text,
                            context=context,
                            line_number=node.start_point[0] + 1,
                            char_count=len(text),
                        )
                    )

            for child in node.children:
                visit(child)

        visit(root)
        return strings

    def _extract_object_properties(self, node: Any, source: str) -> Dict[str, Any]:
        """Extract properties from an object literal node"""
        properties: Dict[str, Any] = {}

        for child in node.children:
            if child.type == "pair":
                key_node = child.child_by_field_name("key")
                value_node = child.child_by_field_name("value")

                if key_node and value_node:
                    key = self._get_node_text(key_node, source).strip("'\"")
                    value = self._get_node_text(value_node, source)

                    # Try to parse value
                    if value_node.type == "string":
                        properties[key] = value.strip("'\"")
                    elif value_node.type in ("true", "false"):
                        properties[key] = value == "true"
                    elif value_node.type == "number":
                        try:
                            properties[key] = float(value) if "." in value else int(value)
                        except ValueError:
                            properties[key] = value
                    else:
                        properties[key] = value

            elif child.type == "shorthand_property_identifier":
                name = self._get_node_text(child, source)
                properties[name] = name  # Reference to variable

        return properties

    def _get_node_text(self, node: Any, source: str) -> str:
        """Get the text content of a tree-sitter node"""
        return source[node.start_byte : node.end_byte]

    # =========================================================================
    # New tree-sitter extraction: symbols, arrays, decorators, type annotations
    # =========================================================================

    def _ts_build_symbol_table(
        self, root: Any, source: str, object_literals: List[TSObjectLiteral]
    ) -> TSSymbolTable:
        """Build a symbol table from variable declarations in the AST."""
        table = TSSymbolTable()

        # Index object literals by variable name
        for obj in object_literals:
            if obj.variable_name:
                table._object_entries[obj.variable_name] = obj.properties

        def _extract_type_annotation(node: Any) -> Optional[str]:
            """Get type annotation from a variable declarator's parent or sibling."""
            ta = node.child_by_field_name("type")
            if ta:
                return self._get_node_text(ta, source).lstrip(":").strip()
            return None

        def visit(node: Any, scope: str = "module") -> None:
            if node.type == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value_node = node.child_by_field_name("value")
                if name_node and value_node:
                    var_name = self._get_node_text(name_node, source)
                    raw = self._get_node_text(value_node, source)
                    ta = _extract_type_annotation(node)
                    # Determine kind from parent lexical_declaration
                    kind = "const"
                    parent = node.parent
                    if parent and parent.type == "lexical_declaration":
                        for ch in parent.children:
                            if ch.type in ("const", "let", "var"):
                                kind = ch.type
                                break

                    if value_node.type == "string":
                        val = raw.strip("'\"`")
                        table.entries[var_name] = TSSymbolEntry(
                            name=var_name,
                            value=val,
                            raw_value=raw,
                            scope=scope,
                            kind=kind,
                            type_annotation=ta,
                            line_number=node.start_point[0] + 1,
                        )
                    elif value_node.type == "template_string":
                        val = raw[1:-1] if len(raw) >= 2 else raw
                        table.entries[var_name] = TSSymbolEntry(
                            name=var_name,
                            value=val,
                            raw_value=raw,
                            scope=scope,
                            kind=kind,
                            type_annotation=ta,
                            line_number=node.start_point[0] + 1,
                        )
                    elif value_node.type == "number":
                        try:
                            num_val: Any = float(raw) if "." in raw else int(raw)
                        except ValueError:
                            num_val = raw
                        table.entries[var_name] = TSSymbolEntry(
                            name=var_name,
                            value=num_val,
                            raw_value=raw,
                            scope=scope,
                            kind=kind,
                            type_annotation=ta,
                            line_number=node.start_point[0] + 1,
                        )
                    elif value_node.type in ("true", "false"):
                        table.entries[var_name] = TSSymbolEntry(
                            name=var_name,
                            value=(raw == "true"),
                            raw_value=raw,
                            scope=scope,
                            kind=kind,
                            type_annotation=ta,
                            line_number=node.start_point[0] + 1,
                        )
                    elif value_node.type == "identifier":
                        # Reference to another symbol
                        table.entries[var_name] = TSSymbolEntry(
                            name=var_name,
                            value=raw,
                            raw_value=raw,
                            scope=scope,
                            kind=kind,
                            type_annotation=ta,
                            line_number=node.start_point[0] + 1,
                        )
                    elif value_node.type == "object":
                        props = self._extract_object_properties(value_node, source)
                        table.entries[var_name] = TSSymbolEntry(
                            name=var_name,
                            value=props,
                            raw_value=raw[:200],
                            scope=scope,
                            kind=kind,
                            type_annotation=ta,
                            line_number=node.start_point[0] + 1,
                        )
                        table._object_entries[var_name] = props

            # this.x = y assignment
            elif node.type == "assignment_expression":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left and right and left.type == "member_expression":
                    obj = left.child_by_field_name("object")
                    prop = left.child_by_field_name("property")
                    if obj and prop and self._get_node_text(obj, source) == "this":
                        attr_name = self._get_node_text(prop, source)
                        raw = self._get_node_text(right, source)
                        if right.type == "string":
                            val = raw.strip("'\"`")
                        elif right.type == "identifier":
                            val = raw
                        else:
                            val = raw
                        table.this_attrs[attr_name] = TSSymbolEntry(
                            name=attr_name,
                            value=val,
                            raw_value=raw,
                            scope=scope,
                            kind="field",
                            line_number=node.start_point[0] + 1,
                        )

            # Track scope changes
            child_scope = scope
            if node.type == "class_declaration":
                name_n = node.child_by_field_name("name")
                if name_n:
                    child_scope = f"class:{self._get_node_text(name_n, source)}"
            elif node.type in ("function_declaration", "method_definition"):
                name_n = node.child_by_field_name("name")
                if name_n:
                    child_scope = f"function:{self._get_node_text(name_n, source)}"

            for child in node.children:
                visit(child, child_scope)

        visit(root)
        return table

    def _ts_extract_array_literals(self, root: Any, source: str) -> List[TSArrayLiteral]:
        """Extract array literal assignments from the AST."""
        arrays: List[TSArrayLiteral] = []

        def visit(node: Any) -> None:
            if node.type == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value_node = node.child_by_field_name("value")
                if name_node and value_node and value_node.type == "array":
                    var_name = self._get_node_text(name_node, source)
                    elements: List[Any] = []
                    for child in value_node.children:
                        if child.type == "string":
                            elements.append(self._get_node_text(child, source).strip("'\"`"))
                        elif child.type == "identifier":
                            elements.append(f"${self._get_node_text(child, source)}")
                        elif child.type == "spread_element":
                            inner = child.children[1] if len(child.children) > 1 else None
                            if inner:
                                elements.append(f"...${self._get_node_text(inner, source)}")
                        elif child.type not in ("[", "]", ","):
                            elements.append(self._get_node_text(child, source))
                    arrays.append(
                        TSArrayLiteral(
                            variable_name=var_name,
                            elements=elements,
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                        )
                    )
            for child in node.children:
                visit(child)

        visit(root)
        return arrays

    def _ts_extract_decorators(self, root: Any, source: str) -> List[TSDecoratedItem]:
        """Extract TypeScript/experimental decorators from classes and methods."""
        items: List[TSDecoratedItem] = []

        def visit(node: Any) -> None:
            if node.type in ("class_declaration", "method_definition"):
                decorators: List[str] = []
                decorator_args: Dict[str, Any] = {}
                # Look for decorator siblings (preceding nodes)
                parent = node.parent
                if parent:
                    found_self = False
                    for sibling in reversed(parent.children):
                        if sibling is node:
                            found_self = True
                            continue
                        if found_self and sibling.type == "decorator":
                            # Check if it's a call: @Name(args)
                            for ch in sibling.children:
                                if ch.type == "call_expression":
                                    func_n = ch.child_by_field_name("function")
                                    if func_n:
                                        dec_name = self._get_node_text(func_n, source)
                                        decorators.append(dec_name)
                                        args_n = ch.child_by_field_name("arguments")
                                        if args_n:
                                            for arg in args_n.children:
                                                if arg.type == "object":
                                                    decorator_args.update(
                                                        self._extract_object_properties(arg, source)
                                                    )
                                    break
                                elif ch.type == "identifier":
                                    decorators.append(self._get_node_text(ch, source))
                                    break
                        elif found_self and sibling.type != "decorator":
                            break

                if decorators:
                    name_node = node.child_by_field_name("name")
                    item_name = self._get_node_text(name_node, source) if name_node else "unknown"
                    item_type = "class" if node.type == "class_declaration" else "method"
                    items.append(
                        TSDecoratedItem(
                            item_name=item_name,
                            item_type=item_type,
                            decorators=decorators,
                            decorator_args=decorator_args,
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                        )
                    )

            for child in node.children:
                visit(child)

        visit(root)
        return items

    # =========================================================================
    # Post-processing: argument resolution, method chain decomposition
    # =========================================================================

    def _resolve_all_arguments(self, result: TSParseResult) -> None:
        """Resolve identifier references in arguments using the symbol table."""
        st = result.symbol_table
        for inst in result.instantiations:
            inst.resolved_arguments = dict(inst.arguments)
            for key, val in inst.arguments.items():
                if isinstance(val, str):
                    resolved = st.resolve(val)
                    if resolved is not None:
                        inst.resolved_arguments[key] = resolved
            # Check parent for type annotation (already extracted in tree-sitter)

        for call in result.function_calls:
            call.resolved_arguments = dict(call.arguments)
            for key, val in call.arguments.items():
                if isinstance(val, str):
                    resolved = st.resolve(val)
                    if resolved is not None:
                        call.resolved_arguments[key] = resolved

    def _decompose_method_chains(self, result: TSParseResult) -> None:
        """Decompose function_name into receiver_chain and method_name."""
        for call in result.function_calls:
            if call.is_method_call and "." in call.function_name:
                parts = call.function_name.split(".")
                call.method_name = parts[-1]
                call.receiver_chain = parts[:-1]
            elif not call.is_method_call:
                call.method_name = call.function_name
                call.receiver_chain = []

    # =========================================================================
    # Regex extraction methods (fallback)
    # =========================================================================

    def parse_file(self, file_path: str) -> TSParseResult:
        """Parse a TypeScript/JavaScript file from path"""
        try:
            path = Path(file_path)
            source = path.read_text(encoding="utf-8", errors="ignore")
            return self.parse(source, file_path)
        except Exception as e:
            result = TSParseResult()
            result.errors.append(f"File read error: {str(e)}")
            return result

    def _extract_imports(self, source: str) -> List[TSImportInfo]:
        """Extract import statements from source"""
        imports = []
        lines = source.split("\n")

        for line_num, line in enumerate(lines, 1):
            # Named imports: import { X, Y } from 'module'
            match = IMPORT_PATTERNS["named_import"].search(line)
            if match:
                names_str = match.group(1)
                names = [n.strip().split(" as ")[0] for n in names_str.split(",")]
                imports.append(
                    TSImportInfo(
                        module=match.group(2),
                        names=[n for n in names if n],
                        line_number=line_num,
                    )
                )
                continue

            # Default import: import X from 'module'
            match = IMPORT_PATTERNS["default_import"].search(line)
            if match:
                imports.append(
                    TSImportInfo(
                        module=match.group(2),
                        names=[],
                        default_import=match.group(1),
                        line_number=line_num,
                    )
                )
                continue

            # Namespace import: import * as X from 'module'
            match = IMPORT_PATTERNS["namespace_import"].search(line)
            if match:
                imports.append(
                    TSImportInfo(
                        module=match.group(2),
                        names=[],
                        namespace_import=match.group(1),
                        line_number=line_num,
                    )
                )
                continue

            # CommonJS require
            match = IMPORT_PATTERNS["require"].search(line)
            if match:
                imports.append(
                    TSImportInfo(
                        module=match.group(2),
                        names=[],
                        default_import=match.group(1),
                        is_require=True,
                        line_number=line_num,
                    )
                )
                continue

            # CommonJS destructured require
            match = IMPORT_PATTERNS["require_destructure"].search(line)
            if match:
                names_str = match.group(1)
                names = [n.strip().split(":")[0] for n in names_str.split(",")]
                imports.append(
                    TSImportInfo(
                        module=match.group(2),
                        names=[n for n in names if n],
                        is_require=True,
                        line_number=line_num,
                    )
                )

        return imports

    def _extract_instantiations(self, source: str) -> List[TSClassInstantiation]:
        """Extract new ClassName() patterns"""
        instantiations = []
        lines = source.split("\n")

        for line_num, line in enumerate(lines, 1):
            for match in NEW_INSTANCE_PATTERN.finditer(line):
                class_name = match.group(1)

                # Skip common built-ins
                if class_name in {"Date", "Array", "Map", "Set", "Promise", "Error"}:
                    continue

                instantiations.append(
                    TSClassInstantiation(
                        class_name=class_name,
                        line_start=line_num,
                        source_snippet=line.strip()[:100],
                    )
                )

        return instantiations

    def _extract_function_calls(self, source: str) -> List[TSFunctionCall]:
        """Extract function call patterns"""
        function_calls = []
        lines = source.split("\n")

        # Track seen calls to avoid duplicates on same line
        seen: Set[Tuple[int, str]] = set()

        for line_num, line in enumerate(lines, 1):
            # Skip comments
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue

            for match in FUNCTION_CALL_PATTERN.finditer(line):
                func_name = match.group(1)

                # Skip common builtins and control flow
                if func_name in {
                    "if",
                    "for",
                    "while",
                    "switch",
                    "function",
                    "return",
                    "new",
                    "throw",
                    "catch",
                }:
                    continue

                key = (line_num, func_name)
                if key in seen:
                    continue
                seen.add(key)

                # Check if method call
                is_method = "." in func_name
                receiver = func_name.rsplit(".", 1)[0] if is_method else None
                arguments: Dict[str, Any] = {}
                positional_args: List[Any] = []

                # Best-effort inline argument parsing for regex fallback mode.
                # This enables model extraction from simple patterns like:
                # create({ model: "gpt-4o-mini" }) / create(apiParams) / create(...apiParams)
                open_paren_idx = line.find("(", match.start())
                close_paren_idx = line.rfind(")")
                if open_paren_idx != -1 and close_paren_idx > open_paren_idx:
                    arg_text = line[open_paren_idx + 1 : close_paren_idx].strip()
                    if arg_text.startswith("{") and arg_text.endswith("}"):
                        inner = arg_text[1:-1]
                        for kv in re.finditer(
                            r'["\']?([A-Za-z_][A-Za-z0-9_]*)["\']?\s*:\s*([^,}]+)', inner
                        ):
                            arg_key = kv.group(1)
                            raw_val = kv.group(2).strip()
                            if (
                                raw_val.startswith(("'", '"', "`"))
                                and raw_val.endswith(("'", '"', "`"))
                                and len(raw_val) >= 2
                            ):
                                arguments[arg_key] = raw_val[1:-1]
                            else:
                                arguments[arg_key] = raw_val
                    elif arg_text:
                        # Split simple comma-separated args on the same line.
                        for part in [p.strip() for p in arg_text.split(",") if p.strip()]:
                            if part.startswith("..."):
                                arguments.setdefault("__args_expanded__", []).append(
                                    f"${part[3:].strip()}"
                                )
                            else:
                                positional_args.append(part)

                function_calls.append(
                    TSFunctionCall(
                        function_name=func_name,
                        receiver=receiver,
                        is_method_call=is_method,
                        arguments=arguments,
                        positional_args=positional_args,
                        line_start=line_num,
                        source_snippet=line.strip()[:100],
                    )
                )

        return function_calls

    def _extract_object_literals(self, source: str) -> List[TSObjectLiteral]:
        """Extract object literal assignments"""
        objects = []
        lines = source.split("\n")

        for line_num, line in enumerate(lines, 1):
            match = OBJECT_LITERAL_PATTERN.search(line)
            if match:
                is_exported = "export" in line[: match.start()]
                objects.append(
                    TSObjectLiteral(
                        variable_name=match.group(1),
                        is_exported=is_exported,
                        is_const="const" in line,
                        line_start=line_num,
                    )
                )

        return objects

    def _extract_string_literals(self, source: str) -> List[TSStringLiteral]:
        """Extract long string literals (potential prompts) with enhanced context capture"""
        strings = []
        lines = source.split("\n")

        # Patterns to extract context
        # const SYSTEM_PROMPT = `...` or let myPrompt = "..."
        var_assignment_pattern = re.compile(
            r'(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*\w+)?\s*=\s*[`"\']',
            re.IGNORECASE,
        )
        # Object property: systemPrompt: "..." or "systemPrompt": "..."
        property_pattern = re.compile(r'["\']?([A-Za-z_][A-Za-z0-9_]*)["\']?\s*:\s*[`"\']')
        # Function definition: function getPrompt() or const getPrompt = () =>
        # Use [ \t] instead of \s to avoid matching across lines
        function_pattern = re.compile(
            r"(?:function[ \t]+([A-Za-z_][A-Za-z0-9_]*)\s*\(|(?:const|let|var)[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)[ \t]*=>)"
        )
        # Assignment: this.prompt = "..." or obj.field = "..."
        member_assignment_pattern = re.compile(
            r'(?:this|[A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[`"\']'
        )

        def find_context_in_text(text: str) -> Optional[str]:
            """Find context from various patterns in text"""
            # Check variable assignment first (most specific)
            m = var_assignment_pattern.search(text)
            if m:
                return m.group(1)
            # Check member assignment
            m = member_assignment_pattern.search(text)
            if m:
                return m.group(1)
            # Check property assignment
            m = property_pattern.search(text)
            if m:
                return m.group(1)
            return None

        def find_function_context(source: str, pos: int) -> Optional[str]:
            """Look backwards from position to find enclosing function"""
            # Search backwards for function definition (up to 500 chars)
            start = max(0, pos - 500)
            preceding = source[start:pos]

            # Find last (closest) function definition
            last_func = None
            for m in function_pattern.finditer(preceding):
                func_name = m.group(1) or m.group(2)
                if func_name:
                    last_func = func_name
            return last_func

        # Find template literals (backticks) across the full source
        for match in TEMPLATE_LITERAL_PATTERN.finditer(source):
            value = match.group(1)
            if len(value) > 50:  # Only long strings
                # Calculate line number
                pos = match.start()
                line_num = source[:pos].count("\n") + 1

                # Try to find variable context from the line
                line_start = source.rfind("\n", 0, pos) + 1
                line_text = source[line_start : pos + 1]
                context = find_context_in_text(line_text)

                enclosing_fn = find_function_context(source, pos)
                # Fall back to function context
                if not context:
                    context = enclosing_fn

                strings.append(
                    TSStringLiteral(
                        value=value[:500],  # Truncate for storage
                        is_template=True,
                        has_interpolation="${" in value,
                        context=context,
                        line_number=line_num,
                        char_count=len(value),
                        enclosing_function=enclosing_fn,
                    )
                )

        # Find regular string literals
        for line_num, line in enumerate(lines, 1):
            for match in STRING_LITERAL_PATTERN.finditer(line):
                value = match.group(1)

                # Try to find context from the line
                context = find_context_in_text(line[: match.start()])

                # Always compute position for enclosing function lookup
                pos = sum(len(lines[i]) + 1 for i in range(line_num - 1)) + match.start()
                enclosing_fn = find_function_context(source, pos)

                # Fall back to function context for context field
                if not context:
                    context = enclosing_fn

                strings.append(
                    TSStringLiteral(
                        value=value[:500],
                        is_template=False,
                        context=context,
                        line_number=line_num,
                        char_count=len(value),
                        enclosing_function=enclosing_fn,
                    )
                )

        return strings


# Singleton parser instance
_parser: Optional[TypeScriptParser] = None


def get_ts_parser() -> TypeScriptParser:
    """Get the singleton TypeScript parser instance"""
    global _parser
    if _parser is None:
        _parser = TypeScriptParser()
    return _parser


def parse_typescript(source: str, file_path: Optional[str] = None) -> TSParseResult:
    """Convenience function to parse TypeScript/JavaScript source"""
    return get_ts_parser().parse(source, file_path)


def parse_typescript_file(file_path: str) -> TSParseResult:
    """Convenience function to parse a TypeScript/JavaScript file"""
    return get_ts_parser().parse_file(file_path)
