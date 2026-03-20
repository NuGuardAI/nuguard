"""GuardrailsAI framework adapter.

Detects usage of the ``guardrails-ai`` library:
- ``Guard(...)`` / ``AsyncGuard(...)`` instantiation → GUARDRAIL node
- ``@register_validator`` / ``@validate_call`` decorator → GUARDRAIL node
- ``from guardrails.hub import <ValidatorName>`` → one GUARDRAIL node per validator
"""

from __future__ import annotations

from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType


class GuardrailsAIAdapter(FrameworkAdapter):
    """Adapter for the guardrails-ai input/output validation library."""

    name = "guardrails_ai"
    priority = 25
    handles_imports = [
        "guardrails",
        "guardrails.hub",
        "guardrails.validators",
        "guardrails_ai",
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

        # 1. Guard() / AsyncGuard() instantiation → GUARDRAIL
        for inst in parse_result.instantiations:
            if inst.class_name not in {"Guard", "AsyncGuard"}:
                continue
            name = _clean(inst.assigned_to) or f"guard_{inst.line}"
            canon = canonicalize_text(f"guardrails:{name}")
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canon,
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.92,
                    metadata={"framework": "guardrails_ai", "guard_type": inst.class_name},
                    file_path=file_path,
                    line=inst.line,
                    snippet=f"{inst.class_name}(...)",
                    evidence_kind="ast_instantiation",
                )
            )

        # 2. @register_validator / @validate_call decorators → GUARDRAIL
        for call in parse_result.function_calls:
            if call.function_name not in {
                "register_validator",
                "validate_call",
                "full_validation_async",
            }:
                continue
            validator_name = _clean(
                call.args.get("name") or call.assigned_to or f"validator_{call.line}"
            )
            canon = canonicalize_text(f"guardrails:validator:{validator_name}")
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canon,
                    display_name=validator_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "framework": "guardrails_ai",
                        "source": "register_validator",
                        "decorator": call.function_name,
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"@{call.function_name}",
                    evidence_kind="ast_call",
                )
            )

        # 3. Hub imports — each imported validator class IS a distinct GUARDRAIL
        for imp in parse_result.imports:
            if not imp.module or not imp.module.startswith("guardrails.hub"):
                continue
            for validator_class in imp.names or []:
                if not validator_class:
                    continue
                canon = canonicalize_text(f"guardrails.hub:{validator_class.lower()}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.GUARDRAIL,
                        canonical_name=canon,
                        display_name=validator_class,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": "guardrails_ai",
                            "source": "hub_import",
                            "validator_class": validator_class,
                        },
                        file_path=file_path,
                        line=imp.line,
                        snippet=f"from guardrails.hub import {validator_class}",
                        evidence_kind="ast_import",
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
