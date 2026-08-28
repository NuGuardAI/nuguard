"""``google.golang.org/api/checks/v1alpha`` (Google Checks / AI Safety) adapter.

No dominant Go-native guardrails library equivalent to Python's
``guardrails-ai`` was found — verified via web research before writing this
adapter. Google's Checks/AI-Safety API has a real, documented Go client
instead, verified against pkg.go.dev::

    checksService, err := checks.NewService(ctx)
    result, err := checksService.Aisafety.ClassifyContent(request).Do()

- ``checks.NewService(ctx, ...)`` → FRAMEWORK node (presence evidence).
- ``.ClassifyContent(...)`` → GUARDRAIL node. Note ``go_parser``'s
  ``_split_callee`` keeps the *whole* dotted prefix as the receiver
  (``checksService.Aisafety.ClassifyContent`` → receiver =
  ``"checksService.Aisafety"``), so a fixed-string receiver match won't
  reliably match across arbitrary variable names. Matching is done on
  ``call.function_name == "ClassifyContent"`` alone — already
  import-scoped by ``handles_imports``, so this is safe.

A heuristic fallback mirroring Python's ``guardrail_heuristic.py``
(sanitize-function-called-immediately-before-an-outbound-call) is
deliberately **not** built here: that adapter relies on Python's native AST
block/statement structure for reliable statement-adjacency detection,
which ``go_parser``'s flat ``function_calls`` list can only approximate via
lossy line-number proximity — a materially noisier signal than its Python
counterpart. Left deferred, same as the original scoping in
docs/go-support.md, but for this more specific reason (a parser capability
gap, not "no library exists").
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ._go_base import GoFrameworkAdapter

_MODULE = "google.golang.org/api/checks/v1alpha"
_CLASSIFY_CALL = "ClassifyContent"


class GoGuardrailsAdapter(GoFrameworkAdapter):
    """Detect Google Checks/AI-Safety guardrail usage."""

    name = "go_guardrails"
    priority = 55
    handles_imports = [_MODULE]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = (
            parse_result
            if isinstance(parse_result, GoParseResult)
            else parse_go(content, file_path)
        )
        matched_import = self._matching_import(result)
        if matched_import is None:
            return []

        framework = self._fw_node(file_path, matched_import, display_name="Google Checks")
        framework.metadata.update({"framework": "go_guardrails"})
        detections: list[ComponentDetection] = [framework]

        for call in result.function_calls:
            if call.function_name != _CLASSIFY_CALL:
                continue

            canon = canonicalize_text(f"go_guardrails:{file_path}:{call.line}")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canon,
                    display_name="Checks Content Classification",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "go_guardrails",
                        "detection_kind": "framework_native",
                        "language": "golang",
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=call.source_snippet or f"{call.receiver}.ClassifyContent(...)",
                    evidence_kind="ast_call",
                )
            )

        return detections


__all__ = ["GoGuardrailsAdapter"]
