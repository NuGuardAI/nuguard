"""Vercel AI SDK (the ``ai`` npm package) TypeScript/JavaScript adapter.

Detects the SDK's core primitives used to build a chat/agent endpoint:

- ``tool({ description, inputSchema/parameters, execute })`` → TOOL
- ``streamText({...})`` / ``generateText({...})`` → AGENT (the chat handler
  itself — Vercel AI SDK has no separate "Agent" class; the streamText/
  generateText call *is* the agent invocation)
- A literal provider call passed as ``model:`` (e.g. ``openai('gpt-4o-mini')``,
  ``anthropic('claude-3-5-sonnet')``) → MODEL with a resolved model id
- A custom-provider factory (``createOpenAICompatible``, ``createOpenAI``,
  ``createAnthropic``, ...) → MODEL, lower confidence, since the actual
  per-request model id is frequently a runtime value (env var, config
  lookup) that can't be resolved statically

Tool/model attribution to an agent is file-scoped: every ``tool()`` and
resolved MODEL found in the same file as a ``streamText``/``generateText``
call is treated as reachable from that call. This is a deliberate
simplification (there is no cross-call scope tracking in the TS parser) —
correct for the common single-route-per-file layout (e.g. a single Express/
Next.js route handler building its tool set inline, as in OWASP Juice
Shop's ``routes/chat.ts``) and only over-attributes in files with multiple
independent streamText/generateText calls sharing unrelated tool sets.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...core.ts_parser import TSFunctionCall, TSParseResult, parse_typescript
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._ts_regex import TSFrameworkAdapter

_CORE_PACKAGE = "ai"
_PROVIDER_PACKAGES = (
    "@ai-sdk/openai",
    "@ai-sdk/anthropic",
    "@ai-sdk/google",
    "@ai-sdk/openai-compatible",
    "@ai-sdk/azure",
    "@ai-sdk/amazon-bedrock",
    "@ai-sdk/mistral",
    "@ai-sdk/groq",
    "@ai-sdk/cohere",
    "@ai-sdk/xai",
)

_AGENT_CALL_NAMES = {"streamText", "generateText", "streamObject", "generateObject"}
_TOOL_FUNC_NAME = "tool"
_PROVIDER_FACTORY_NAMES = {
    "createOpenAICompatible",
    "createOpenAI",
    "createAnthropic",
    "createAzure",
    "createVertex",
    "createGoogleGenerativeAI",
    "createAmazonBedrock",
    "createMistral",
    "createGroq",
}
# A literal `openai('gpt-4o-mini')`-style provider call passed as `model:`.
_LITERAL_PROVIDER_RE = re.compile(
    r"\b(openai|anthropic|google|azure|bedrock|mistral|groq|cohere|xai|deepseek)\s*"
    r"\(\s*['\"]([\w./:-]+)['\"]"
)
# Zod field definitions inside an inputSchema/parameters object, e.g.
# `query: z.string().describe(...)` or `discount: z.number()`.
_ZOD_FIELD_RE = re.compile(r"(\w+)\s*:\s*z\.(\w+)\s*\(")
_TOOL_PROP_NAME_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*:\s*tool\s*\(")


class VercelAISDKTSAdapter(TSFrameworkAdapter):
    """Detect Vercel AI SDK tool/agent/model usage in TS/JS."""

    name = "vercel_ai_sdk_ts"
    priority = 25
    handles_imports = [_CORE_PACKAGE, *_PROVIDER_PACKAGES]

    def can_handle(self, imports_present: set[str]) -> bool:
        # "ai" is short enough that the base class's substring match would
        # false-positive on unrelated imports (e.g. "email", "domain",
        # "container" all contain "ai") — require an exact match for it.
        if _CORE_PACKAGE in imports_present:
            return True
        return any(pkg in imports_present for pkg in _PROVIDER_PACKAGES)

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
        lines = (result.source or content).splitlines()
        detected: list[ComponentDetection] = []

        # ── TOOL detection ───────────────────────────────────────────────
        tool_canons: list[str] = []
        for call in result.function_calls:
            if call.function_name != _TOOL_FUNC_NAME:
                continue
            tool_name = self._tool_name(lines, call)
            description = self._resolve(call, "description") or f"{tool_name} tool"
            canon = canonicalize_text(f"vercel_ai_sdk:tool:{tool_name}:{file_path}:{call.line_start}")
            tool_canons.append(canon)
            schema_text = (call.arguments or {}).get("inputSchema") or (call.arguments or {}).get(
                "parameters"
            ) or ""
            parameters = {m.group(1): m.group(2) for m in _ZOD_FIELD_RE.finditer(str(schema_text))}
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canon,
                    display_name=tool_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "framework": "vercel_ai_sdk",
                        "description": description,
                        "parameters": parameters,
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=call.line_start,
                    snippet=call.source_snippet or f"tool({{ description: {description!r} }})",
                    evidence_kind="ast_call",
                )
            )

        # ── MODEL detection ──────────────────────────────────────────────
        model_canon: str | None = None
        model_confidence = 0.0
        for call in result.function_calls:
            if call.function_name in _AGENT_CALL_NAMES:
                model_arg = str((call.arguments or {}).get("model") or "")
                m = _LITERAL_PROVIDER_RE.search(model_arg)
                if m:
                    provider, model_id = m.group(1), m.group(2)
                    model_canon = canonicalize_text(f"vercel_ai_sdk:model:{provider}:{model_id}")
                    model_confidence = 0.85
                    detected.append(
                        ComponentDetection(
                            component_type=ComponentType.MODEL,
                            canonical_name=model_canon,
                            display_name=model_id,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=model_confidence,
                            metadata={
                                "framework": "vercel_ai_sdk",
                                "provider": provider,
                                "language": "typescript",
                            },
                            file_path=file_path,
                            line=call.line_start,
                            snippet=f"{provider}({model_id!r})",
                            evidence_kind="ast_call",
                        )
                    )
                    break
        if model_canon is None:
            for call in result.function_calls:
                if call.function_name not in _PROVIDER_FACTORY_NAMES:
                    continue
                provider_name = self._resolve(call, "name") or call.function_name
                base_url = self._resolve(call, "baseURL")
                model_canon = canonicalize_text(
                    f"vercel_ai_sdk:provider:{call.function_name}:{file_path}:{call.line_start}"
                )
                model_confidence = 0.6
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=model_canon,
                        display_name=f"{provider_name} (dynamic model)",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=model_confidence,
                        metadata={
                            "framework": "vercel_ai_sdk",
                            "provider_factory": call.function_name,
                            "base_url": base_url,
                            "detection_kind": "dynamic_provider",
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=call.line_start,
                        snippet=call.source_snippet or f"{call.function_name}(...)",
                        evidence_kind="ast_call",
                    )
                )
                break

        # ── AGENT detection ──────────────────────────────────────────────
        agent_calls = [c for c in result.function_calls if c.function_name in _AGENT_CALL_NAMES]
        seen_lines: set[int] = set()
        for call in agent_calls:
            if call.line_start in seen_lines:
                continue
            seen_lines.add(call.line_start)
            agent_name = f"{self._file_label(file_path)} Assistant"
            canon = canonicalize_text(f"vercel_ai_sdk:agent:{file_path}:{call.line_start}")
            rels: list[RelationshipHint] = []
            for tool_canon in tool_canons:
                rels.append(
                    RelationshipHint(
                        source_canonical=canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=tool_canon,
                        target_type=ComponentType.TOOL,
                        relationship_type="CALLS",
                    )
                )
            if model_canon:
                rels.append(
                    RelationshipHint(
                        source_canonical=canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=model_canon,
                        target_type=ComponentType.MODEL,
                        relationship_type="USES",
                    )
                )
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.75,
                    metadata={
                        "framework": "vercel_ai_sdk",
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=call.line_start,
                    snippet=call.source_snippet or f"{call.function_name}({{ ... }})",
                    evidence_kind="ast_call",
                    relationships=rels,
                )
            )

        return detected

    @staticmethod
    def _tool_name(lines: list[str], call: TSFunctionCall) -> str:
        """Best-effort object-literal property name a tool() call is assigned to.

        ``tool()``'s own arguments carry no name — the name is the object key
        it's a value of, e.g. ``searchProducts: tool({...})``.
        """
        if 1 <= call.line_start <= len(lines):
            m = _TOOL_PROP_NAME_RE.search(lines[call.line_start - 1])
            if m:
                return m.group(1)
        return f"tool_{call.line_start}"

    @staticmethod
    def _file_label(file_path: str) -> str:
        stem = Path(file_path).stem
        words = re.split(r"[_\-.]+", stem)
        return " ".join(w.capitalize() for w in words if w) or "AI"


__all__ = ["VercelAISDKTSAdapter"]
