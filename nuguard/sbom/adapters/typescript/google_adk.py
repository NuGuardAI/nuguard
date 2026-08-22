"""Google ADK (Agent Development Kit) TypeScript Adapter for NuGuard SBOM.

Parsing is performed by ``nuguard.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).

Supports:
- LlmAgent, SequentialAgent, ParallelAgent, LoopAgent
- defineTool(), FunctionTool
- Gemini / Vertex AI model references
- Agent → Model and Agent → Tool relationship hints
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...core.ts_parser import TSParseResult, parse_typescript
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._ts_regex import TSFrameworkAdapter
from ._ts_regex import normalize_model_default as _normalize_model

_GOOGLE_ADK_PACKAGES = [
    "@google/adk",
    "@google-cloud/adk",
    "google-adk",
    "@genkit-ai/core",
    "@genkit-ai/ai",
    "@genkit-ai/googleai",
    "@genkit-ai/vertexai",
]

_GOOGLE_AI_PACKAGES = [
    "@google/generative-ai",
    "@google-cloud/aiplatform",
    "@google-cloud/vertexai",
]

_ALL_PACKAGES = _GOOGLE_ADK_PACKAGES + _GOOGLE_AI_PACKAGES

_AGENT_CLASSES = {"Agent", "LlmAgent", "SequentialAgent", "ParallelAgent", "LoopAgent"}
_TOOL_CALL_NAMES = {"defineTool", "tool", "createTool", "FunctionTool"}
_MODEL_CLASSES = {"GenerativeModel", "ChatModel", "VertexAI"}

# Matches factory functions like createNavigationTools, createWeatherTools, etc.
_CREATE_TOOLS_RE = re.compile(r"^create([A-Z][a-zA-Z]+)Tools$")

def _agent_subtype(class_name: str) -> str:
    if "Llm" in class_name:
        return "llm"
    if "Sequential" in class_name:
        return "sequential"
    if "Parallel" in class_name:
        return "parallel"
    if "Loop" in class_name:
        return "loop"
    return "generic"


class GoogleADKAdapter(TSFrameworkAdapter):
    """Detect Google ADK usage in TypeScript/JavaScript files."""

    name = "google_adk_ts"
    priority = 25
    handles_imports = _ALL_PACKAGES

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

        source = result.source or content
        detected: list[ComponentDetection] = [self._fw_node(file_path)]

        # --- Tools ---
        for call in result.function_calls:
            fn = call.function_name.split(".")[-1]
            if fn not in _TOOL_CALL_NAMES:
                continue
            tool_name = (
                self._resolve(call, "name", "toolName")
                or (self._clean(call.positional_args[0]) if call.positional_args else "")
                or self._assignment_name(source, call.line_start)
                or f"tool_{call.line_start}"
            )
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canonicalize_text(tool_name.lower()),
                    display_name=tool_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "creation_method": call.function_name,
                        "framework": "google-adk",
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=call.line_start,
                    snippet=call.source_snippet or f"{call.function_name}({tool_name!r})",
                    evidence_kind="ast_call",
                )
            )

        # --- Factory-function tool detection: createXxxTools() imports ---
        # Detects patterns like `import { createNavigationTools } from './tools/navigation'`
        seen_tool_caps: set[str] = set()
        for imp in result.imports:
            for name in imp.names:
                m = _CREATE_TOOLS_RE.match(name)
                if not m:
                    continue
                capability = m.group(1).lower()
                if capability in seen_tool_caps:
                    continue
                seen_tool_caps.add(capability)
                display = f"{capability} tools"
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=canonicalize_text(f"{capability}_tools"),
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.80,
                        metadata={
                            "creation_method": name,
                            "framework": "google-adk",
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=0,
                        snippet=f"import {{ {name} }} from '{imp.module}'",
                        evidence_kind="ast_import",
                    )
                )

        # --- Explicit model objects (GenerativeModel, VertexAI) ---
        model_canonicals: dict[str, str] = {}
        for inst in result.instantiations:
            if inst.class_name not in _MODEL_CLASSES:
                continue
            model_name = _normalize_model(
                self._resolve(inst, "model", "modelName", "name")
                or self._assignment_name(source, inst.line_start)
                or f"gemini_{inst.line_start}"
            )
            canon = canonicalize_text(model_name.lower())
            model_canonicals[inst.class_name] = canon
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=canon,
                    display_name=model_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "google-adk",
                        "class": inst.class_name,
                        "provider": "google",
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or "",
                    evidence_kind="ast_instantiation",
                )
            )

        # --- Agents ---
        for inst in result.instantiations:
            if inst.class_name not in _AGENT_CLASSES:
                continue
            agent_name = (
                self._resolve(inst, "name", "agentName")
                or self._assignment_name(source, inst.line_start)
                or f"{inst.class_name.lower()}_{inst.line_start}"
            )
            agent_canon = canonicalize_text(agent_name.lower())
            rels: list[RelationshipHint] = []

            # Model link
            model_val = _normalize_model(self._resolve(inst, "model"))
            if model_val:
                model_canon = canonicalize_text(model_val.lower())
                rels.append(
                    RelationshipHint(
                        source_canonical=agent_canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=model_canon,
                        target_type=ComponentType.MODEL,
                        relationship_type="USES",
                    )
                )
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=model_canon,
                        display_name=model_val,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": "google-adk",
                            "provider": "google",
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=inst.line_start,
                        snippet=f"model={model_val!r}",
                        evidence_kind="ast_instantiation",
                    )
                )

            tools_val = (inst.resolved_arguments or inst.arguments).get("tools")
            if tools_val:
                refs = (
                    tools_val
                    if isinstance(tools_val, list)
                    else [
                        t.strip().strip("'\"")
                        for t in str(tools_val).strip("[]").split(",")
                        if t.strip()
                    ]
                )
                for ref in refs:
                    rels.append(
                        RelationshipHint(
                            source_canonical=agent_canon,
                            source_type=ComponentType.AGENT,
                            target_canonical=canonicalize_text(str(ref).lower()),
                            target_type=ComponentType.TOOL,
                            relationship_type="CALLS",
                        )
                    )

            # Instruction → PROMPT
            instruction = self._resolve(inst, "instruction", "system_instruction")
            instruction_tvars = self._template_vars(instruction) if instruction else []
            if len(instruction) > 10:
                prompt_name = f"{agent_name} Instructions"
                prompt_canon = canonicalize_text(prompt_name.lower())
                rels.append(
                    RelationshipHint(
                        source_canonical=agent_canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=prompt_canon,
                        target_type=ComponentType.PROMPT,
                        relationship_type="USES",
                    )
                )
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.PROMPT,
                        canonical_name=prompt_canon,
                        display_name=prompt_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.92,
                        metadata={
                            "framework": "google-adk",
                            "prompt_type": "instruction",
                            "role": "system",
                            "content": instruction,
                            "char_count": len(instruction),
                            "is_template": bool(instruction_tvars),
                            "template_variables": instruction_tvars,
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=inst.line_start,
                        snippet=instruction[:80],
                        evidence_kind="ast_instantiation",
                    )
                )

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=agent_canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "class": inst.class_name,
                        "agent_type": _agent_subtype(inst.class_name),
                        "framework": "google-adk",
                        "has_instructions": len(instruction) > 10,
                        **(
                            {
                                "instructions_preview": instruction[:500],
                                "is_template": bool(instruction_tvars),
                                "template_variables": instruction_tvars,
                            }
                            if len(instruction) > 10
                            else {}
                        ),
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or "",
                    evidence_kind="ast_instantiation",
                    relationships=rels,
                )
            )

        # --- Agent fallback: runner.run() pattern without explicit new Agent(...) ---
        # Handles cases where the app uses runner.run(agent, ...) with agents defined
        # via factory functions or other patterns not in _AGENT_CLASSES.
        if not any(d.component_type == ComponentType.AGENT for d in detected):
            has_runner = any(
                "runner.run" in (c.function_name or "")
                or "runner.run" in (c.source_snippet or "")
                for c in result.function_calls
            )
            if has_runner:
                agent_name = Path(file_path).stem.replace("_", " ").replace("-", " ").title()
                agent_canon = canonicalize_text(agent_name.lower())
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.AGENT,
                        canonical_name=agent_canon,
                        display_name=agent_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.75,
                        metadata={
                            "agent_type": "generic",
                            "framework": "google-adk",
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=0,
                        snippet="runner.run(...)",
                        evidence_kind="ast_call",
                    )
                )

        return detected


GOOGLE_ADK_TS_PACKAGES = _GOOGLE_ADK_PACKAGES
GOOGLE_AI_TS_PACKAGES = _GOOGLE_AI_PACKAGES
ADK_AGENT_CLASS_NAMES = list(_AGENT_CLASSES)
