"""AWS Bedrock Guardrails adapter.

Detects usage of AWS Bedrock's built-in guardrail feature via ``boto3``:
- ``bedrock_runtime.apply_guardrail(...)`` — the dedicated guardrail-check API.
- ``bedrock_runtime.converse(...)`` / ``.invoke_model(...)`` calls carrying a
  ``guardrailConfig={"guardrailIdentifier": ..., "guardrailVersion": ...}``
  kwarg — applies a guardrail inline to a model invocation.

Verified shapes: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-runtime/client/apply_guardrail.html

The AST parser (``nuguard.sbom.ast_parser``) drops dict-literal keyword
arguments entirely (``_extract_value`` has no ``ast.Dict`` branch), so
``guardrailConfig={...}`` never appears in ``call.args`` — the same
limitation ``claude_agent_sdk.py`` works around for ``mcp_servers=`` via a
line-scoped regex fallback on the raw source, reused here.
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter

_GUARDRAIL_CONFIG_RE = re.compile(r"\bguardrailConfig\s*=")
_BEDROCK_CALL_NAMES = {"converse", "invoke_model", "invoke_model_with_response_stream"}


class AWSBedrockGuardrailsAdapter(FrameworkAdapter):
    """Detect AWS Bedrock Guardrails usage via boto3 bedrock-runtime calls."""

    name = "aws_bedrock_guardrails"
    priority = 30
    handles_imports = ["boto3"]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        detected: list[ComponentDetection] = []
        lines = content.splitlines()

        for call in parse_result.function_calls:
            if call.function_name == "apply_guardrail":
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.GUARDRAIL,
                        canonical_name=canonicalize_text(
                            f"aws_bedrock_guardrails:{file_path}:{call.line}"
                        ),
                        display_name="Bedrock Guardrail",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": "aws_bedrock_guardrails",
                            "vendor": "aws",
                            "guardrail_type": "bedrock_guardrails",
                            "detection_kind": "framework_native",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=f"{call.receiver or 'bedrock_runtime'}.apply_guardrail(...)",
                        evidence_kind="ast_call",
                    )
                )
                continue

            if call.function_name not in _BEDROCK_CALL_NAMES:
                continue
            snippet_lines = lines[call.line - 1 : max(call.line_end, call.line)]
            if not _GUARDRAIL_CONFIG_RE.search("\n".join(snippet_lines)):
                continue
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(
                        f"aws_bedrock_guardrails:{file_path}:{call.line}"
                    ),
                    display_name="Bedrock Guardrail",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.80,
                    metadata={
                        "framework": "aws_bedrock_guardrails",
                        "vendor": "aws",
                        "guardrail_type": "bedrock_guardrails",
                        "detection_kind": "framework_native",
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"{call.receiver or 'bedrock_runtime'}.{call.function_name}(guardrailConfig=...)",
                    evidence_kind="regex",
                )
            )

        return detected


__all__ = ["AWSBedrockGuardrailsAdapter"]
