"""AWS Bedrock Guardrails TypeScript/JavaScript adapter.

Mirrors ``python/aws_bedrock_guardrails.py``:
- ``.applyGuardrail(...)`` (``@aws-sdk/client-bedrock-runtime``'s
  ``ApplyGuardrailCommand``, or the SDK v2-style method call) → GUARDRAIL.
- ``.converse(...)`` / ``.invokeModel(...)`` calls carrying a
  ``guardrailConfig: {...}`` field → GUARDRAIL, detected via a line-scoped
  regex on the raw source (nested object-literal field extraction is not
  reliably available from the shared TS parser, same rationale as the
  Python twin's ast.Dict limitation).
"""

from __future__ import annotations

import re
from typing import Any

from ...core.ts_parser import TSParseResult, parse_typescript
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ._ts_regex import TSFrameworkAdapter

_BEDROCK_PACKAGES = ["@aws-sdk/client-bedrock-runtime"]
_GUARDRAIL_CONFIG_RE = re.compile(r"\bguardrailConfig\s*:")
_BEDROCK_CALL_METHODS = {"converse", "invokeModel", "invokeModelWithResponseStream"}


class AWSBedrockGuardrailsTSAdapter(TSFrameworkAdapter):
    """Detect AWS Bedrock Guardrails usage in TypeScript/JavaScript files."""

    name = "aws_bedrock_guardrails_ts"
    priority = 30
    handles_imports = _BEDROCK_PACKAGES

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
        if not self._detect(result):
            return []

        detected: list[ComponentDetection] = []
        lines = (result.source or content).splitlines()

        for call in result.function_calls:
            method = call.method_name or call.function_name.split(".")[-1]
            if method in {"applyGuardrail", "ApplyGuardrailCommand"}:
                detected.append(
                    self._node(file_path, call.line_start, "client.applyGuardrail(...)", "ast_call")
                )
                continue
            if method not in _BEDROCK_CALL_METHODS:
                continue
            snippet_lines = lines[call.line_start - 1 : max(call.line_end, call.line_start)]
            if not _GUARDRAIL_CONFIG_RE.search("\n".join(snippet_lines)):
                continue
            detected.append(
                self._node(
                    file_path,
                    call.line_start,
                    f"client.{method}({{ guardrailConfig: ... }})",
                    "regex",
                    confidence=0.80,
                )
            )

        return detected

    def _node(
        self,
        file_path: str,
        line: int,
        snippet: str,
        evidence_kind: str,
        confidence: float = 0.90,
    ) -> ComponentDetection:
        return ComponentDetection(
            component_type=ComponentType.GUARDRAIL,
            canonical_name=canonicalize_text(f"aws_bedrock_guardrails:{file_path}:{line}"),
            display_name="Bedrock Guardrail",
            adapter_name=self.name,
            priority=self.priority,
            confidence=confidence,
            metadata={
                "framework": "aws_bedrock_guardrails",
                "vendor": "aws",
                "guardrail_type": "bedrock_guardrails",
                "detection_kind": "framework_native",
                "language": "typescript",
            },
            file_path=file_path,
            line=line,
            snippet=snippet,
            evidence_kind=evidence_kind,
        )


__all__ = ["AWSBedrockGuardrailsTSAdapter"]
