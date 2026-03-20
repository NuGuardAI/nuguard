from __future__ import annotations

import re
from dataclasses import dataclass

from xelo.adapters.base import DetectionAdapter, RegexAdapter
from xelo.types import ComponentType


@dataclass(frozen=True)
class BuiltinFrameworkSpec:
    adapter_name: str
    status: str
    priority: int
    patterns: tuple[str, ...]


_BUILTIN_FRAMEWORK_SPECS: tuple[BuiltinFrameworkSpec, ...] = (
    BuiltinFrameworkSpec(
        adapter_name="langgraph",
        status="builtin_v1",
        priority=10,
        patterns=(r"\blanggraph\b",),
    ),
    BuiltinFrameworkSpec(
        adapter_name="openai_agents",
        status="builtin_v1",
        priority=20,
        patterns=(r"\bopenai[ _-]?agents?\b",),
    ),
    BuiltinFrameworkSpec(
        adapter_name="autogen",
        status="builtin_v1",
        priority=30,
        patterns=(r"\bautogen\b",),
    ),
    BuiltinFrameworkSpec(
        adapter_name="semantic_kernel",
        status="builtin_v1",
        priority=40,
        patterns=(r"\bsemantic[_ -]?kernel\b",),
    ),
    BuiltinFrameworkSpec(
        adapter_name="crewai",
        status="builtin_v1",
        priority=50,
        patterns=(r"\bcrewai\b",),
    ),
    BuiltinFrameworkSpec(
        adapter_name="llamaindex",
        status="builtin_v1",
        priority=60,
        patterns=(r"\bllama[_ -]?index\b",),
    ),
)


def builtin_framework_specs() -> tuple[BuiltinFrameworkSpec, ...]:
    return _BUILTIN_FRAMEWORK_SPECS


def builtin_framework_adapters() -> tuple[DetectionAdapter, ...]:
    adapters: list[DetectionAdapter] = []
    for spec in _BUILTIN_FRAMEWORK_SPECS:
        compiled_patterns = tuple(re.compile(pattern, re.IGNORECASE) for pattern in spec.patterns)
        adapters.append(
            RegexAdapter(
                name=spec.adapter_name,
                component_type=ComponentType.FRAMEWORK,
                priority=spec.priority,
                patterns=compiled_patterns,
                canonical_name=f"framework:{spec.adapter_name}",
                metadata={
                    "framework": spec.adapter_name,
                    "implementation": "vela_builtin",
                },
            )
        )
    return tuple(adapters)
