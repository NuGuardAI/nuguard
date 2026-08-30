"""Claude Agent SDK adapter.

Detects usage of the ``claude-agent-sdk`` (``claude_agent_sdk``) Python SDK:
- ``ClaudeAgentOptions(model=..., allowed_tools=[...], system_prompt=...)`` → MODEL, TOOL, PROMPT nodes
- ``ClaudeSDKClient(options=...)`` → AGENT node with relationships
- ``query(prompt=..., options=...)`` → AGENT node (one-shot pattern)
- ``AgentDefinition(name=..., ...)`` → subagent AGENT nodes
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint
from ..models_kb import get_model_details, infer_provider

_TEMPLATE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Regex fallbacks for patterns the AST parser misses (async-with, complex nested args)
_CLAUDE_SDK_CLIENT_RE = re.compile(r"\bClaudeSDKClient\s*\(")
_MCP_SERVERS_RE = re.compile(r"\bmcp_servers\s*=")
_HOOKS_RE = re.compile(r"\bhooks\s*=")
_CAN_USE_TOOL_RE = re.compile(r"\bcan_use_tool\s*=")


class ClaudeAgentSDKAdapter(FrameworkAdapter):
    """Adapter for the Claude Agent SDK (claude-agent-sdk / claude_agent_sdk)."""

    name = "claude_agent_sdk"
    priority = 20
    handles_imports = ["claude_agent_sdk"]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        detected: list[ComponentDetection] = [self._framework_node(file_path)]
        content_lines = content.splitlines()

        # options_vars tracks ClaudeAgentOptions instantiations by their assigned variable name
        # so that ClaudeSDKClient(options=options) can inherit model/tools/prompt metadata.
        options_vars: dict[str, dict[str, Any]] = {}

        # Pass 1 — ClaudeAgentOptions: extract model, system_prompt, allowed_tools, mcp_servers
        for inst in parse_result.instantiations:
            if inst.class_name != "ClaudeAgentOptions":
                continue
            args = inst.args or {}
            model_name = _clean(args.get("model", ""))
            system_prompt = _clean(args.get("system_prompt", ""))
            allowed_tools = args.get("allowed_tools") or []
            permission_mode = _clean(args.get("permission_mode", ""))
            mcp_servers_raw = args.get("mcp_servers")

            # `hooks=` and `can_use_tool=` are dict/callable kwargs the AST
            # parser drops entirely (no ast.Dict branch in _extract_value) —
            # same limitation mcp_servers works around above, via a
            # line-scoped regex fallback on the raw source.
            opts_snippet = "\n".join(content_lines[inst.line - 1 : max(inst.line_end, inst.line)])
            has_hooks = bool(_HOOKS_RE.search(opts_snippet))
            has_can_use_tool = bool(_CAN_USE_TOOL_RE.search(opts_snippet))

            # Store for cross-referencing in Pass 2
            if inst.assigned_to:
                options_vars[inst.assigned_to] = {
                    "model": model_name,
                    "system_prompt": system_prompt,
                    "allowed_tools": allowed_tools,
                    "permission_mode": permission_mode,
                    "has_hooks": has_hooks,
                    "has_can_use_tool": has_can_use_tool,
                    "line": inst.line,
                }

            # MODEL node
            if model_name:
                provider = infer_provider(model_name)
                model_canon = canonicalize_text(model_name.lower())
                model_details = get_model_details(model_name, provider)
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=model_canon,
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.92,
                        metadata={
                            "framework": "claude_agent_sdk",
                            "provider": provider,
                            **{k: v for k, v in model_details.items() if v is not None},
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"ClaudeAgentOptions(model={model_name!r})",
                        evidence_kind="ast_instantiation",
                    )
                )

            # PROMPT node from system_prompt (only if substantive)
            if len(system_prompt) >= 40:
                prompt_display = (
                    f"{inst.assigned_to} System Prompt"
                    if inst.assigned_to
                    else "Claude Agent System Prompt"
                )
                prompt_canon = canonicalize_text(prompt_display.lower())
                template_vars = _TEMPLATE_VAR_RE.findall(system_prompt)
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.PROMPT,
                        canonical_name=prompt_canon,
                        display_name=prompt_display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "role": "system",
                            "content": system_prompt,
                            "char_count": len(system_prompt),
                            "is_template": bool(template_vars),
                            "template_variables": template_vars,
                            "framework": "claude_agent_sdk",
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=system_prompt[:80],
                        evidence_kind="ast_instantiation",
                    )
                )

            # TOOL nodes from allowed_tools list
            if isinstance(allowed_tools, list):
                for tool_ref in allowed_tools:
                    if isinstance(tool_ref, str) and not tool_ref.startswith("$"):
                        tool_canon = canonicalize_text(f"claude_agent_sdk:tool:{tool_ref}")
                        detected.append(
                            ComponentDetection(
                                component_type=ComponentType.TOOL,
                                canonical_name=tool_canon,
                                display_name=tool_ref,
                                adapter_name=self.name,
                                priority=self.priority,
                                confidence=0.80,
                                metadata={
                                    "framework": "claude_agent_sdk",
                                    "tool_source": "allowed_tools",
                                },
                                file_path=file_path,
                                line=inst.line,
                                snippet=f"allowed_tools=[..., {tool_ref!r}, ...]",
                                evidence_kind="ast_instantiation",
                            )
                        )

            # MCP_SERVER node when mcp_servers is configured.
            # The AST parser drops complex nested dicts entirely; fall back to
            # a regex scan of raw content so configured mcp_servers are still detected.
            _mcp_detected = (
                mcp_servers_raw is not None and mcp_servers_raw not in ([], {}, "")
            ) or bool(_MCP_SERVERS_RE.search(content))
            if _mcp_detected:
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MCP_SERVER,
                        canonical_name=canonicalize_text("claude_agent_sdk:mcp_servers"),
                        display_name="MCP Servers",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "framework": "claude_agent_sdk",
                            "mcp_servers_configured": True,
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet="mcp_servers={...}",
                        evidence_kind="ast_instantiation",
                    )
                )

        # Pass 2 — ClaudeSDKClient: AGENT node linked to options.
        # The AST parser only captures simple assignments (x = ClassName(...)).
        # `async with ClaudeSDKClient(...) as agent:` is not captured — use a
        # regex fallback to ensure the AGENT node is always emitted when this
        # class appears in the source.
        _sdk_client_insts = [i for i in parse_result.instantiations if i.class_name == "ClaudeSDKClient"]
        _sdk_client_regex_hit = bool(_CLAUDE_SDK_CLIENT_RE.search(content))

        if _sdk_client_insts:
            _sdk_client_sources: list[tuple[str, dict[str, Any], int]] = [
                (str((inst.args or {}).get("options", "")), inst.args or {}, inst.line)
                for inst in _sdk_client_insts
            ]
        elif _sdk_client_regex_hit:
            # Fallback: synthesise a single entry using first available options_var
            _first_opts_ref = next(iter(options_vars), "")
            _sdk_client_sources = [(f"${_first_opts_ref}" if _first_opts_ref else "", {}, 0)]
        else:
            _sdk_client_sources = []

        for options_ref_raw, _raw_args, _line in _sdk_client_sources:
            opts: dict[str, Any] = {}
            if options_ref_raw.startswith("$"):
                opts = options_vars.get(options_ref_raw[1:], {})

            agent_name = _clean(f"claude_agent_{_line}" if _line else "claude_agent")
            agent_canon = canonicalize_text(f"claude_agent_sdk:{agent_name}")

            rels: list[RelationshipHint] = []
            model_name = opts.get("model", "")
            system_prompt = opts.get("system_prompt", "")
            allowed_tools = opts.get("allowed_tools", [])
            permission_mode = opts.get("permission_mode", "")

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

            if len(system_prompt) >= 40:
                prompt_display = (
                    f"{options_ref_raw[1:]} System Prompt" if options_ref_raw.startswith("$") else
                    "Claude Agent System Prompt"
                )
                prompt_canon = canonicalize_text(prompt_display.lower())
                rels.append(
                    RelationshipHint(
                        source_canonical=agent_canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=prompt_canon,
                        target_type=ComponentType.PROMPT,
                        relationship_type="USES",
                    )
                )

            if isinstance(allowed_tools, list):
                for tool_ref in allowed_tools:
                    if isinstance(tool_ref, str) and not tool_ref.startswith("$"):
                        tool_canon = canonicalize_text(f"claude_agent_sdk:tool:{tool_ref}")
                        rels.append(
                            RelationshipHint(
                                source_canonical=agent_canon,
                                source_type=ComponentType.AGENT,
                                target_canonical=tool_canon,
                                target_type=ComponentType.TOOL,
                                relationship_type="CALLS",
                            )
                        )

            # hooks=/can_use_tool= → GUARDRAIL nodes with an explicit PROTECTS
            # hint. agent_canon is only reliably known here (ClaudeSDKClient
            # names are line-number-based, not reconstructable from outside
            # this loop) — mirrors openai_agents.py's explicit input_guardrails
            # wiring.
            if opts.get("has_hooks"):
                hooks_canon = canonicalize_text(f"claude_agent_sdk:guardrail:hooks:{_line}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.GUARDRAIL,
                        canonical_name=hooks_canon,
                        display_name="Claude Agent SDK Hooks",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "framework": "claude_agent_sdk",
                            "guardrail_type": "hooks",
                            "detection_kind": "framework_native",
                        },
                        file_path=file_path,
                        line=_line,
                        snippet="ClaudeAgentOptions(hooks={...})",
                        evidence_kind="regex",
                    )
                )
                rels.append(
                    RelationshipHint(
                        source_canonical=hooks_canon,
                        source_type=ComponentType.GUARDRAIL,
                        target_canonical=agent_canon,
                        target_type=ComponentType.AGENT,
                        relationship_type="PROTECTS",
                    )
                )
            if opts.get("has_can_use_tool"):
                cut_canon = canonicalize_text(f"claude_agent_sdk:guardrail:can_use_tool:{_line}")
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.GUARDRAIL,
                        canonical_name=cut_canon,
                        display_name="Claude Agent SDK canUseTool",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.80,
                        metadata={
                            "framework": "claude_agent_sdk",
                            "guardrail_type": "can_use_tool",
                            "detection_kind": "framework_native",
                        },
                        file_path=file_path,
                        line=_line,
                        snippet="ClaudeAgentOptions(can_use_tool=...)",
                        evidence_kind="regex",
                    )
                )
                rels.append(
                    RelationshipHint(
                        source_canonical=cut_canon,
                        source_type=ComponentType.GUARDRAIL,
                        target_canonical=agent_canon,
                        target_type=ComponentType.AGENT,
                        relationship_type="PROTECTS",
                    )
                )

            meta: dict[str, Any] = {
                "framework": "claude_agent_sdk",
                "agent_class": "ClaudeSDKClient",
            }
            if model_name:
                meta["model"] = model_name
            if permission_mode:
                meta["permission_mode"] = permission_mode

            evidence = "ast_instantiation" if _sdk_client_insts else "regex"
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=agent_canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.92 if _sdk_client_insts else 0.80,
                    metadata=meta,
                    file_path=file_path,
                    line=_line,
                    snippet=f"ClaudeSDKClient(options={options_ref_raw or '...'})",
                    evidence_kind=evidence,
                    relationships=rels,
                )
            )

        # Pass 3 — standalone query() calls (one-shot agent pattern, no method receiver)
        for call in parse_result.function_calls:
            if call.function_name != "query":
                continue
            # Skip method calls like agent.query(...)
            if getattr(call, "receiver", None) is not None:
                continue
            args = call.args or {}

            # Resolve options reference if present
            opts = {}
            options_ref = str(args.get("options", ""))
            if options_ref.startswith("$"):
                opts = options_vars.get(options_ref[1:], {})

            agent_name = _clean(call.assigned_to or f"query_agent_{call.line}")
            agent_canon = canonicalize_text(f"claude_agent_sdk:{agent_name}")

            rels = []
            model_name = opts.get("model", "")
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
                    component_type=ComponentType.AGENT,
                    canonical_name=agent_canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.80,
                    metadata={
                        "framework": "claude_agent_sdk",
                        "agent_class": "query",
                        "is_oneshot": True,
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet="query(prompt=...)",
                    evidence_kind="ast_call",
                    relationships=rels,
                )
            )

        # Pass 4 — AgentDefinition: programmatic subagent declarations
        for inst in parse_result.instantiations:
            if inst.class_name != "AgentDefinition":
                continue
            args = inst.args or {}
            subagent_name = _clean(
                args.get("name")
                or (inst.positional_args[0] if inst.positional_args else None)
                or inst.assigned_to
                or f"subagent_{inst.line}"
            )
            subagent_canon = canonicalize_text(f"claude_agent_sdk:subagent:{subagent_name}")
            subagent_tools = args.get("tools") or []
            description = _clean(args.get("description", ""))

            rels = []
            if isinstance(subagent_tools, list):
                for tool_ref in subagent_tools:
                    if isinstance(tool_ref, str) and not tool_ref.startswith("$"):
                        tool_canon = canonicalize_text(f"claude_agent_sdk:tool:{tool_ref}")
                        rels.append(
                            RelationshipHint(
                                source_canonical=subagent_canon,
                                source_type=ComponentType.AGENT,
                                target_canonical=tool_canon,
                                target_type=ComponentType.TOOL,
                                relationship_type="CALLS",
                            )
                        )

            meta = {
                "framework": "claude_agent_sdk",
                "agent_class": "AgentDefinition",
                "is_subagent": True,
            }
            if description:
                meta["description"] = description

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=subagent_canon,
                    display_name=subagent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata=meta,
                    file_path=file_path,
                    line=inst.line,
                    snippet=f"AgentDefinition(name={subagent_name!r})",
                    evidence_kind="ast_instantiation",
                    relationships=rels,
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
