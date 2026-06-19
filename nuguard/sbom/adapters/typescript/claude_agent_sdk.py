"""Claude Agent SDK TypeScript adapter for NuGuard SBOM.

Parsing is performed by ``nuguard.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).

Supports:
- ``query({ prompt, options: { model, systemPrompt, allowedTools } })`` → AGENT node
- ``new ClaudeCode({ model, systemPrompt, allowedTools })`` → AGENT node
- Agent → Model, Agent → Prompt, Agent → Tool relationship hints
"""

from __future__ import annotations

from typing import Any

from ...core.ts_parser import TSParseResult, parse_typescript
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._ts_regex import TSFrameworkAdapter

_CLAUDE_CODE_PACKAGES = ["@anthropic-ai/claude-code"]

_AGENT_CLASSES = {"ClaudeCode", "ClaudeCodeClient", "ClaudeSDKClient"}
_QUERY_FN_NAMES = {"query"}


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


CLAUDE_AGENT_SDK_TS_PACKAGES = _CLAUDE_CODE_PACKAGES
