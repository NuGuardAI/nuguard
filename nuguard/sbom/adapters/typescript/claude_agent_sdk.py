"""Claude Agent SDK TypeScript adapter for NuGuard SBOM.

Parsing is performed by ``nuguard.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).

Supports:
- ``query({ prompt, options: { model, systemPrompt, allowedTools } })`` → AGENT node
- ``new ClaudeCode({ model, systemPrompt, allowedTools })`` → AGENT node
- Agent → Model, Agent → Prompt, Agent → Tool relationship hints
"""

from __future__ import annotations

import re
from typing import Any

from ...core.ts_parser import TSParseResult, parse_typescript
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._ts_regex import TSFrameworkAdapter

_CLAUDE_CODE_PACKAGES = ["@anthropic-ai/claude-code"]

_AGENT_CLASSES = {"ClaudeCode", "ClaudeCodeClient", "ClaudeSDKClient"}
_QUERY_FN_NAMES = {"query"}

# hooks:/canUseTool: are object/callable fields the shared TS parser does not
# reliably resolve nested keys for — detected via a line-scoped regex on the
# raw source instead, same rationale as the Python twin's ast.Dict gap.
_HOOKS_RE = re.compile(r"\bhooks\s*:")
_CAN_USE_TOOL_RE = re.compile(r"\bcanUseTool\s*:")


def _guardrail_flags(source: str, line_start: int, line_end: int) -> tuple[bool, bool]:
    lines = source.splitlines()
    snippet = "\n".join(lines[max(line_start - 1, 0) : max(line_end, line_start)])
    return bool(_HOOKS_RE.search(snippet)), bool(_CAN_USE_TOOL_RE.search(snippet))


class ClaudeAgentSDKTSAdapter(TSFrameworkAdapter):
    """Detect Claude Agent SDK usage in TypeScript/JavaScript files."""

    name = "claude_agent_sdk_ts"
    priority = 20
    handles_imports = _CLAUDE_CODE_PACKAGES

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

        # --- query() function calls (most common pattern) ---
        for call in result.function_calls:
            fn = call.function_name.split(".")[-1]
            if fn not in _QUERY_FN_NAMES:
                continue
            # Skip method calls like agent.query(...)
            if call.receiver_chain:
                continue

            agent_name = self._assignment_name(source, call.line_start) or f"query_agent_{call.line_start}"
            agent_canon = canonicalize_text(agent_name.lower())

            rels: list[RelationshipHint] = []

            # Resolve model — may be nested under options.model
            model_name = self._resolve(call, "model")
            if model_name:
                model_canon = canonicalize_text(model_name.lower())
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
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={
                            "framework": "@anthropic-ai/claude-code",
                            "provider": "anthropic",
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=call.line_start,
                        snippet=f"model={model_name!r}",
                        evidence_kind="ast_call",
                    )
                )

            # Resolve system prompt
            system_prompt = self._resolve(call, "systemPrompt", "system_prompt")
            if len(system_prompt) > 10:
                prompt_name = f"{agent_name} System Prompt"
                prompt_canon = canonicalize_text(prompt_name.lower())
                template_vars = self._template_vars(system_prompt)
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
                        confidence=0.88,
                        metadata={
                            "framework": "@anthropic-ai/claude-code",
                            "role": "system",
                            "content": system_prompt,
                            "char_count": len(system_prompt),
                            "is_template": bool(template_vars),
                            "template_variables": template_vars,
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=call.line_start,
                        snippet=system_prompt[:80],
                        evidence_kind="ast_call",
                    )
                )

            # Resolve allowed tools
            tools_val = (
                (call.resolved_arguments or {}).get("allowedTools")
                or (call.arguments or {}).get("allowedTools")
                or (call.resolved_arguments or {}).get("allowed_tools")
                or (call.arguments or {}).get("allowed_tools")
            )
            if tools_val:
                refs = (
                    tools_val
                    if isinstance(tools_val, list)
                    else [t.strip().strip("'\"") for t in str(tools_val).strip("[]").split(",") if t.strip()]
                )
                for ref in refs:
                    ref_str = self._clean(str(ref))
                    if ref_str:
                        rels.append(
                            RelationshipHint(
                                source_canonical=agent_canon,
                                source_type=ComponentType.AGENT,
                                target_canonical=canonicalize_text(f"claude_agent_sdk:tool:{ref_str}"),
                                target_type=ComponentType.TOOL,
                                relationship_type="CALLS",
                            )
                        )

            has_hooks, has_can_use_tool = _guardrail_flags(source, call.line_start, call.line_end)
            rels.extend(
                self._guardrail_hints(file_path, call.line_start, agent_canon, has_hooks, has_can_use_tool)
            )
            for det in self._guardrail_detections(file_path, call.line_start, has_hooks, has_can_use_tool):
                detected.append(det)

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=agent_canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.82 if not model_name else 0.90,
                    metadata={
                        "framework": "@anthropic-ai/claude-code",
                        "agent_function": "query",
                        "is_oneshot": True,
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=call.line_start,
                    snippet=call.source_snippet or "query({...})",
                    evidence_kind="ast_call",
                    relationships=rels,
                )
            )

        # --- new ClaudeCode({...}) / new ClaudeCodeClient({...}) instantiations ---
        for inst in result.instantiations:
            if inst.class_name not in _AGENT_CLASSES:
                continue

            agent_name = (
                self._assignment_name(source, inst.line_start)
                or f"claude_agent_{inst.line_start}"
            )
            agent_canon = canonicalize_text(agent_name.lower())

            rels = []

            model_name = self._resolve(inst, "model")
            if model_name:
                model_canon = canonicalize_text(model_name.lower())
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
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": "@anthropic-ai/claude-code",
                            "provider": "anthropic",
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=inst.line_start,
                        snippet=f"model={model_name!r}",
                        evidence_kind="ast_instantiation",
                    )
                )

            system_prompt = self._resolve(inst, "systemPrompt", "system_prompt")
            template_vars = self._template_vars(system_prompt) if system_prompt else []
            if len(system_prompt) > 10:
                prompt_name = f"{agent_name} System Prompt"
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
                        confidence=0.90,
                        metadata={
                            "framework": "@anthropic-ai/claude-code",
                            "role": "system",
                            "content": system_prompt,
                            "char_count": len(system_prompt),
                            "is_template": bool(template_vars),
                            "template_variables": template_vars,
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=inst.line_start,
                        snippet=system_prompt[:80],
                        evidence_kind="ast_instantiation",
                    )
                )

            tools_val = (
                (inst.resolved_arguments or {}).get("allowedTools")
                or (inst.arguments or {}).get("allowedTools")
                or (inst.resolved_arguments or {}).get("allowed_tools")
                or (inst.arguments or {}).get("allowed_tools")
            )
            if tools_val:
                refs = (
                    tools_val
                    if isinstance(tools_val, list)
                    else [t.strip().strip("'\"") for t in str(tools_val).strip("[]").split(",") if t.strip()]
                )
                for ref in refs:
                    ref_str = self._clean(str(ref))
                    if ref_str:
                        rels.append(
                            RelationshipHint(
                                source_canonical=agent_canon,
                                source_type=ComponentType.AGENT,
                                target_canonical=canonicalize_text(f"claude_agent_sdk:tool:{ref_str}"),
                                target_type=ComponentType.TOOL,
                                relationship_type="CALLS",
                            )
                        )

            has_hooks, has_can_use_tool = _guardrail_flags(source, inst.line_start, inst.line_end)
            rels.extend(
                self._guardrail_hints(file_path, inst.line_start, agent_canon, has_hooks, has_can_use_tool)
            )
            for det in self._guardrail_detections(file_path, inst.line_start, has_hooks, has_can_use_tool):
                detected.append(det)

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
                        "framework": "@anthropic-ai/claude-code",
                        "language": "typescript",
                        **(
                            {"instructions_preview": system_prompt[:500]}
                            if len(system_prompt) > 10
                            else {}
                        ),
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or f"new {inst.class_name}({{...}})",
                    evidence_kind="ast_instantiation",
                    relationships=rels,
                )
            )

        return detected

    @staticmethod
    def _guardrail_hints(
        file_path: str,
        line: int,
        agent_canon: str,
        has_hooks: bool,
        has_can_use_tool: bool,
    ) -> list[RelationshipHint]:
        hints: list[RelationshipHint] = []
        if has_hooks:
            hints.append(
                RelationshipHint(
                    source_canonical=canonicalize_text(f"claude_agent_sdk:guardrail:hooks:{line}"),
                    source_type=ComponentType.GUARDRAIL,
                    target_canonical=agent_canon,
                    target_type=ComponentType.AGENT,
                    relationship_type="PROTECTS",
                )
            )
        if has_can_use_tool:
            hints.append(
                RelationshipHint(
                    source_canonical=canonicalize_text(f"claude_agent_sdk:guardrail:can_use_tool:{line}"),
                    source_type=ComponentType.GUARDRAIL,
                    target_canonical=agent_canon,
                    target_type=ComponentType.AGENT,
                    relationship_type="PROTECTS",
                )
            )
        return hints

    @staticmethod
    def _guardrail_detections(
        file_path: str,
        line: int,
        has_hooks: bool,
        has_can_use_tool: bool,
    ) -> list[ComponentDetection]:
        dets: list[ComponentDetection] = []
        if has_hooks:
            dets.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(f"claude_agent_sdk:guardrail:hooks:{line}"),
                    display_name="Claude Agent SDK Hooks",
                    adapter_name="claude_agent_sdk_ts",
                    priority=20,
                    confidence=0.85,
                    metadata={
                        "framework": "@anthropic-ai/claude-code",
                        "guardrail_type": "hooks",
                        "detection_kind": "framework_native",
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=line,
                    snippet="hooks: { PreToolUse: [...] }",
                    evidence_kind="regex",
                )
            )
        if has_can_use_tool:
            dets.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(f"claude_agent_sdk:guardrail:can_use_tool:{line}"),
                    display_name="Claude Agent SDK canUseTool",
                    adapter_name="claude_agent_sdk_ts",
                    priority=20,
                    confidence=0.80,
                    metadata={
                        "framework": "@anthropic-ai/claude-code",
                        "guardrail_type": "can_use_tool",
                        "detection_kind": "framework_native",
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=line,
                    snippet="canUseTool: ...",
                    evidence_kind="regex",
                )
            )
        return dets


CLAUDE_AGENT_SDK_TS_PACKAGES = _CLAUDE_CODE_PACKAGES
