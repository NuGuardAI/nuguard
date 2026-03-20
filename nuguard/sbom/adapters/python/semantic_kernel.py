"""Semantic Kernel adapter.

Detects usage of Microsoft Semantic Kernel (``semantic_kernel``):
- ``Kernel`` instantiation → FRAMEWORK node
- ``kernel.add_plugin()`` / ``KernelPlugin`` → TOOL nodes
- ``kernel.add_function()`` / ``@kernel_function`` decorator → TOOL nodes
- ``OpenAIChatCompletion``, ``AzureChatCompletion``, etc. → MODEL nodes
- ``sk_function`` / ``KernelFunction`` → TOOL nodes
- Prompt templates / ``PromptTemplateConfig`` → PROMPT nodes
"""

from __future__ import annotations

import re
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter, RelationshipHint
from xelo.adapters.models_kb import get_model_details
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

_TEMPLATE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

_SERVICE_CLASSES = {
    "OpenAIChatCompletion": "openai",
    "OpenAITextCompletion": "openai",
    "AzureChatCompletion": "azure",
    "AzureTextCompletion": "azure",
    "GoogleAIChatCompletion": "google",
    "VertexAIChatCompletion": "google",
    "AnthropicChatCompletion": "anthropic",
    "HuggingFaceTextCompletion": "huggingface",
    "OllamaChatCompletion": "ollama",
    "MistralAIChatCompletion": "mistral",
}


class SemanticKernelAdapter(FrameworkAdapter):
    """Adapter for Microsoft Semantic Kernel framework."""

    name = "semantic_kernel"
    priority = 40
    handles_imports = [
        "semantic_kernel",
        "semantic_kernel.kernel",
        "semantic_kernel.connectors",
        "semantic_kernel.functions",
        "semantic_kernel.contents",
    ]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        detected: list[ComponentDetection] = [self._framework_node(file_path)]
        kernel_canonicals: list[str] = []

        for inst in parse_result.instantiations:
            # Kernel itself → FRAMEWORK node
            if inst.class_name == "Kernel":
                var_name = inst.assigned_to or "kernel"
                canon = canonicalize_text(f"semantic_kernel:{var_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.FRAMEWORK,
                        canonical_name=canon,
                        display_name="Semantic Kernel",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.95,
                        metadata={"framework": "semantic_kernel"},
                        file_path=file_path,
                        line=inst.line,
                        snippet="Kernel()",
                        evidence_kind="ast_instantiation",
                    )
                )
                kernel_canonicals.append(canon)

            # AI service classes → MODEL
            elif inst.class_name in _SERVICE_CLASSES:
                provider = _SERVICE_CLASSES[inst.class_name]
                args = inst.args or {}
                model_name = (
                    _clean(
                        args.get("ai_model_id")
                        or args.get("model_id")
                        or args.get("deployment_name")
                        or args.get("model")
                    )
                    or inst.class_name
                )
                details = get_model_details(model_name, provider, args)
                model_canon = canonicalize_text(model_name.lower())

                rels: list[RelationshipHint] = []
                for kc in kernel_canonicals:
                    rels.append(
                        RelationshipHint(
                            source_canonical=kc,
                            source_type=ComponentType.FRAMEWORK,
                            target_canonical=model_canon,
                            target_type=ComponentType.MODEL,
                            relationship_type="USES",
                        )
                    )

                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=model_canon,
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "class_name": inst.class_name,
                            "provider": provider,
                            **{k: v for k, v in details.items() if v is not None},
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(ai_model_id={model_name!r})",
                        evidence_kind="ast_instantiation",
                        relationships=rels,
                    )
                )

            # KernelPlugin → TOOL
            elif inst.class_name in {"KernelPlugin", "KernelFunction"}:
                plugin_name = _clean(
                    inst.args.get("name")
                    or (inst.positional_args[0] if inst.positional_args else None)
                    or inst.assigned_to
                    or f"plugin_{inst.line}"
                )
                canon = canonicalize_text(f"semantic_kernel:plugin:{plugin_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=canon,
                        display_name=plugin_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={"plugin_type": inst.class_name, "framework": "semantic_kernel"},
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(name={plugin_name!r})",
                        evidence_kind="ast_instantiation",
                    )
                )

            # PromptTemplateConfig → PROMPT
            elif inst.class_name in {"PromptTemplateConfig", "KernelPromptTemplate"}:
                template_raw = inst.args.get("template") or inst.args.get("template_str", "")
                template = _clean(template_raw)
                # Function-reference template: look up string literals from that function
                if not template and isinstance(template_raw, str) and template_raw.startswith("$"):
                    func_name = template_raw[1:]
                    func_literals = [
                        lit.value
                        for lit in parse_result.string_literals
                        if lit.context == func_name
                        and len(lit.value) >= 40
                        and not lit.is_docstring
                    ]
                    if func_literals:
                        template = max(func_literals, key=len)
                display_name = (
                    _clean(inst.args.get("name") or inst.assigned_to or "")
                    .replace("_", " ")
                    .title()
                    or inst.class_name
                )
                canon = canonicalize_text(display_name.lower())
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.PROMPT,
                        canonical_name=canon,
                        display_name=display_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={
                            "role": "system",
                            "content": template,
                            "char_count": len(template),
                            "is_template": bool(_TEMPLATE_VAR_RE.findall(template)),
                            "template_variables": _TEMPLATE_VAR_RE.findall(template),
                            "framework": "semantic_kernel",
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(...)",
                        evidence_kind="ast_instantiation",
                    )
                )

        # add_plugin / import_plugin_from_object → TOOL
        for call in parse_result.function_calls:
            if call.function_name in {
                "add_plugin",
                "import_plugin_from_object",
                "import_native_plugin_from_directory",
            }:
                plugin_name = _clean(
                    call.args.get("plugin_name")
                    or call.args.get("name")
                    or (call.positional_args[1] if len(call.positional_args) > 1 else None)
                    or f"plugin_{call.line}"
                )
                canon = canonicalize_text(f"semantic_kernel:plugin:{plugin_name}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=canon,
                        display_name=plugin_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "registration": call.function_name,
                            "framework": "semantic_kernel",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=f"{call.function_name}(plugin_name={plugin_name!r})",
                        evidence_kind="ast_call",
                    )
                )

        return detected


def _clean(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip("'\"` ")
    if s.startswith("$") or s in {"<complex>", "<lambda>", "<dict>", "<list>"}:
        return ""
    return s
