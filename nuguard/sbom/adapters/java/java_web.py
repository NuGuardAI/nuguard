"""Java Spring MVC/WebFlux and JAX-RS/Quarkus endpoint adapter."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from ...core.java_parser import _mask_non_code
from ...http_semantics import HANDLER_SIGNALS_KEY
from ...normalization import canonicalize_text
from ...types import ComponentType
from .._handler_signals_text import regex_handler_signals
from ..base import ComponentDetection, RelationshipHint
from ._http_params import build_http_request
from ._java_base import JavaFrameworkAdapter
from .java_ai import _agent_canonical, _method_contains_ai

# Handler resolves the caller from the credential rather than the request.
_JAVA_IDENTITY_PATTERNS = (
    re.compile(r"@AuthenticationPrincipal\b|@CurrentUser\b|@RequestAttribute\b"),
    re.compile(r"\b(?:Principal|Authentication|JwtAuthenticationToken)\s+\w+"),
    re.compile(r"SecurityContextHolder\.getContext\(\)\.getAuthentication\(\)"),
)
_JAVA_COOKIE_PATTERNS = (
    re.compile(r"\.addCookie\s*\(|\bResponseCookie\b|\bHttpSession\b|session\.setAttribute\s*\("),
    re.compile(r"[\"']Set-Cookie[\"']", re.IGNORECASE),
)


_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_ANNOTATION_RE = re.compile(r"@\w+(?:\([^)]*\))?\s*")
# ``Type name;`` / ``Type name =`` declarations in a class body (fields, local variables).
# A lowercase package prefix is kept (``demo.agents.SupportAgent agent;``).
_DECLARED_TYPE_RE = re.compile(
    r"\b((?:[a-z_]\w*\.)*[A-Z]\w*(?:\.[A-Z]\w*)*)(?:<[^>;{}()]*>)?\s+[a-z_]\w*\s*[;=]"
)
# Simple types that are never agents; skipping them keeps the hint list short.
_NON_AGENT_TYPES = frozenset(
    {"String", "Object", "Integer", "Long", "Double", "Boolean", "List", "Map", "Set", "Optional"}
)


def _constructor_parameter_type(parameter: str) -> str:
    """Return the type of ``@Qualifier("x") final SupportAgent agent`` (generics removed)."""
    text = _ANNOTATION_RE.sub("", parameter).strip()
    text = re.sub(r"\bfinal\b", "", text).strip()
    parts = text.rsplit(None, 1)
    if len(parts) != 2:
        return ""
    return re.sub(r"<.*>", "", parts[0]).strip()


def _injected_agent_hints(
    content: str,
    file_path: str,
    owner: Any,
    owner_name: str,
    result: Any,
    agent_canonical: str,
) -> list[RelationshipHint]:
    """Link a controller to the agents it is injected with.

    The extractor resolves a hint by the canonical name of its target, and that name is built from
    the target's file path. Java mirrors packages in directories, so the file of an injected type
    can be derived from the controller's own path, its package and its imports. A hint whose
    target is not an agent never resolves and is dropped, so only real agents get linked.

    A simple name is resolved the way Java does: a single-type import, then the controller's
    own package, then on-demand imports. Each injected type becomes one hint carrying its
    candidate files in that order, and the extractor takes the first one that was scanned.
    Comments and string literals are masked first so commented-out code is not a declaration.
    """
    package_match = _PACKAGE_RE.search(content)
    package = package_match.group(1) if package_match else ""
    parent = str(PurePosixPath(file_path).parent)
    parent = "" if parent == "." else parent
    package_path = package.replace(".", "/")
    source_root: str | None
    if not package_path:
        source_root = parent
    elif parent == package_path:
        source_root = ""
    elif parent.endswith("/" + package_path):
        source_root = parent[: -len(package_path) - 1]
    else:
        source_root = None  # the directory layout does not mirror the package

    type_names: list[str] = []
    for method in result.method_declarations:
        if method.is_constructor and method.containing_type == owner_name:
            type_names.extend(_constructor_parameter_type(item) for item in method.parameters)
    if owner is not None:
        newlines = [match.start() for match in re.finditer(r"\n", content)]
        masked, _, _ = _mask_non_code(content, newlines)
        body = masked[owner.body_start : owner.body_end + 1]
        type_names.extend(_DECLARED_TYPE_RE.findall(body))

    explicit = {
        item.module.rsplit(".", 1)[-1]: item.module
        for item in result.imports
        if not item.wildcard and not item.is_static
    }
    wildcards = [item.module for item in result.imports if item.wildcard and not item.is_static]

    def _path_for(qualified: str) -> str | None:
        if source_root is None:
            return None
        relative = qualified.replace(".", "/") + ".java"
        return f"{source_root}/{relative}" if source_root else relative

    def _candidate(qualified: str, simple: str) -> tuple[str, str] | None:
        target_file = _path_for(qualified)
        if target_file is None:
            if "." in qualified and qualified.rsplit(".", 1)[0] != package:
                return None
            target_file = f"{parent}/{simple}.java" if parent else f"{simple}.java"
        return target_file, _agent_canonical(target_file, simple)

    hints: dict[tuple[tuple[str, str], ...], RelationshipHint] = {}
    for type_name in type_names:
        if not type_name:
            continue
        simple = type_name.rsplit(".", 1)[-1]
        if not simple[:1].isupper() or simple == owner_name or simple in _NON_AGENT_TYPES:
            continue
        if "." in type_name:
            qualified_names = [type_name]
        elif type_name in explicit:
            qualified_names = [explicit[type_name]]
        else:
            qualified_names = [f"{package}.{type_name}" if package else type_name]
            qualified_names.extend(f"{module}.{type_name}" for module in wildcards)
        candidates: list[tuple[str, str]] = []
        for qualified in qualified_names:
            candidate = _candidate(qualified, simple)
            if candidate is not None and candidate not in candidates:
                candidates.append(candidate)
        if not candidates or any(canonical == agent_canonical for _, canonical in candidates):
            continue
        hints.setdefault(
            tuple(candidates),
            RelationshipHint(
                source_canonical=agent_canonical,
                source_type=ComponentType.AGENT,
                target_canonical=candidates[0][1],
                target_type=ComponentType.AGENT,
                relationship_type="CALLS",
                target_files=tuple(candidates),
            ),
        )
    return list(hints.values())


class JavaWebAdapter(JavaFrameworkAdapter):
    """Extract Spring MVC/WebFlux and JAX-RS/Quarkus routes and auth controls."""

    name = "java_web"
    priority = 18
    handles_packages = [
        "org.springframework.web",
        "org.springframework.security",
        "jakarta.ws.rs",
        "javax.ws.rs",
        "io.quarkus",
    ]

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
    }
    _AUTH_ANNOTATIONS = {
        "PreAuthorize",
        "Secured",
        "RolesAllowed",
        "Authenticated",
        "DenyAll",
    }

    @staticmethod
    def _join_path(*parts: str) -> str:
        clean = [part.strip().strip("/") for part in parts if part and part.strip("/")]
        return "/" + "/".join(clean) if clean else "/"

    # ``value = LevelConstants.LEVEL_1`` — a bare constant reference rather
    # than a string literal. We can't resolve the constant's real value
    # without reading its (usually separate) declaring file, but Java code
    # conventionally names such route/step constants after their own string
    # value (``LEVEL_1 = "LEVEL_1"``), so the identifier itself is a
    # reasonable best-effort path segment — far better than silently
    # dropping the endpoint. Deliberately scoped to route resolution only,
    # not ``_annotation_value()`` itself, since that helper is shared by
    # adapters (e.g. model-name extraction) where this guess would be wrong.
    _BARE_CONSTANT_VALUE_RE = re.compile(
        r"(?:value|path)\s*=\s*([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\b"
    )

    def _route_value(self, annotation: str) -> str:
        value = self._annotation_value(annotation)
        if value:
            return value
        match = self._BARE_CONSTANT_VALUE_RE.search(annotation)
        if not match:
            return ""
        return match.group(1).rsplit(".", 1)[-1]

    @staticmethod
    def _is_request_mapping_annotation(name: str) -> bool:
        """Match ``RequestMapping`` and Spring *composed* meta-annotations.

        Spring lets a custom annotation alias its own ``value()`` onto
        ``@RequestMapping`` via ``@AliasFor`` (a standard pattern for
        domain-specific routing annotations, e.g.
        ``@VulnerableAppRequestMapping``). Such annotations are, semantically,
        route mappings, but their literal name is never ``RequestMapping`` —
        static extraction can't resolve the alias without reading the
        annotation's own (usually separate) declaration file, so this matches
        on the ``*RequestMapping`` naming convention instead.
        """
        return name == "RequestMapping" or (
            name.endswith("RequestMapping") and name != "RequestMapping"
        )

    def _route(self, annotations: tuple[str, ...]) -> tuple[str, str] | None:
        method: str | None = None
        path: str | None = None
        for annotation in annotations:
            name = self._annotation_name(annotation)
            if name in self._METHOD_ANNOTATIONS:
                if method is None:
                    method = self._METHOD_ANNOTATIONS[name]
                value = self._route_value(annotation)
                if value and path is None:
                    path = value
            elif name == "Path":
                # JAX-RS sub-resource path on the method; the verb comes from
                # a separate @GET/@POST annotation.
                value = self._route_value(annotation)
                if value and path is None:
                    path = value
            elif self._is_request_mapping_annotation(name):
                method_match = re.search(r"RequestMethod\.([A-Z]+)", annotation)
                if method is None:
                    method = method_match.group(1) if method_match else "ANY"
                value = self._route_value(annotation)
                if value and path is None:
                    path = value
        if method is None:
            return None
        return method, path or ""

    def extract(self, content: str, file_path: str, parse_result: Any) -> list[ComponentDetection]:
        result = self._parse_result(content, file_path, parse_result)
        imports = {item.module for item in result.imports}
        if any(item.startswith("org.springframework") for item in imports):
            framework = "spring-boot"
        elif any(item.startswith("io.quarkus") for item in imports):
            framework = "quarkus"
        else:
            framework = "jax-rs"
        detections: list[ComponentDetection] = [
            self._fw_node(
                framework, file_path, min((item.line for item in result.imports), default=1)
            )
        ]
        type_map = {item.name: item for item in result.type_declarations}
        emitted_agents: set[str] = set()
        emitted_auth: set[str] = set()
        emitted_validation: set[str] = set()

        for method in result.method_declarations:
            route = self._route(method.annotations)
            if route is None:
                continue
            owner = type_map.get(method.containing_type or "")
            class_annotations = owner.annotations if owner else ()
            class_path = ""
            for annotation in class_annotations:
                class_annotation_name = self._annotation_name(annotation)
                is_class_route_annotation = (
                    class_annotation_name == "Path"
                    or self._is_request_mapping_annotation(class_annotation_name)
                    # Composed controller annotations (e.g.
                    # ``@VulnerableAppRestController``) commonly alias their
                    # own ``value()`` onto ``@RequestMapping`` too, so a class
                    # path can arrive via a ``*Controller`` name as well.
                    or class_annotation_name.endswith("Controller")
                )
                if is_class_route_annotation:
                    value = self._route_value(annotation)
                    if value:
                        class_path = value
                        break
            method_http, method_path = route
            path = self._join_path(class_path, method_path)
            endpoint_key = path
            if not method_path:
                # The method-level route annotation's path couldn't be resolved
                # statically — commonly because a composed mapping annotation
                # (see ``_is_request_mapping_annotation``) takes a constant
                # reference rather than a string literal, e.g.
                # ``@VulnerableAppRequestMapping(value = LevelConstants.LEVEL_1)``.
                # Disambiguate by Java method identity so distinct handlers
                # that share an unresolved class-level path don't collapse
                # into a single SBOM node.
                endpoint_key = f"{path}#{method.name}"
            endpoint_canonical = canonicalize_text(f"java-endpoint:{method_http}:{endpoint_key}")
            owner_name = method.containing_type or PurePosixPath(file_path).stem
            agent_canonical = _agent_canonical(file_path, owner_name)
            all_annotations = class_annotations + method.annotations
            annotation_names = {self._annotation_name(item) for item in all_annotations}
            permit_all = "PermitAll" in annotation_names
            auth_required = bool(annotation_names & self._AUTH_ANNOTATIONS) and not permit_all
            accepts_user_input = bool(method.parameters) or any(
                name in annotation_names for name in {"RequestBody", "RequestParam", "PathVariable"}
            )
            http_request = build_http_request(
                method,
                result,
                framework=framework,
                annotation_name=self._annotation_name,
                annotation_value=self._annotation_value,
            )
            relationships = [
                RelationshipHint(
                    source_canonical=endpoint_canonical,
                    source_type=ComponentType.API_ENDPOINT,
                    target_canonical=agent_canonical,
                    target_type=ComponentType.AGENT,
                    relationship_type="CALLS",
                )
            ]
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.API_ENDPOINT,
                    canonical_name=endpoint_canonical,
                    display_name=f"{method_http} {path}",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.96,
                    metadata={
                        "framework": framework,
                        "language": "java",
                        "api_endpoint": path,
                        "endpoint": path,
                        "method": method_http,
                        "methods": [method_http],
                        "auth_required": auth_required,
                        "no_auth_required": permit_all,
                        "accepts_user_input": accepts_user_input,
                        "parameters": list(method.parameters),
                        "http_request": http_request.model_dump(mode="json"),
                        HANDLER_SIGNALS_KEY: regex_handler_signals(
                            "\n".join([*method.parameters, *method.annotations, method.body]),
                            identity_patterns=_JAVA_IDENTITY_PATTERNS,
                            cookie_patterns=_JAVA_COOKIE_PATTERNS,
                        ),
                    },
                    file_path=file_path,
                    line=method.line,
                    snippet=method.signature[:160],
                    evidence_kind="ast_decorator",
                    relationships=relationships,
                )
            )
            if agent_canonical not in emitted_agents:
                emitted_agents.add(agent_canonical)
                injected_agents = _injected_agent_hints(
                    content, file_path, owner, owner_name, result, agent_canonical
                )
                detections.append(
                    ComponentDetection(
                        component_type=ComponentType.AGENT,
                        canonical_name=agent_canonical,
                        display_name=owner_name,
                        adapter_name=self.name,
                        priority=self.priority + 2,
                        confidence=0.75 if _method_contains_ai(method) else 0.62,
                        metadata={
                            "framework": framework,
                            "language": "java",
                            "web_controller": True,
                        },
                        file_path=file_path,
                        line=owner.line if owner else method.line,
                        snippet=owner.signature[:160] if owner else method.signature[:160],
                        evidence_kind="ast_decorator",
                        relationships=injected_agents,
                    )
                )
            if auth_required:
                auth_canonical = canonicalize_text(f"java-auth:{file_path}:{owner_name}")
                if auth_canonical not in emitted_auth:
                    emitted_auth.add(auth_canonical)
                    detections.append(
                        ComponentDetection(
                            component_type=ComponentType.AUTH,
                            canonical_name=auth_canonical,
                            display_name=f"{owner_name} authorization",
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.94,
                            metadata={
                                "framework": framework,
                                "language": "java",
                                "auth_type": "method-security",
                            },
                            file_path=file_path,
                            line=method.line,
                            snippet=" ".join(sorted(annotation_names & self._AUTH_ANNOTATIONS)),
                            evidence_kind="ast_decorator",
                            relationships=[
                                RelationshipHint(
                                    source_canonical=auth_canonical,
                                    source_type=ComponentType.AUTH,
                                    target_canonical=endpoint_canonical,
                                    target_type=ComponentType.API_ENDPOINT,
                                    relationship_type="PROTECTS",
                                )
                            ],
                        )
                    )
            validated = bool(annotation_names & {"Valid", "Validated"}) or bool(
                re.search(r"(?i)\b(?:validator|validationService)\.validate\s*\(", method.body)
            )
            if validated:
                guardrail_canonical = canonicalize_text(
                    f"java-validation:{file_path}:{owner_name}:{method.name}"
                )
                if guardrail_canonical not in emitted_validation:
                    emitted_validation.add(guardrail_canonical)
                    detections.append(
                        ComponentDetection(
                            component_type=ComponentType.GUARDRAIL,
                            canonical_name=guardrail_canonical,
                            display_name=f"{method.name} validation",
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.88,
                            metadata={
                                "framework": framework,
                                "language": "java",
                                "guardrail_type": "request_output_validation",
                            },
                            file_path=file_path,
                            line=method.line,
                            snippet=method.signature[:160],
                            evidence_kind="ast_decorator",
                            relationships=[
                                RelationshipHint(
                                    source_canonical=guardrail_canonical,
                                    source_type=ComponentType.GUARDRAIL,
                                    target_canonical=endpoint_canonical,
                                    target_type=ComponentType.API_ENDPOINT,
                                    relationship_type="PROTECTS",
                                ),
                                RelationshipHint(
                                    source_canonical=guardrail_canonical,
                                    source_type=ComponentType.GUARDRAIL,
                                    target_canonical=agent_canonical,
                                    target_type=ComponentType.AGENT,
                                    relationship_type="PROTECTS",
                                ),
                            ],
                        )
                    )
        return detections
