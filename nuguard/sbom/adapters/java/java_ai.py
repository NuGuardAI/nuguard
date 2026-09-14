"""Java AI framework, model, prompt, tool, datastore, and guardrail adapter."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from ...core.java_parser import JavaMethodDeclaration
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._java_base import JavaFrameworkAdapter

_AI_PACKAGES: tuple[tuple[str, str], ...] = (
    ("org.springframework.ai", "spring-ai"),
    ("dev.langchain4j", "langchain4j"),
    ("io.quarkiverse.langchain4j", "quarkus-langchain4j"),
    ("com.openai", "openai-java"),
    ("com.azure.ai.openai", "azure-openai-java"),
    ("software.amazon.awssdk.services.bedrock", "aws-bedrock-java"),
    ("com.google.genai", "google-genai-java"),
    ("com.google.cloud.vertexai", "google-vertex-ai-java"),
)
_MODEL_LITERAL_RE = re.compile(
    r"(?i)\b(?:model|modelName|modelId|deploymentName)\s*\(\s*"
    r'(?P<quote>["\'])(?P<model>[^"\']{2,120})(?P=quote)'
)
_MODEL_ASSIGN_RE = re.compile(
    r"(?i)\b(?:MODEL|MODEL_NAME|MODEL_ID|DEPLOYMENT_NAME)\b\s*=\s*"
    r'(?P<quote>["\'])(?P<model>[^"\']{2,120})(?P=quote)'
)
_AI_MARKERS = (
    "ChatClient",
    "ChatModel",
    "AiServices",
    "OpenAIClient",
    "OpenAIService",
    "BedrockRuntimeClient",
    "GenerativeModel",
    "VertexAI",
    "PromptTemplate",
)
_TOOL_ANNOTATIONS = {"Tool", "Function", "ToolFunction", "Action"}
_GUARDRAIL_MARKERS = (
    "Guardrail",
    "ModerationModel",
    "ContentSafety",
    "PromptShield",
    "OutputValidator",
    "ResponseValidator",
)
_DATASTORE_MARKERS: tuple[tuple[str, str, str], ...] = (
    ("VectorStore", "vector-store", "vector"),
    ("EmbeddingStore", "embedding-store", "vector"),
    ("Pinecone", "pinecone", "vector"),
    ("Qdrant", "qdrant", "vector"),
    ("Chroma", "chroma", "vector"),
    ("Redis", "redis", "kv"),
    ("JdbcTemplate", "jdbc", "relational"),
)


def _provider(imports: set[str], model: str = "") -> str | None:
    joined = " ".join(imports).lower()
    lowered = model.lower()
    if "anthropic" in joined or lowered.startswith("claude"):
        return "anthropic"
    if "azure.ai.openai" in joined or "deployment" in lowered:
        return "azure-openai"
    if "bedrock" in joined:
        return "aws-bedrock"
    if "google" in joined or lowered.startswith(("gemini", "text-bison")):
        return "google"
    if "openai" in joined or lowered.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    return None


def _frameworks(imports: set[str]) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for package, framework in _AI_PACKAGES:
        matching = [
            value for value in imports if value == package or value.startswith(package + ".")
        ]
        if matching:
            result.append((framework, 1))
    return result


def _agent_canonical(file_path: str, containing_type: str) -> str:
    return canonicalize_text(f"java-agent:{file_path}:{containing_type}")


def _method_contains_ai(method: JavaMethodDeclaration) -> bool:
    haystack = f"{method.signature}\n{method.body}"
    return any(marker in haystack for marker in _AI_MARKERS) or bool(
        re.search(r"(?i)\.(?:prompt|call|chat|generate|complete|invoke)\s*\(", haystack)
    )


def _template_variables(value: str) -> list[str]:
    values: list[str] = []
    for pattern in (
        r"\{\{\s*([A-Za-z_$][\w$.-]*)\s*\}\}",
        r"\$\{\s*([A-Za-z_$][\w$.-]*)\s*\}",
        r"\{\s*([A-Za-z_$][\w$.-]*)\s*\}",
        r"%(?:\d+\$)?[sdf]",
    ):
        for match in re.finditer(pattern, value):
            token = match.group(1) if match.lastindex else match.group(0)
            if token not in values:
                values.append(token)
    return values


class JavaAIAdapter(JavaFrameworkAdapter):
    """Extract Java AI frameworks, agents, models, prompts, tools, and controls."""

    name = "java_ai"
    priority = 20
    handles_packages = [package for package, _ in _AI_PACKAGES]

    def extract(self, content: str, file_path: str, parse_result: Any) -> list[ComponentDetection]:
        result = self._parse_result(content, file_path, parse_result)
        imports = {item.module for item in result.imports}
        frameworks = _frameworks(imports)
        if not frameworks:
            return []

        detections: list[ComponentDetection] = []
        import_line = min((item.line for item in result.imports), default=1)
        for framework, _ in frameworks:
            detections.append(self._fw_node(framework, file_path, import_line))

        model_entries: list[tuple[str, int, str]] = []
        for pattern in (_MODEL_LITERAL_RE, _MODEL_ASSIGN_RE):
            for match in pattern.finditer(content):
                model = match.group("model").strip()
                if not model or model.startswith(("${", "#{")):
                    continue
                line = content.count("\n", 0, match.start()) + 1
                entry = (model, line, match.group(0)[:160])
                if entry not in model_entries:
                    model_entries.append(entry)

        primary_framework = frameworks[0][0]
        model_canonicals: list[str] = []
        for model, line, snippet in model_entries:
            canonical = canonicalize_text(model.lower())
            model_canonicals.append(canonical)
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=canonical,
                    display_name=model,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.94,
                    metadata={
                        "framework": primary_framework,
                        "provider": _provider(imports, model),
                        "language": "java",
                        "source": "java_call",
                    },
                    file_path=file_path,
                    line=line,
                    snippet=snippet,
                    evidence_kind="ast_call",
                )
            )

        types_by_name = {item.name: item for item in result.type_declarations}
        agent_types: list[str] = []
        for item in result.type_declarations:
            body = content[item.body_start : item.body_end + 1]
            if any(marker in body for marker in _AI_MARKERS) or any(
                method.containing_type == item.name and _method_contains_ai(method)
                for method in result.method_declarations
            ):
                agent_types.append(item.name)
        if not agent_types:
            stem = PurePosixPath(file_path).stem or "JavaApplication"
            agent_types = [stem]

        tool_methods = [
            method
            for method in result.method_declarations
            if any(
                self._annotation_name(annotation) in _TOOL_ANNOTATIONS
                for annotation in method.annotations
            )
        ]
        tool_canonicals = {
            method.name: canonicalize_text(
                f"java-tool:{file_path}:{method.containing_type or 'type'}:{method.name}"
            )
            for method in tool_methods
        }

        datastore_canonicals: list[tuple[str, str]] = []
        for marker, name, datastore_type in _DATASTORE_MARKERS:
            if marker not in content:
                continue
            canonical = canonicalize_text(f"java-datastore:{name}:{file_path}")
            datastore_canonicals.append((canonical, datastore_type))
            line = next(
                (item.line for item in result.imports if marker.lower() in item.module.lower()),
                1,
            )
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=canonical,
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.86,
                    metadata={
                        "framework": primary_framework,
                        "language": "java",
                        "datastore_type": datastore_type,
                    },
                    file_path=file_path,
                    line=line,
                    snippet=marker,
                    evidence_kind="ast_import",
                )
            )

        guardrail_canonical: str | None = None
        guardrail_marker = next(
            (marker for marker in _GUARDRAIL_MARKERS if marker in content), None
        )
        if guardrail_marker:
            guardrail_canonical = canonicalize_text(
                f"java-guardrail:{file_path}:{guardrail_marker}"
            )
            line = next(
                (
                    index
                    for index, source_line in enumerate(content.splitlines(), start=1)
                    if guardrail_marker in source_line
                ),
                1,
            )
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=guardrail_canonical,
                    display_name=guardrail_marker,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "framework": primary_framework,
                        "language": "java",
                        "guardrail_type": "input_output_validation",
                    },
                    file_path=file_path,
                    line=line,
                    snippet=self._line_snippet(content, line),
                    evidence_kind="ast_call",
                )
            )

        prompt_detections: list[ComponentDetection] = []
        prompt_canonicals: list[str] = []
        for literal in result.string_literals:
            variable = (literal.assigned_to or "").lower()
            if not (
                literal.is_potential_prompt
                or any(
                    marker in variable for marker in ("prompt", "instruction", "system", "persona")
                )
            ):
                continue
            value = literal.value.strip()
            if not value:
                continue
            canonical = canonicalize_text(
                f"java-prompt:{file_path}:{literal.line}:{literal.assigned_to or 'literal'}"
            )
            prompt_canonicals.append(canonical)
            template_vars = _template_variables(value)
            source_line = self._line_snippet(content, literal.line)
            formatted = "String.format" in source_line or ".formatted(" in source_line
            prompt_detections.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=canonical,
                    display_name=literal.assigned_to or f"Prompt at line {literal.line}",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "framework": primary_framework,
                        "language": "java",
                        "content": value,
                        "char_count": len(value),
                        "role": "system" if "system" in variable else "user",
                        "is_template": bool(template_vars or formatted),
                        "template_variables": template_vars,
                        "injection_risk_score": 0.8 if formatted else 0.2,
                    },
                    file_path=file_path,
                    line=literal.line,
                    snippet=value.replace("\n", " ")[:160],
                    evidence_kind="ast_literal",
                )
            )

        for agent_type in agent_types:
            type_decl = types_by_name.get(agent_type)
            line = type_decl.line if type_decl else import_line
            agent_canonical = _agent_canonical(file_path, agent_type)
            relationships: list[RelationshipHint] = []
            for model_canonical in model_canonicals:
                relationships.append(
                    RelationshipHint(
                        source_canonical=agent_canonical,
                        source_type=ComponentType.AGENT,
                        target_canonical=model_canonical,
                        target_type=ComponentType.MODEL,
                        relationship_type="USES",
                    )
                )
            for prompt_canonical in prompt_canonicals:
                relationships.append(
                    RelationshipHint(
                        source_canonical=agent_canonical,
                        source_type=ComponentType.AGENT,
                        target_canonical=prompt_canonical,
                        target_type=ComponentType.PROMPT,
                        relationship_type="USES",
                    )
                )
            for tool_canonical in tool_canonicals.values():
                relationships.append(
                    RelationshipHint(
                        source_canonical=agent_canonical,
                        source_type=ComponentType.AGENT,
                        target_canonical=tool_canonical,
                        target_type=ComponentType.TOOL,
                        relationship_type="CALLS",
                    )
                )
            for datastore_canonical, _ in datastore_canonicals:
                relationships.append(
                    RelationshipHint(
                        source_canonical=agent_canonical,
                        source_type=ComponentType.AGENT,
                        target_canonical=datastore_canonical,
                        target_type=ComponentType.DATASTORE,
                        relationship_type="ACCESSES",
                        access_type="readwrite",
                    )
                )
            if guardrail_canonical:
                relationships.append(
                    RelationshipHint(
                        source_canonical=guardrail_canonical,
                        source_type=ComponentType.GUARDRAIL,
                        target_canonical=agent_canonical,
                        target_type=ComponentType.AGENT,
                        relationship_type="PROTECTS",
                    )
                )
                for model_canonical in model_canonicals:
                    relationships.append(
                        RelationshipHint(
                            source_canonical=guardrail_canonical,
                            source_type=ComponentType.GUARDRAIL,
                            target_canonical=model_canonical,
                            target_type=ComponentType.MODEL,
                            relationship_type="PROTECTS",
                        )
                    )
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=agent_canonical,
                    display_name=agent_type,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": primary_framework,
                        "language": "java",
                        "agentic": bool(tool_methods),
                    },
                    file_path=file_path,
                    line=line,
                    snippet=type_decl.signature[:160] if type_decl else agent_type,
                    evidence_kind="ast_instantiation",
                    relationships=relationships,
                )
            )

        for method in tool_methods:
            canonical = tool_canonicals[method.name]
            annotation = next(
                (
                    item
                    for item in method.annotations
                    if self._annotation_name(item) in _TOOL_ANNOTATIONS
                ),
                "",
            )
            description = self._annotation_value(annotation)
            privilege_scope: list[str] = []
            haystack = f"{method.name} {method.body}".lower()
            if re.search(r"\b(exec|processbuilder|runtime\.getruntime)\b", haystack):
                privilege_scope.append("code_execution")
            if re.search(r"\b(write|delete|save|update|insert)\b", haystack):
                privilege_scope.append("db_write")
            if re.search(r"\b(sendmail|email|smtp|mail)\b", haystack):
                privilege_scope.append("email_out")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canonical,
                    display_name=method.name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.95,
                    metadata={
                        "framework": primary_framework,
                        "language": "java",
                        "description": description or None,
                        "parameters": list(method.parameters),
                        "privilege_scope": privilege_scope,
                    },
                    file_path=file_path,
                    line=method.line,
                    snippet=method.signature[:160],
                    evidence_kind="ast_decorator",
                )
            )

        detections.extend(prompt_detections)
        return detections
