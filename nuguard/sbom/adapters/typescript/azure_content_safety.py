"""Azure AI Content Safety / Prompt Shields TypeScript/JavaScript adapter.

Mirrors ``python/azure_content_safety.py``:
- ``@azure-rest/ai-content-safety`` client + ``.path("/text:analyze")``/
  ``analyzeText``/``analyzeImage`` calls → GUARDRAIL.
- Prompt Shields — REST-only, no SDK method — detected via a raw source
  match on the ``:shieldPrompt`` path, independent of any import.
"""

from __future__ import annotations

import re
from typing import Any

from ...core.ts_parser import TSParseResult, parse_typescript
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ._ts_regex import TSFrameworkAdapter

_CONTENT_SAFETY_PACKAGES = ["@azure-rest/ai-content-safety", "@azure/ai-content-safety"]
# Prompt Shields is REST-only (no SDK method) — trigger this adapter on
# common HTTP-client packages too, the same way Python's
# guardrail_heuristic.py keys outbound-call heuristics off requests/httpx.
_HTTP_PACKAGES = ["axios", "node-fetch"]
_SDK_METHODS = {"analyzeText", "analyzeImage"}
_PROMPT_SHIELD_RE = re.compile(r":shieldPrompt\b")


class AzureContentSafetyTSAdapter(TSFrameworkAdapter):
    """Detect Azure AI Content Safety / Prompt Shields usage in TS/JS files."""

    name = "azure_content_safety_ts"
    priority = 30
    handles_imports = _CONTENT_SAFETY_PACKAGES + _HTTP_PACKAGES

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result: TSParseResult = (
            parse_result
            if isinstance(parse_result, TSParseResult)
            else parse_typescript(content, file_path)
        )
        detected: list[ComponentDetection] = []
        source = result.source or content

        has_sdk_import = any(
            pkg in imp.module or imp.module == pkg
            for imp in result.imports
            for pkg in _CONTENT_SAFETY_PACKAGES
        )
        if has_sdk_import:
            for call in result.function_calls:
                method = call.method_name or call.function_name.split(".")[-1]
                if method not in _SDK_METHODS:
                    continue
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.GUARDRAIL,
                        canonical_name=canonicalize_text(
                            f"azure_content_safety:{file_path}:{call.line_start}"
                        ),
                        display_name="Azure Content Safety",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={
                            "framework": "azure_content_safety",
                            "vendor": "azure",
                            "guardrail_type": "content_safety",
                            "detection_kind": "framework_native",
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=call.line_start,
                        snippet=call.source_snippet or f"client.{method}(...)",
                        evidence_kind="ast_call",
                    )
                )

        m = _PROMPT_SHIELD_RE.search(source)
        if m:
            line = source.count("\n", 0, m.start()) + 1
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(
                        f"azure_content_safety:prompt_shields:{file_path}:{line}"
                    ),
                    display_name="Azure Prompt Shields",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.6,
                    metadata={
                        "framework": "azure_content_safety",
                        "vendor": "azure",
                        "guardrail_type": "prompt_shields",
                        "detection_kind": "heuristic",
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=line,
                    snippet="POST .../contentsafety/text:shieldPrompt",
                    evidence_kind="regex",
                )
            )

        return detected


__all__ = ["AzureContentSafetyTSAdapter"]
