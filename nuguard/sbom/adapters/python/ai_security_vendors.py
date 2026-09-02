"""Third-party AI-security vendor guardrail adapter.

One file for several small, related-but-distinct products (mirrors how
``datastores.py`` and ``llm_clients.py`` each handle multiple products in a
single module):

- **Palo Alto Prisma AIRS** — ``import aisecurity`` (pip: ``pan-aisecurity``).
  https://pypi.org/project/pan-aisecurity/
- **Protect AI Guardian** — ``from guardian_client import GuardianAPIClient``
  (pip: ``guardian-client``). https://pypi.org/project/guardian-client/
- **Presidio** — ``presidio_analyzer.AnalyzerEngine`` /
  ``presidio_anonymizer.AnonymizerEngine``.
- **llm-guard** — ``import llm_guard``, ``scan_prompt(...)`` / ``scan_output(...)``.
- **Rebuff** — ``from rebuff import Rebuff`` / ``RebuffSdk``.
- **NeMo Guardrails** — ``from nemoguardrails import LLMRails, RailsConfig``.
  Promotes the existing gap-fill-only keyword
  (``nuguard/sbom/core/gap_fill/categories.py``) to real deterministic
  detection.
- **Lakera Guard** — no SDK exists; it's REST-only via ``requests`` to
  ``api.lakera.ai``, so it's detected as a raw source match on that domain,
  independent of any import (mirrors the Azure Prompt Shields REST-only
  detection in ``azure_content_safety.py``).

Robust Intelligence and Wiz are deliberately not covered here — no verified,
stable, importable SDK could be confirmed for Robust Intelligence, and Wiz is
a posture-management/scanning platform rather than an inference-path
control, so it does not belong in GUARDRAIL detection at all.
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter

_LAKERA_RE = re.compile(r"api\.lakera\.ai")

_PRESIDIO_CLASSES = {"AnalyzerEngine", "AnonymizerEngine"}
_LLM_GUARD_FUNCS = {"scan_prompt", "scan_output"}
_REBUFF_CLASSES = {"Rebuff", "RebuffSdk"}
_NEMO_CLASSES = {"LLMRails", "RailsConfig"}


def _has_import(parse_result: Any, module_prefix: str) -> bool:
    return any(
        (imp.module or "") == module_prefix or (imp.module or "").startswith(module_prefix + ".")
        for imp in parse_result.imports
    )


def _imported_names(parse_result: Any, module_prefix: str) -> set[str]:
    names: set[str] = set()
    for imp in parse_result.imports:
        module = imp.module or ""
        if module == module_prefix or module.startswith(module_prefix + "."):
            names.update(imp.names or [])
    return names


class AISecurityVendorsAdapter(FrameworkAdapter):
    """Detect third-party AI-security vendor guardrail libraries."""

    name = "ai_security_vendors"
    priority = 30
    handles_imports = [
        "aisecurity",
        "guardian_client",
        "presidio_analyzer",
        "presidio_anonymizer",
        "llm_guard",
        "rebuff",
        "nemoguardrails",
        "requests",
        "httpx",
        "aiohttp",
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

        # Palo Alto Prisma AIRS
        if _has_import(parse_result, "aisecurity"):
            init_calls = [
                c
                for c in parse_result.function_calls
                if c.function_name == "init" and c.receiver == "aisecurity"
            ]
            if init_calls:
                for call in init_calls:
                    detected.append(
                        self._node(
                            "prisma_airs",
                            "Palo Alto Prisma AIRS",
                            file_path,
                            call.line,
                            "aisecurity.init(...)",
                            "ast_call",
                            0.85,
                        )
                    )
            else:
                imp_line = next(
                    (i.line for i in parse_result.imports if (i.module or "").startswith("aisecurity")),
                    0,
                )
                detected.append(
                    self._node(
                        "prisma_airs",
                        "Palo Alto Prisma AIRS",
                        file_path,
                        imp_line,
                        "import aisecurity",
                        "ast_import",
                        0.8,
                    )
                )

        # Protect AI Guardian
        if "GuardianAPIClient" in _imported_names(parse_result, "guardian_client"):
            imp_line = next(
                (i.line for i in parse_result.imports if (i.module or "") == "guardian_client"),
                0,
            )
            detected.append(
                self._node(
                    "protect_ai_guardian",
                    "Protect AI Guardian",
                    file_path,
                    imp_line,
                    "from guardian_client import GuardianAPIClient",
                    "ast_import",
                    0.85,
                )
            )

        # Presidio
        for inst in parse_result.instantiations:
            if inst.class_name not in _PRESIDIO_CLASSES:
                continue
            detected.append(
                self._node(
                    "presidio",
                    "Presidio",
                    file_path,
                    inst.line,
                    f"{inst.class_name}(...)",
                    "ast_instantiation",
                    0.88,
                )
            )

        # llm-guard
        if _has_import(parse_result, "llm_guard"):
            scan_calls = [c for c in parse_result.function_calls if c.function_name in _LLM_GUARD_FUNCS]
            if scan_calls:
                for call in scan_calls:
                    detected.append(
                        self._node(
                            "llm_guard",
                            "llm-guard",
                            file_path,
                            call.line,
                            f"{call.function_name}(...)",
                            "ast_call",
                            0.85,
                        )
                    )
            else:
                imp_line = next(
                    (i.line for i in parse_result.imports if (i.module or "").startswith("llm_guard")),
                    0,
                )
                detected.append(
                    self._node(
                        "llm_guard",
                        "llm-guard",
                        file_path,
                        imp_line,
                        "import llm_guard",
                        "ast_import",
                        0.75,
                    )
                )

        # Rebuff
        for inst in parse_result.instantiations:
            if inst.class_name not in _REBUFF_CLASSES:
                continue
            detected.append(
                self._node(
                    "rebuff",
                    "Rebuff",
                    file_path,
                    inst.line,
                    f"{inst.class_name}(...)",
                    "ast_instantiation",
                    0.85,
                )
            )

        # NeMo Guardrails
        for inst in parse_result.instantiations:
            if inst.class_name not in _NEMO_CLASSES:
                continue
            confidence = 0.85 if inst.class_name == "LLMRails" else 0.75
            detected.append(
                self._node(
                    "nemo_guardrails",
                    "NeMo Guardrails",
                    file_path,
                    inst.line,
                    f"{inst.class_name}(...)",
                    "ast_instantiation",
                    confidence,
                )
            )

        # Lakera Guard — REST-only, no SDK
        m = _LAKERA_RE.search(content)
        if m:
            line = content.count("\n", 0, m.start()) + 1
            det = self._node(
                "lakera_guard",
                "Lakera Guard",
                file_path,
                line,
                "POST https://api.lakera.ai/...",
                "regex",
                0.6,
            )
            det.metadata["detection_kind"] = "heuristic"
            detected.append(det)

        return detected

    def _node(
        self,
        vendor_key: str,
        display_name: str,
        file_path: str,
        line: int,
        snippet: str,
        evidence_kind: str,
        confidence: float,
    ) -> ComponentDetection:
        return ComponentDetection(
            component_type=ComponentType.GUARDRAIL,
            canonical_name=canonicalize_text(f"{vendor_key}:{file_path}:{line}"),
            display_name=display_name,
            adapter_name=self.name,
            priority=self.priority,
            confidence=confidence,
            metadata={
                "framework": vendor_key,
                "guardrail_type": vendor_key,
                "detection_kind": "framework_native",
            },
            file_path=file_path,
            line=line,
            snippet=snippet,
            evidence_kind=evidence_kind,
        )


__all__ = ["AISecurityVendorsAdapter"]
