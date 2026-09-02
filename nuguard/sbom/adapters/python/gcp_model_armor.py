"""GCP Model Armor + Vertex/Gemini safety-settings adapter.

Two distinct GCP guardrail signals:
- **Model Armor** — ``google.cloud.modelarmor_v1`` / ``modelarmor_v1beta``,
  ``ModelArmorClient(...)`` + ``.sanitize_user_prompt(...)`` /
  ``.sanitize_model_response(...)``. Verified:
  https://docs.cloud.google.com/python/docs/reference/google-cloud-modelarmor/latest
- **Vertex/Gemini safety settings** — a ``safety_settings=[SafetySetting(...), ...]``
  kwarg passed to ``generate_content(...)``, or bare ``SafetySetting(...)``
  instantiations (``google.generativeai`` / ``google.genai`` /
  ``vertexai.generative_models``). Lower confidence than Model Armor since
  this is a kwarg-presence heuristic, not a dedicated guardrail class.
"""

from __future__ import annotations

from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter

_MODEL_ARMOR_CLASSES = {"ModelArmorClient"}
_MODEL_ARMOR_METHODS = {"sanitize_user_prompt", "sanitize_model_response"}
_SAFETY_SETTING_CLASSES = {"SafetySetting"}


def _mentions_model_armor(parse_result: Any) -> bool:
    for imp in parse_result.imports:
        module = (imp.module or "").lower()
        if "modelarmor" in module:
            return True
        if any("modelarmor" in name.lower() for name in imp.names or []):
            return True
    return False


class GCPModelArmorAdapter(FrameworkAdapter):
    """Detect GCP Model Armor and Vertex/Gemini safety-settings usage."""

    name = "gcp_model_armor"
    priority = 30
    handles_imports = [
        "google.cloud",
        "google.generativeai",
        "google.genai",
        "vertexai",
        "vertexai.generative_models",
    ]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        detected: list[ComponentDetection] = []

        if _mentions_model_armor(parse_result):
            for inst in parse_result.instantiations:
                if inst.class_name not in _MODEL_ARMOR_CLASSES:
                    continue
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.GUARDRAIL,
                        canonical_name=canonicalize_text(f"gcp_model_armor:{file_path}:{inst.line}"),
                        display_name="GCP Model Armor",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={
                            "framework": "gcp_model_armor",
                            "vendor": "gcp",
                            "guardrail_type": "model_armor",
                            "detection_kind": "framework_native",
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(...)",
                        evidence_kind="ast_instantiation",
                    )
                )

            for call in parse_result.function_calls:
                if call.function_name not in _MODEL_ARMOR_METHODS:
                    continue
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.GUARDRAIL,
                        canonical_name=canonicalize_text(f"gcp_model_armor:{file_path}:{call.line}"),
                        display_name="GCP Model Armor",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={
                            "framework": "gcp_model_armor",
                            "vendor": "gcp",
                            "guardrail_type": "model_armor",
                            "detection_kind": "framework_native",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=f"{call.receiver or 'client'}.{call.function_name}(...)",
                        evidence_kind="ast_call",
                    )
                )

        for inst in parse_result.instantiations:
            if inst.class_name not in _SAFETY_SETTING_CLASSES:
                continue
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(f"gcp_safety_settings:{file_path}:{inst.line}"),
                    display_name="Vertex/Gemini Safety Settings",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.75,
                    metadata={
                        "framework": "gcp_model_armor",
                        "vendor": "gcp",
                        "guardrail_type": "vertex_safety_settings",
                        "detection_kind": "heuristic",
                    },
                    file_path=file_path,
                    line=inst.line,
                    snippet="SafetySetting(...)",
                    evidence_kind="ast_instantiation",
                )
            )

        for call in parse_result.function_calls:
            if call.function_name != "generate_content":
                continue
            if not (call.args or {}).get("safety_settings"):
                continue
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(f"gcp_safety_settings:{file_path}:{call.line}"),
                    display_name="Vertex/Gemini Safety Settings",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.75,
                    metadata={
                        "framework": "gcp_model_armor",
                        "vendor": "gcp",
                        "guardrail_type": "vertex_safety_settings",
                        "detection_kind": "heuristic",
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"{call.receiver or 'model'}.generate_content(safety_settings=...)",
                    evidence_kind="ast_call",
                )
            )

        return detected


__all__ = ["GCPModelArmorAdapter"]
