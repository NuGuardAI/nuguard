"""Java Spring MVC/WebFlux and JAX-RS/Quarkus endpoint adapter."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._java_base import JavaFrameworkAdapter
from .java_ai import _agent_canonical, _method_contains_ai


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

    def _route(self, annotations: tuple[str, ...]) -> tuple[str, str] | None:
        for annotation in annotations:
            name = self._annotation_name(annotation)
            if name in self._METHOD_ANNOTATIONS:
                return self._METHOD_ANNOTATIONS[name], self._annotation_value(annotation)
            if name == "RequestMapping":
                method_match = re.search(r"RequestMethod\.([A-Z]+)", annotation)
                method = method_match.group(1) if method_match else "ANY"
                return method, self._annotation_value(annotation)
        return None

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
                if self._annotation_name(annotation) in {"RequestMapping", "Path"}:
                    class_path = self._annotation_value(annotation)
                    break
            method_http, method_path = route
            path = self._join_path(class_path, method_path)
            endpoint_canonical = canonicalize_text(f"java-endpoint:{method_http}:{path}")
            owner_name = method.containing_type or PurePosixPath(file_path).stem
            agent_canonical = _agent_canonical(file_path, owner_name)
            all_annotations = class_annotations + method.annotations
            annotation_names = {self._annotation_name(item) for item in all_annotations}
            permit_all = "PermitAll" in annotation_names
            auth_required = bool(annotation_names & self._AUTH_ANNOTATIONS) and not permit_all
            accepts_user_input = bool(method.parameters) or any(
                name in annotation_names for name in {"RequestBody", "RequestParam", "PathVariable"}
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
