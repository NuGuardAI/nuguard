"""AWS Bedrock Agents TypeScript Adapter for Xelo SBOM.

Parsing is performed by ``xelo.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).

Extracts:
- BedrockAgentRuntimeClient → runtime presence
- InvokeAgentCommand → Agent nodes
- InvokeInlineAgentCommand → Inline agents with model/instructions/tools
- RetrieveCommand / RetrieveAndGenerateCommand → Knowledge base (Datastore) nodes
"""

from __future__ import annotations

import re
from typing import Any

from xelo.adapters.base import ComponentDetection, RelationshipHint
from xelo.adapters.typescript._ts_regex import TSFrameworkAdapter
from xelo.core.ts_parser import TSParseResult, parse_typescript
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType


_BEDROCK_PACKAGES = [
    "@aws-sdk/client-bedrock-agent-runtime",
    "@aws-sdk/client-bedrock-agent",
    "@aws-sdk/client-bedrock-runtime",
    "@aws-sdk/client-bedrock",
]

_AGENT_COMMAND_CLASSES = {"InvokeAgentCommand", "InvokeInlineAgentCommand"}
_KB_COMMAND_CLASSES = {"RetrieveCommand", "RetrieveAndGenerateCommand"}

_FM_PATTERNS: dict[str, dict[str, str]] = {
    "anthropic.claude": {"provider": "anthropic", "family": "claude"},
    "amazon.titan": {"provider": "amazon", "family": "titan"},
    "meta.llama": {"provider": "meta", "family": "llama"},
    "mistral.": {"provider": "mistral", "family": "mistral"},
    "cohere.": {"provider": "cohere", "family": "cohere"},
    "ai21.": {"provider": "ai21", "family": "jurassic"},
}


def _model_info(model_id: str) -> dict[str, str]:
    for pattern, info in _FM_PATTERNS.items():
        if pattern in model_id.lower():
            return info
    return {"provider": "bedrock", "family": "unknown"}


class BedrockAgentsTSAdapter(TSFrameworkAdapter):
    """Detect AWS Bedrock Agents SDK usage in TypeScript/JavaScript files."""

    name = "bedrock_agents_ts"
    priority = 28
    handles_imports = _BEDROCK_PACKAGES

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

        detected: list[ComponentDetection] = [self._fw_node(file_path)]

        for inst in result.instantiations:
            cls = inst.class_name
            # resolved_arguments expands any variable references (e.g. const AGENT_ID = "...")
            args = inst.resolved_arguments or inst.arguments

            if cls == "InvokeAgentCommand":
                detected.extend(self._invoke_agent(file_path, inst.line_start, args))
            elif cls == "InvokeInlineAgentCommand":
                detected.extend(self._inline_agent(file_path, inst.line_start, args, content))
            elif cls in _KB_COMMAND_CLASSES:
                detected.extend(self._kb_command(file_path, inst.line_start, cls, args))

        return detected

    # ------------------------------------------------------------------

    def _invoke_agent(
        self, file_path: str, line: int, args: dict[str, Any]
    ) -> list[ComponentDetection]:
        agent_id = self._clean(args.get("agentId", ""))
        agent_alias = self._clean(args.get("agentAliasId", ""))
        agent_name = agent_id or f"bedrock_agent_{line}"
        agent_canon = canonicalize_text(agent_name.lower())
        out: list[ComponentDetection] = []
        rels: list[RelationshipHint] = []

        input_text = self._clean(args.get("inputText", ""))
        if len(input_text) > 5:
            prompt_canon = canonicalize_text(f"{agent_name} input")
            rels.append(
                RelationshipHint(
                    source_canonical=agent_canon,
                    source_type=ComponentType.AGENT,
                    target_canonical=prompt_canon,
                    target_type=ComponentType.PROMPT,
                    relationship_type="USES",
                )
            )
            out.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=prompt_canon,
                    display_name=f"{agent_name} Input",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "aws-bedrock",
                        "prompt_type": "agent_input",
                        "role": "user",
                        "content": input_text,
                        "char_count": len(input_text),
                        "is_template": bool(self._template_vars(input_text)),
                        "template_variables": self._template_vars(input_text),
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=line,
                    snippet=input_text[:80],
                    evidence_kind="ast_instantiation",
                )
            )

        out.append(
            ComponentDetection(
                component_type=ComponentType.AGENT,
                canonical_name=agent_canon,
                display_name=agent_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.90,
                metadata={
                    "agent_id": agent_id,
                    "agent_alias_id": agent_alias,
                    "framework": "aws-bedrock",
                    "command": "InvokeAgentCommand",
                    "language": "typescript",
                },
                file_path=file_path,
                line=line,
                snippet=f"new InvokeAgentCommand({{agentId: {agent_id!r}}})",
                evidence_kind="ast_instantiation",
                relationships=rels,
            )
        )
        return out

    def _inline_agent(
        self, file_path: str, line: int, args: dict[str, Any], source: str
    ) -> list[ComponentDetection]:
        agent_name = f"inline_agent_{line}"
        agent_canon = canonicalize_text(agent_name)
        out: list[ComponentDetection] = []
        rels: list[RelationshipHint] = []

        fm = self._clean(args.get("foundationModel", ""))
        if fm:
            info = _model_info(fm)
            model_canon = canonicalize_text(fm.lower())
            rels.append(
                RelationshipHint(
                    source_canonical=agent_canon,
                    source_type=ComponentType.AGENT,
                    target_canonical=model_canon,
                    target_type=ComponentType.MODEL,
                    relationship_type="USES",
                )
            )
            out.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=model_canon,
                    display_name=fm,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": "aws-bedrock",
                        "model_id": fm,
                        "provider": info.get("provider", "aws"),
                        "family": info.get("family"),
                        "source": "InvokeInlineAgentCommand",
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=line,
                    snippet=f"foundationModel={fm!r}",
                    evidence_kind="ast_instantiation",
                )
            )

        instruction = self._clean(args.get("instruction", ""))
        instr_tvars = self._template_vars(instruction) if instruction else []
        if len(instruction) > 5:
            prompt_canon = canonicalize_text(f"{agent_name} instructions")
            rels.append(
                RelationshipHint(
                    source_canonical=agent_canon,
                    source_type=ComponentType.AGENT,
                    target_canonical=prompt_canon,
                    target_type=ComponentType.PROMPT,
                    relationship_type="USES",
                )
            )
            out.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=prompt_canon,
                    display_name=f"{agent_name} Instructions",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.92,
                    metadata={
                        "framework": "aws-bedrock",
                        "prompt_type": "instruction",
                        "role": "system",
                        "content": instruction,
                        "char_count": len(instruction),
                        "is_template": bool(instr_tvars),
                        "template_variables": instr_tvars,
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=line,
                    snippet=instruction[:80] + ("..." if len(instruction) > 80 else ""),
                    evidence_kind="ast_instantiation",
                )
            )

        out.append(
            ComponentDetection(
                component_type=ComponentType.AGENT,
                canonical_name=agent_canon,
                display_name=agent_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.90,
                metadata={
                    "framework": "aws-bedrock",
                    "command": "InvokeInlineAgentCommand",
                    "is_inline": True,
                    "has_instructions": len(instruction) > 5,
                    "language": "typescript",
                },
                file_path=file_path,
                line=line,
                snippet="new InvokeInlineAgentCommand({...})",
                evidence_kind="ast_instantiation",
                relationships=rels,
            )
        )
        return out

    def _kb_command(
        self, file_path: str, line: int, command: str, args: dict[str, Any]
    ) -> list[ComponentDetection]:
        kb_id = self._clean(args.get("knowledgeBaseId", ""))
        kb_name = kb_id or f"knowledge_base_{line}"
        out: list[ComponentDetection] = []

        out.append(
            ComponentDetection(
                component_type=ComponentType.DATASTORE,
                canonical_name=canonicalize_text(kb_name.lower()),
                display_name=kb_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.90,
                metadata={
                    "datastore_type": "knowledge_base",
                    "knowledge_base_id": kb_id,
                    "command": command,
                    "framework": "aws-bedrock",
                    "language": "typescript",
                },
                file_path=file_path,
                line=line,
                snippet=f"new {command}({{knowledgeBaseId: {kb_id!r}}})",
                evidence_kind="ast_instantiation",
            )
        )

        if command == "RetrieveAndGenerateCommand":
            m = re.search(r"""modelArn\s*:\s*['"]([^'"]+)['"]""", str(args))
            if m:
                model_arn = m.group(1)
                info = _model_info(model_arn)
                out.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=canonicalize_text(model_arn.lower()),
                        display_name=model_arn,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "model_arn": model_arn,
                            "provider": info.get("provider", "aws"),
                            "source": "RetrieveAndGenerateCommand",
                            "language": "typescript",
                        },
                        file_path=file_path,
                        line=line,
                        snippet=f"modelArn={model_arn!r}",
                        evidence_kind="ast_instantiation",
                    )
                )

        return out


BEDROCK_TS_PACKAGES = _BEDROCK_PACKAGES
BEDROCK_AGENT_COMMANDS = list(_AGENT_COMMAND_CLASSES)
BEDROCK_KB_COMMANDS = list(_KB_COMMAND_CLASSES)
