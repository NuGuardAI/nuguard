"""Azure AI Content Safety / Prompt Shields adapter.

Detects two related Azure guardrail surfaces:
- ``azure.ai.contentsafety.ContentSafetyClient`` — real SDK, ``.analyze_text(...)``
  / ``.analyze_image(...)`` methods. Verified:
  https://learn.microsoft.com/en-us/azure/ai-services/content-safety/quickstart-jailbreak
- Prompt Shields — has **no SDK method**, only a REST endpoint
  (``.../contentsafety/text:shieldPrompt``), so it is detected the same way
  ``guardrail_heuristic.py`` detects outbound calls: a raw source match on the
  ``:shieldPrompt`` REST path, independent of any Python import.
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter

_SDK_CLASSES = {"ContentSafetyClient"}
_SDK_METHODS = {"analyze_text", "analyze_image"}
_PROMPT_SHIELD_RE = re.compile(r":shieldPrompt\b")


class AzureContentSafetyAdapter(FrameworkAdapter):
    """Detect Azure AI Content Safety / Prompt Shields usage."""

    name = "azure_content_safety"
    priority = 30
    handles_imports = [
        "azure.ai.contentsafety",
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "urllib.request",
        "urllib3",
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

        for inst in parse_result.instantiations:
            if inst.class_name not in _SDK_CLASSES:
                continue
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(f"azure_content_safety:{file_path}:{inst.line}"),
                    display_name="Azure Content Safety",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "azure_content_safety",
                        "vendor": "azure",
                        "guardrail_type": "content_safety",
                        "detection_kind": "framework_native",
                    },
                    file_path=file_path,
                    line=inst.line,
                    snippet=f"{inst.class_name}(...)",
                    evidence_kind="ast_instantiation",
                )
            )

        for call in parse_result.function_calls:
            if call.function_name not in _SDK_METHODS:
                continue
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(f"azure_content_safety:{file_path}:{call.line}"),
                    display_name="Azure Content Safety",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "framework": "azure_content_safety",
                        "vendor": "azure",
                        "guardrail_type": "content_safety",
                        "detection_kind": "framework_native",
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"{call.receiver or 'client'}.{call.function_name}(...)",
                    evidence_kind="ast_call",
                )
            )

        m = _PROMPT_SHIELD_RE.search(content)
        if m:
            line = content.count("\n", 0, m.start()) + 1
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(f"azure_content_safety:prompt_shields:{file_path}:{line}"),
                    display_name="Azure Prompt Shields",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.6,
                    metadata={
                        "framework": "azure_content_safety",
                        "vendor": "azure",
                        "guardrail_type": "prompt_shields",
                        "detection_kind": "heuristic",
                    },
                    file_path=file_path,
                    line=line,
                    snippet="POST .../contentsafety/text:shieldPrompt",
                    evidence_kind="regex",
                )
            )

        return detected


__all__ = ["AzureContentSafetyAdapter"]
