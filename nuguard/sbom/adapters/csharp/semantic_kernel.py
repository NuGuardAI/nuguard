"""C# adapter for Microsoft Semantic Kernel."""

from __future__ import annotations

from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ..models_kb import get_model_details
from ._csharp_base import CSharpFrameworkAdapter
from ._source import (
    find_calls,
    first_argument,
    string_constants,
)

_SERVICE_CALLS: dict[str, str] = {
    "AddAzureOpenAIChatCompletion": "azure",
    "AddAzureOpenAITextEmbeddingGeneration": "azure",
    "AddOpenAIChatCompletion": "openai",
    "AddOpenAITextEmbeddingGeneration": "openai",
    "AddAnthropicChatCompletion": "anthropic",
    "AddOllamaChatCompletion": "ollama",
    "AddMistralChatCompletion": "mistral",
}
# Positional index of the plugin-name argument per registration method.
# Type registrations take the name first; object and prompt-directory
# registrations place it after the instance/path argument.
_PLUGIN_NAME_POSITION: dict[str, int] = {
    "AddFromObject": 1,
    "ImportPluginFromObject": 1,
    "AddFromPromptDirectory": 1,
    "ImportPluginFromPromptDirectory": 1,
}

_PLUGIN_CALLS = {
    "AddFromType",
    "ImportPluginFromType",
    "AddFromObject",
    "ImportPluginFromObject",
    "AddFromPromptDirectory",
    "ImportPluginFromPromptDirectory",
    "CreateFromType",
}
_PLANNER_CLASSES = {
    "ActionPlanner",
    "FunctionCallingStepwisePlanner",
    "HandlebarsPlanner",
    "SequentialPlanner",
    "StepwisePlanner",
}
_KERNEL_CALLS = {
    "CreateBuilder",
    "Build",
    "KernelBuilder",
}


class CSharpSemanticKernelAdapter(CSharpFrameworkAdapter):
    """Detect Semantic Kernel services, plugins, planners, and prompts."""

    name = "csharp_semantic_kernel"
    priority = 40
    handles_namespaces = ["Microsoft.SemanticKernel"]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = self._parse_result(
            content,
            file_path,
            parse_result,
        )

        calls = find_calls(
            content,
            _KERNEL_CALLS | set(_SERVICE_CALLS) | _PLUGIN_CALLS | _PLANNER_CLASSES,
        )
        has_kernel_call = any(
            (call.name == "CreateBuilder" and (call.receiver or "").split(".")[-1] == "Kernel")
            or (call.name == "KernelBuilder" and call.is_constructor)
            or call.name in _SERVICE_CALLS
            or call.name in _PLUGIN_CALLS
            or (call.name in _PLANNER_CLASSES and call.is_constructor)
            for call in calls
        )
        has_kernel_attribute = any(
            any(
                (
                    attribute.split("(", 1)[0].split(".")[-1].removesuffix("Attribute")
                    == "KernelFunction"
                )
                for attribute in method.attributes
            )
            for method in result.method_declarations
        )

        if not self._detect(result) and not has_kernel_call and not has_kernel_attribute:
            return []

        constants = string_constants(result)
        root = "framework:semantic_kernel"
        import_line = next(
            (
                item.line
                for item in result.using_directives
                if item.namespace.startswith("Microsoft.SemanticKernel")
            ),
            0,
        )
        detections: list[ComponentDetection] = [
            ComponentDetection(
                component_type=(ComponentType.FRAMEWORK),
                canonical_name=root,
                display_name=("Microsoft Semantic Kernel"),
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.98,
                metadata={
                    "framework": "semantic_kernel",
                    "language": "csharp",
                },
                file_path=file_path,
                line=import_line,
                snippet=("using Microsoft.SemanticKernel"),
                evidence_kind="ast_import",
            )
        ]

        builder_variables: set[str] = set()

        for call in calls:
            if call.name in _KERNEL_CALLS:
                receiver = call.receiver or ""
                is_kernel_builder = (
                    (call.name == "CreateBuilder" and receiver.split(".")[-1] == "Kernel")
                    or (call.name == "KernelBuilder" and call.is_constructor)
                    or (call.name == "Build" and receiver.split(".")[0] in builder_variables)
                )

                if not is_kernel_builder:
                    continue

                if (
                    call.name
                    in {
                        "CreateBuilder",
                        "KernelBuilder",
                    }
                    and call.assigned_to
                ):
                    builder_variables.add(call.assigned_to)

                instance_name = call.assigned_to or "kernel"
                canonical = canonicalize_text(f"semantic_kernel:{instance_name}")
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.FRAMEWORK),
                        canonical_name=canonical,
                        display_name=instance_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.94,
                        metadata={
                            "framework": ("semantic_kernel"),
                            "builder_call": (call.name),
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind="ast_call",
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.FRAMEWORK),
                                relationship_type=("CONTAINS"),
                            )
                        ],
                    )
                )
                continue

            if call.name in _SERVICE_CALLS:
                provider = _SERVICE_CALLS[call.name]
                model_name = first_argument(
                    call,
                    (
                        "modelId",
                        "model_id",
                        "deploymentName",
                        "deployment",
                        "aiModelId",
                    ),
                    constants,
                    0,
                )

                if not model_name:
                    # An unresolved model/deployment expression is not a model
                    # identifier — deriving one from the registration method
                    # would fabricate names like "AzureOpenAI" (#260).
                    continue

                canonical = canonicalize_text(model_name.lower())
                details = get_model_details(
                    model_name,
                    provider,
                    {},
                )
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.MODEL),
                        canonical_name=canonical,
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=(0.91 if model_name else 0.82),
                        metadata={
                            "framework": ("semantic_kernel"),
                            "provider": provider,
                            "registration": (call.name),
                            "language": "csharp",
                            **{key: value for key, value in details.items() if value is not None},
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind="ast_call",
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.MODEL),
                                relationship_type=("USES"),
                            )
                        ],
                    )
                )
                continue

            if call.name in _PLUGIN_CALLS:
                plugin_type = call.generic_arguments[0] if call.generic_arguments else ""
                plugin_name = first_argument(
                    call,
                    (
                        "pluginName",
                        "name",
                    ),
                    constants,
                    # Object and prompt-directory registrations place the
                    # plugin name in positional argument one.
                    _PLUGIN_NAME_POSITION.get(call.name, 0),
                )
                display = plugin_name or plugin_type or call.assigned_to or f"plugin_{call.line}"
                canonical = canonicalize_text(f"semantic_kernel:plugin:{display}")
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.TOOL),
                        canonical_name=canonical,
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": ("semantic_kernel"),
                            "registration": (call.name),
                            "plugin_type": (plugin_type or None),
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind="ast_call",
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.TOOL),
                                relationship_type=("USES"),
                            )
                        ],
                    )
                )
                continue

            if call.name in _PLANNER_CLASSES and call.is_constructor:
                display = call.assigned_to or call.name
                canonical = canonicalize_text(f"semantic_kernel:planner:{display}")
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.AGENT),
                        canonical_name=canonical,
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": ("semantic_kernel"),
                            "planner_class": (call.name),
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind=("ast_instantiation"),
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.AGENT),
                                relationship_type=("USES"),
                            )
                        ],
                    )
                )

        for method in result.method_declarations:
            is_kernel_function = any(
                (
                    attribute.split(
                        "(",
                        1,
                    )[0]
                    .split(".")[-1]
                    .removesuffix("Attribute")
                    == "KernelFunction"
                )
                for attribute in method.attributes
            )

            if not is_kernel_function:
                continue

            canonical = canonicalize_text(f"semantic_kernel:function:{method.name}")
            detections.append(
                ComponentDetection(
                    component_type=(ComponentType.TOOL),
                    canonical_name=canonical,
                    display_name=method.name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.93,
                    metadata={
                        "framework": ("semantic_kernel"),
                        "registration": ("KernelFunctionAttribute"),
                        "containing_type": (method.containing_type),
                        "language": "csharp",
                    },
                    file_path=file_path,
                    line=method.line,
                    snippet=method.signature,
                    evidence_kind="ast_attribute",
                    relationships=[
                        RelationshipHint(
                            source_canonical=root,
                            source_type=(ComponentType.FRAMEWORK),
                            target_canonical=(canonical),
                            target_type=(ComponentType.TOOL),
                            relationship_type="USES",
                        )
                    ],
                )
            )

        for literal in result.string_literals:
            assigned = (literal.assigned_to or "").lower()
            prompt_name = any(
                token in assigned
                for token in (
                    "prompt",
                    "template",
                    "instruction",
                )
            )

            if not (literal.is_potential_prompt or prompt_name):
                continue

            if len(literal.value.strip()) < 12:
                continue

            display = literal.assigned_to or f"prompt_{literal.line}"
            canonical = canonicalize_text(f"semantic_kernel:prompt:{display}")
            detections.append(
                ComponentDetection(
                    component_type=(ComponentType.PROMPT),
                    canonical_name=canonical,
                    display_name=display,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.86,
                    metadata={
                        "framework": ("semantic_kernel"),
                        "role": "system",
                        "content": literal.value,
                        "char_count": len(literal.value),
                        "is_template": (literal.is_interpolated),
                        "template_variables": list(literal.interpolation_expressions),
                        "language": "csharp",
                    },
                    file_path=file_path,
                    line=literal.line,
                    snippet=literal.value[:160],
                    evidence_kind=("ast_string_literal"),
                    relationships=[
                        RelationshipHint(
                            source_canonical=root,
                            source_type=(ComponentType.FRAMEWORK),
                            target_canonical=(canonical),
                            target_type=(ComponentType.PROMPT),
                            relationship_type="USES",
                        )
                    ],
                )
            )

        return _dedupe(detections)


def _dedupe(
    detections: list[ComponentDetection],
) -> list[ComponentDetection]:
    seen: set[tuple[ComponentType, str]] = set()
    result: list[ComponentDetection] = []

    for detection in detections:
        key = (
            detection.component_type,
            detection.canonical_name,
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(detection)

    return result


__all__ = ["CSharpSemanticKernelAdapter"]
