"""Resolve Java handler signatures into technology-neutral HTTP request metadata.

Spring MVC/WebFlux and JAX-RS bind request inputs through parameter
annotations (``@RequestParam``, ``@QueryParam``, ...). This module turns the
raw parameter declarations produced by :mod:`nuguard.sbom.core.java_parser`
into :class:`~nuguard.sbom.models.HttpRequestMetadata`, so consumers such as
``nuguard pentest`` never parse Java themselves.

Names are only emitted when they are statically visible (annotation values,
the Java identifier for implicitly bound simple types, or literal keys read
from a request-parameter map). Anything else sets ``has_unresolved_inputs``
rather than inventing a parameter name.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from ...core.java_parser import JavaMethodDeclaration, JavaParseResult
from ...models import HttpParameterLocation, HttpParameterMetadata, HttpRequestMetadata

_ANNOTATION_RE = re.compile(r"@([A-Za-z_$][\w$.]*)\s*(\((?:[^()]|\([^()]*\))*\))?")
_NAMED_ATTR_RE = re.compile(r'\bname\s*=\s*"([^"]+)"')
_REQUEST_METHOD_RE = re.compile(r"RequestMethod\.([A-Z]+)")

_PARAM_ANNOTATION_LOCATIONS: dict[str, HttpParameterLocation] = {
    "PathVariable": "path",
    "PathParam": "path",
    "RequestParam": "query",
    "QueryParam": "query",
    "RequestHeader": "header",
    "HeaderParam": "header",
    "CookieValue": "cookie",
    "CookieParam": "cookie",
    "FormParam": "form",
    "RequestPart": "multipart",
    "RequestBody": "json",
}
_METHOD_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
    "GET": "GET",
    "POST": "POST",
    "PUT": "PUT",
    "PATCH": "PATCH",
    "DELETE": "DELETE",
    "HEAD": "HEAD",
    "OPTIONS": "OPTIONS",
}
# Framework-injected arguments that are not request inputs.
_FRAMEWORK_TYPES = frozenset(
    {
        "Authentication",
        "BindingResult",
        "Errors",
        "HttpEntity",
        "HttpHeaders",
        "HttpServletRequest",
        "HttpServletResponse",
        "HttpSession",
        "Locale",
        "Model",
        "ModelMap",
        "Principal",
        "RedirectAttributes",
        "ServerHttpRequest",
        "ServerHttpResponse",
        "ServerWebExchange",
        "SessionStatus",
        "UriComponentsBuilder",
        "UriInfo",
        "WebRequest",
        "AsyncResponse",
        "SecurityContext",
        "Request",
        "Response",
    }
)
_SIMPLE_TYPES = frozenset(
    {
        "String",
        "CharSequence",
        "char",
        "Character",
        "int",
        "Integer",
        "long",
        "Long",
        "short",
        "Short",
        "byte",
        "Byte",
        "boolean",
        "Boolean",
        "double",
        "Double",
        "float",
        "Float",
        "BigDecimal",
        "BigInteger",
        "UUID",
        "LocalDate",
        "LocalDateTime",
        "Instant",
    }
)
_MAP_TYPES = frozenset({"Map", "MultiValueMap", "HashMap", "LinkedHashMap", "MultivaluedMap"})
_MULTIPART_TYPES = frozenset({"MultipartFile", "Part", "FilePart"})
_INT_TYPES = frozenset({"int", "Integer", "long", "Long", "short", "Short", "byte", "Byte"})
_FLOAT_TYPES = frozenset({"double", "Double", "float", "Float", "BigDecimal"})
_BOOL_TYPES = frozenset({"boolean", "Boolean"})
_LIST_TYPES = frozenset({"List", "Set", "Collection", "Iterable", "Flux"})

_FORM_MARKERS = ("APPLICATION_FORM_URLENCODED", "application/x-www-form-urlencoded")
_MULTIPART_MARKERS = ("MULTIPART_FORM_DATA", "multipart/form-data")
_JSON_MARKERS = ("APPLICATION_JSON", "application/json")


def _base_type(type_text: str) -> tuple[str, str]:
    """Return ``(outer, inner)`` simple type names, e.g. ``Optional<Long>`` -> (Optional, Long)."""
    text = type_text.replace("final ", "").strip()
    outer = re.sub(r"<.*$", "", text).replace("[]", "").replace("...", "").strip()
    outer = outer.rsplit(".", 1)[-1]
    inner_match = re.search(r"<\s*([\w$.]+)", text)
    inner = inner_match.group(1).rsplit(".", 1)[-1] if inner_match else ""
    if text.endswith("[]") or text.endswith("..."):
        return "List", outer
    return outer, inner


def _type_hint(type_text: str) -> str:
    outer, inner = _base_type(type_text)
    base = inner if outer == "Optional" and inner else outer
    if outer in _LIST_TYPES:
        return "list"
    if base in _INT_TYPES:
        return "int"
    if base in _FLOAT_TYPES:
        return "float"
    if base in _BOOL_TYPES:
        return "bool"
    return "string"


def _split_declaration(declaration: str) -> tuple[list[tuple[str, str]], str, str]:
    """Split ``@A("x") @B final Type<T> name`` into (annotations, type, identifier)."""
    annotations = [
        (match.group(1).rsplit(".", 1)[-1], match.group(0))
        for match in _ANNOTATION_RE.finditer(declaration)
    ]
    remainder = _ANNOTATION_RE.sub(" ", declaration)
    remainder = re.sub(r"\bfinal\b", " ", remainder).strip()
    parts = remainder.rsplit(None, 1)
    if len(parts) != 2:
        return annotations, remainder, ""
    return annotations, parts[0].strip(), parts[1].strip()


def _string_constants(parse_result: JavaParseResult) -> dict[str, str]:
    """Same-file ``static final String NAME = "value"`` style constants."""
    return {
        literal.assigned_to: literal.value
        for literal in parse_result.string_literals
        if literal.assigned_to and literal.enclosing_method is None
    }


def _map_keys(
    method: JavaMethodDeclaration, variable: str, constants: dict[str, str]
) -> tuple[list[str], bool]:
    """Keys read from a request map in the handler body, and whether any were unresolvable.

    A key resolves when it is a string literal or a same-file constant;
    anything else (e.g. ``Constants.ID`` declared in another file) is
    reported as unresolved instead of being guessed.
    """
    pattern = re.compile(
        rf"\b{re.escape(variable)}\s*\.\s*(?:get|getOrDefault|getFirst|containsKey)\s*\(\s*"
        r'(?:"([^"]+)"|([A-Za-z_$][\w$.]*))\s*[,)]'
    )
    keys: list[str] = []
    unresolved = False
    for match in pattern.finditer(method.body):
        key = match.group(1) or constants.get(match.group(2) or "", "")
        if not key:
            unresolved = True
        elif key not in keys:
            keys.append(key)
    return keys, unresolved


def _methods(annotations: Iterable[str], annotation_name: Callable[[str], str]) -> list[str]:
    methods: list[str] = []
    for annotation in annotations:
        name = annotation_name(annotation)
        if name in _METHOD_ANNOTATIONS:
            methods.append(_METHOD_ANNOTATIONS[name])
        elif name.endswith("RequestMapping"):
            found = _REQUEST_METHOD_RE.findall(annotation)
            methods.extend(found or ["UNKNOWN"])
    return list(dict.fromkeys(methods)) or ["UNKNOWN"]


def build_http_request(
    method: JavaMethodDeclaration,
    parse_result: JavaParseResult,
    *,
    framework: str,
    annotation_name: Callable[[str], str],
    annotation_value: Callable[[str], str],
) -> HttpRequestMetadata:
    """Describe the HTTP request shape of one Java route handler.

    *annotation_name*/*annotation_value* are the shared
    :class:`~nuguard.sbom.adapters.java._java_base.JavaFrameworkAdapter`
    helpers, so annotation parsing stays in one place.
    """
    route_text = " ".join(method.annotations)
    consumes_form = any(marker in route_text for marker in _FORM_MARKERS)
    consumes_multipart = any(marker in route_text for marker in _MULTIPART_MARKERS)
    jax_rs = framework in {"jax-rs", "quarkus"}
    constants = _string_constants(parse_result)

    parameters: dict[tuple[str, str], HttpParameterMetadata] = {}
    content_types: list[str] = []
    unresolved = False

    def add(name: str, location: HttpParameterLocation, type_text: str, required: bool) -> None:
        key = (name, location)
        if name and key not in parameters:
            parameters[key] = HttpParameterMetadata(
                name=name, location=location, type_hint=_type_hint(type_text), required=required
            )

    for declaration in method.parameters:
        annotations, type_text, identifier = _split_declaration(declaration)
        outer, _inner = _base_type(type_text)
        binding = next(
            (
                (name, text)
                for name, text in annotations
                if name in _PARAM_ANNOTATION_LOCATIONS
            ),
            None,
        )

        if binding is None:
            if outer in _FRAMEWORK_TYPES or not identifier:
                continue
            if outer in _MULTIPART_TYPES:
                add(identifier, "multipart", type_text, True)
            elif jax_rs:
                # JAX-RS binds an unannotated parameter to the entity body.
                unresolved = True
            elif outer in _SIMPLE_TYPES or (outer == "Optional" and _inner in _SIMPLE_TYPES):
                add(identifier, "form" if consumes_form else "query", type_text, False)
            else:
                # Spring @ModelAttribute-style binding of an object's fields.
                unresolved = True
            continue

        annotation_simple, annotation_text = binding
        location = _PARAM_ANNOTATION_LOCATIONS[annotation_simple]
        if location == "query" and consumes_form:
            location = "form"
        if outer in _MULTIPART_TYPES:
            location = "multipart"
        optional = (
            outer == "Optional"
            or re.search(r"\brequired\s*=\s*false\b", annotation_text) is not None
            or "defaultValue" in annotation_text
        )

        if location == "json":
            if outer in _MAP_TYPES:
                keys, partial = _map_keys(method, identifier, constants)
                for key in keys:
                    add(key, "json", "String", False)
                unresolved = unresolved or partial or not keys
            else:
                # A DTO/String body: its fields live in another file.
                unresolved = True
            continue

        if outer in _MAP_TYPES and location in {"query", "form", "header"}:
            keys, partial = _map_keys(method, identifier, constants)
            for key in keys:
                add(key, location, "String", False)
            unresolved = unresolved or partial or not keys
            continue

        named = _NAMED_ATTR_RE.search(annotation_text)
        wire_name = (named.group(1) if named else "") or annotation_value(annotation_text)
        add(wire_name or identifier, location, type_text, location == "path" or not optional)

    locations = {parameter.location for parameter in parameters.values()}
    if "multipart" in locations or consumes_multipart:
        content_types.append("multipart/form-data")
    if "form" in locations:
        content_types.append("application/x-www-form-urlencoded")
    has_body_binding = any(
        name == "RequestBody" for d in method.parameters for name, _ in _split_declaration(d)[0]
    )
    if (
        "json" in locations
        or any(marker in route_text for marker in _JSON_MARKERS)
        or (has_body_binding and not (consumes_form or consumes_multipart))
    ):
        content_types.append("application/json")

    return HttpRequestMetadata(
        methods=_methods(method.annotations, annotation_name),
        parameters=list(parameters.values()),
        content_types=list(dict.fromkeys(content_types)),
        has_unresolved_inputs=unresolved,
    )
