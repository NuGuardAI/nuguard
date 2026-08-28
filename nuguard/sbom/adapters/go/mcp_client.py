"""``mark3labs/mcp-go`` client-side adapter (untrusted, user-configured MCP servers).

Complements ``mcp_server.py`` (apps *hosting* an MCP server) by detecting
apps that act as an MCP *client* — a distinct, higher-risk shape since the
server(s) connected to are untrusted/attacker-influenceable rather than a
fixed, developer-controlled list. Mirrors Python's ``mcp_client.py``: one
document-scoped MCP_SERVER node (all client call sites across the app merge
into it via a fixed canonical name, matching the Python adapter's
``mcp_client:servers`` constant so a mixed Python+Go app still gets a single
node) plus one TOOL node per client-construction call site, tagged
``trust_level="untrusted"`` and ``mcp_server_url=...`` — the exact
``NodeMetadata`` fields ``nuguard/redteam/scenarios/generator.py``'s
``_mcp_toxic_flow_scenarios``/``_mcp_attack_scenarios`` filter on, so this
wires straight into MCP-toxic-flow scenario generation with no redteam-side
changes.

Constructor set verified against
``pkg.go.dev/github.com/mark3labs/mcp-go/client``. ``NewInProcessClient*``
is deliberately excluded — it wraps a local, developer-controlled
``*server.MCPServer`` value, not a user-configured/untrusted endpoint, so it
doesn't fit this adapter's threat model.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._go_base import GoFrameworkAdapter

_MODULE = "github.com/mark3labs/mcp-go/client"

# function_name -> whether its first positional arg is a resolvable base URL
_CLIENT_CONSTRUCTORS: dict[str, bool] = {
    "NewClient": False,
    "NewStdioMCPClient": False,
    "NewStdioMCPClientWithOptions": False,
    "NewSSEMCPClient": True,
    "NewStreamableHttpClient": True,
    "NewOAuthSSEClient": True,
    "NewOAuthStreamableHttpClient": True,
}

_MCP_SERVER_CANON = canonicalize_text("mcp_client:servers")


class MCPGoClientAdapter(GoFrameworkAdapter):
    """Detect ``mcp-go`` client construction (connections to untrusted MCP servers)."""

    name = "mcp_go_client"
    priority = 55
    handles_imports = [_MODULE]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = (
            parse_result
            if isinstance(parse_result, GoParseResult)
            else parse_go(content, file_path)
        )
        matched_import = self._matching_import(result)
        if matched_import is None:
            return []

        detections: list[ComponentDetection] = [
            ComponentDetection(
                component_type=ComponentType.MCP_SERVER,
                canonical_name=_MCP_SERVER_CANON,
                display_name="User-Configured MCP Servers",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.75,
                metadata={
                    "framework": "mcp_go_client",
                    "trust_boundary": "user-configured/untrusted",
                    "language": "golang",
                },
                file_path=file_path,
                line=matched_import.line,
                snippet=f'import "{matched_import.path}"',
                evidence_kind="ast_import",
            )
        ]

        for call in result.function_calls:
            if call.receiver != "client" or call.function_name not in _CLIENT_CONSTRUCTORS:
                continue

            url_resolvable = _CLIENT_CONSTRUCTORS[call.function_name]
            url = self._resolve(call, 0) if url_resolvable else ""

            tool_canon = canonicalize_text(f"mcp_go_client:{file_path}:{call.line}")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=tool_canon,
                    display_name=f"MCP Client ({call.function_name})",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.8,
                    metadata={
                        "framework": "mcp_go_client",
                        "trust_level": "untrusted",
                        "mcp_server_url": url or "dynamic",
                        "language": "golang",
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=call.source_snippet or f"client.{call.function_name}(...)",
                    evidence_kind="ast_call",
                    relationships=[
                        RelationshipHint(
                            source_canonical=tool_canon,
                            source_type=ComponentType.TOOL,
                            target_canonical=_MCP_SERVER_CANON,
                            target_type=ComponentType.MCP_SERVER,
                            relationship_type="CALLS",
                        )
                    ],
                )
            )

        return detections


__all__ = ["MCPGoClientAdapter"]
