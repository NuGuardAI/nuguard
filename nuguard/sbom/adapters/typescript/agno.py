"""Agno Framework TypeScript/JavaScript Adapter for Xelo SBOM.

Parsing is performed by ``xelo.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).

Agno is primarily a Python framework, but TypeScript/JavaScript projects
connect to Agno agent servers via the ``@ag-ui/agno`` npm package, which
implements the AG-UI protocol for HTTP-based streaming communication.

Supports:
- ``new AgnoAgent({ url, headers })`` → FRAMEWORK + AGENT detection
- ``agent.runAgent(...)`` → confirms agent usage
- Multi-agent: ``AgnoMultiAgent`` / ``AgnoRouter`` classes

Note: Since Agno itself runs as a Python server, the TS adapter detects
the client-side consumer, not the server-side agent definition.
"""

from __future__ import annotations

from typing import Any

from xelo.adapters.base import ComponentDetection
from xelo.adapters.typescript._ts_regex import TSFrameworkAdapter
from xelo.core.ts_parser import TSParseResult, parse_typescript
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType


_AGNO_TS_PACKAGES = [
    "@ag-ui/agno",
    # CopilotKit integration that wraps Agno agents
    "@copilotkit/agno",
]

# Class names that represent Agno agent consumers
_AGENT_CLASSES = {
    "AgnoAgent",
    "AgnoMultiAgent",
    "AgnoRouter",
    "AgnoCopilotKitAgent",
}

# Method calls that confirm an agent invocation / interaction
_RUN_METHODS = {"runAgent", "run", "invoke", "stream"}


class AgnoTSAdapter(TSFrameworkAdapter):
    """Detect Agno client-side usage in TypeScript/JavaScript files."""

    name = "agno_ts"
    priority = 25
    handles_imports = _AGNO_TS_PACKAGES

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

        for inst in result.instantiations:
            cls = inst.class_name
            if cls not in _AGENT_CLASSES:
                continue

            # Extract agent name: prefer `name` kwarg, fall back to variable name
            agent_name = (
                self._resolve(inst, "name", "agentId")
                or self._assignment_name(source, inst.line_start)
                or f"agno_agent_{inst.line_start}"
            )

            # Extract the server endpoint URL for metadata
            server_url = self._resolve(inst, "url", "endpoint", "serverUrl") or ""

            agent_canon = canonicalize_text(agent_name.lower())
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=agent_canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "framework": "agno",
                        "agent_class": cls,
                        "server_url": server_url or None,
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=inst.line_start,
                    snippet=inst.source_snippet or f"new {cls}({{ url: {server_url!r} }})",
                    evidence_kind="ast_instantiation",
                )
            )

        return detected
