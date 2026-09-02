"""LangChain moderation-chain adapter.

Detects LangChain's built-in content-moderation constructs:
- ``OpenAIModerationChain`` (``langchain.chains``)
- ``AmazonComprehendModerationChain`` (``langchain_experimental.comprehend_moderation``)

No TypeScript equivalent exists — LangChain.js does not ship these
moderation chains, so this adapter is Python-only.
"""

from __future__ import annotations

from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter

_MODERATION_CLASSES = {"OpenAIModerationChain", "AmazonComprehendModerationChain"}


class LangChainModerationAdapter(FrameworkAdapter):
    """Detect LangChain moderation-chain usage."""

    name = "langchain_moderation"
    priority = 30
    handles_imports = [
        "langchain.chains",
        "langchain.chains.moderation",
        "langchain_experimental",
        "langchain_experimental.comprehend_moderation",
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
            if inst.class_name not in _MODERATION_CLASSES:
                continue
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(f"langchain_moderation:{file_path}:{inst.line}"),
                    display_name=inst.class_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "langchain_moderation",
                        "guardrail_type": "moderation_chain",
                        "moderation_class": inst.class_name,
                        "detection_kind": "framework_native",
                    },
                    file_path=file_path,
                    line=inst.line,
                    snippet=f"{inst.class_name}(...)",
                    evidence_kind="ast_instantiation",
                )
            )

        return detected


__all__ = ["LangChainModerationAdapter"]
