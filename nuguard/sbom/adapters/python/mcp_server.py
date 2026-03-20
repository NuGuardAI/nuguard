"""MCP (Model Context Protocol) server adapter for Xelo SBOM.

Detects usage of the ``mcp`` / ``fastmcp`` Python SDK:
- ``FastMCP("server-name", ...)`` instantiation → FRAMEWORK node
  - ``auth=`` kwarg or known auth-provider instantiation → AUTH node
  - ``host=`` / ``port=`` kwargs → API_ENDPOINT node
- ``@server.tool()`` / ``@mcp.tool()`` decorated function definitions → TOOL nodes
  (tool name = decorated function name)
- Bare ``@tool`` decorator or ``mcp.add_tool(fn)`` calls → TOOL fallback
- ``mcp.run(transport="sse"|"streamable-http", host=..., port=...)`` → API_ENDPOINT node

Relationship edges emitted:
  FRAMEWORK -[CALLS]-> TOOL
  FRAMEWORK -[USES]-> AUTH
  FRAMEWORK -[USES]-> API_ENDPOINT
  AUTH       -[PROTECTS]-> API_ENDPOINT
"""

from __future__ import annotations

from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

# FastMCP package entrypoints
_MCP_SERVER_CLASSES = {"FastMCP", "Server", "MCPServer"}
# Method name used as decorator on tool functions
_TOOL_METHOD = "tool"

# Known MCP / FastMCP auth-provider class names
_AUTH_PROVIDER_CLASSES = {
    "BearerAuthProvider",
    "OAuthProvider",
    "ClientCredentialsProvider",
    "OAuth2Bearer",
    "APIKeyAuth",
    "TokenAuth",
    "JWTAuth",
    "OAuth2AuthorizationCodeProvider",
    "OAuth2ClientCredentialsProvider",
}

# HTTP transports that expose a real API endpoint
_HTTP_TRANSPORTS = {"sse", "streamable-http", "http"}


class MCPServerAdapter(FrameworkAdapter):
    """Adapter for MCP server projects (model-context-protocol / fastmcp)."""

    name = "mcp_server"
    priority = 30
    handles_imports = [
        "mcp",
        "mcp.server",
        "mcp.server.fastmcp",
        "mcp.server.stdio",
        "mcp.types",
        "fastmcp",
    ]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        from xelo.adapters.base import RelationshipHint

        # Track variable names bound to FastMCP / Server instances
        # e.g. ``mcp = FastMCP("excel-mcp")`` → mcp_vars = {"mcp"}
        mcp_vars: set[str] = set()
        server_name: str | None = None
        server_host: str | None = None
        server_port: str | None = None
        server_auth_kwarg: str | None = None  # raw value of auth= kwarg

        for inst in parse_result.instantiations:
            if inst.class_name in _MCP_SERVER_CLASSES:
                if inst.assigned_to:
                    mcp_vars.add(inst.assigned_to)
                # First positional or 'name' kwarg is the server display name
                raw_name = inst.args.get("name") or (
                    inst.positional_args[0] if inst.positional_args else None
                )
                if raw_name and not server_name:
                    server_name = _clean(raw_name)
                # Optional host / port configured on the constructor
                if not server_host:
                    server_host = _clean(inst.args.get("host", ""))
                if not server_port:
                    server_port = _clean(inst.args.get("port", ""))
                # Optional auth= kwarg
                if not server_auth_kwarg:
                    server_auth_kwarg = _clean(inst.args.get("auth", ""))

        # Canonical names shared across the detection lists
        fw_canonical = f"framework:{self.name}"

        # ------------------------------------------------------------------
        # AUTH detection
        # ------------------------------------------------------------------
        auth_detections: list[ComponentDetection] = []

        # 1. Instantiations of known auth-provider classes
        for inst in parse_result.instantiations:
            if inst.class_name not in _AUTH_PROVIDER_CLASSES:
                continue
            auth_type = _auth_kind(inst.class_name)
            canon = canonicalize_text(f"mcp:auth:{inst.class_name.lower()}")
            auth_detections.append(
                ComponentDetection(
                    component_type=ComponentType.AUTH,
                    canonical_name=canon,
                    display_name=inst.class_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": "mcp-server",
                        "auth_type": auth_type,
                        "auth_class": inst.class_name,
                    },
                    file_path=file_path,
                    line=inst.line,
                    snippet=f"{inst.class_name}(...)",
                    evidence_kind="ast_instantiation",
                )
            )

        # 2. auth= kwarg on FastMCP() when value is a non-empty identifier
        if server_auth_kwarg and not auth_detections:
            auth_type = _auth_kind(server_auth_kwarg)
            canon = canonicalize_text(f"mcp:auth:{server_auth_kwarg.lower()}")
            auth_detections.append(
                ComponentDetection(
                    component_type=ComponentType.AUTH,
                    canonical_name=canon,
                    display_name=server_auth_kwarg,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "mcp-server",
                        "auth_type": auth_type,
                        "auth_source": "fastmcp_auth_kwarg",
                    },
                    file_path=file_path,
                    line=0,
                    snippet=f"FastMCP(..., auth={server_auth_kwarg})",
                    evidence_kind="ast_instantiation",
                )
            )

        # ------------------------------------------------------------------
        # API_ENDPOINT detection
        # ------------------------------------------------------------------
        endpoint_detections: list[ComponentDetection] = []

        # Helper: emit one endpoint detection
        def _add_endpoint(host: str, port: str, transport: str, line: int, snippet: str) -> None:
            host = host or "0.0.0.0"
            port = port or ("8000" if transport == "streamable-http" else "8080")
            display = f"{host}:{port} ({transport})"
            canon = canonicalize_text(f"mcp:api_endpoint:{host}:{port}")
            endpoint_detections.append(
                ComponentDetection(
                    component_type=ComponentType.API_ENDPOINT,
                    canonical_name=canon,
                    display_name=display,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "framework": "mcp-server",
                        "transport": transport,
                        "host": host,
                        "port": port,
                        "server_name": server_name or "unknown",
                    },
                    file_path=file_path,
                    line=line,
                    snippet=snippet,
                    evidence_kind="ast_call",
                )
            )

        # Scan .run() / .serve() calls on mcp_vars for HTTP transports
        _seen_endpoints: set[str] = set()
        for call in parse_result.function_calls:
            if call.function_name not in {"run", "serve", "run_http"}:
                continue
            if mcp_vars and call.receiver not in mcp_vars:
                continue
            transport = _clean((call.args or {}).get("transport", ""))
            if transport not in _HTTP_TRANSPORTS:
                continue
            host = _clean((call.args or {}).get("host", server_host or ""))
            port = _clean((call.args or {}).get("port", server_port or ""))
            key = f"{host}:{port}:{transport}"
            if key not in _seen_endpoints:
                _seen_endpoints.add(key)
                _add_endpoint(
                    host,
                    port,
                    transport,
                    call.line,
                    f"{call.receiver or 'mcp'}.{call.function_name}("
                    f"transport={transport!r}, host={host!r}, port={port!r})",
                )

        # If host+port were set on the FastMCP constructor but no run() call found
        if not endpoint_detections and (server_host or server_port):
            _add_endpoint(
                server_host or "",
                server_port or "",
                "http",
                0,
                f"FastMCP(..., host={server_host!r}, port={server_port!r})",
            )

        # ------------------------------------------------------------------
        # TOOL detection
        # ------------------------------------------------------------------
        tool_detections: list[ComponentDetection] = []

        # Scan function_calls for @<var>.tool() decorators
        # These appear as ParsedCall(function_name="tool", receiver=<var>, assigned_to=<fn_name>)
        for call in parse_result.function_calls:
            if call.function_name != _TOOL_METHOD:
                continue
            # Must be a decorator (has assigned_to = the decorated function)
            if call.assigned_to is None:
                continue
            # If we know the MCP variable names, filter to those;
            # if none known yet (e.g. FastMCP constructed elsewhere), accept any .tool() receiver
            if mcp_vars and call.receiver not in mcp_vars:
                continue

            # Tool name: explicit name kwarg > decorated function name
            tool_name = _clean(call.args.get("name")) or call.assigned_to or f"tool_{call.line}"
            canon = canonicalize_text(f"mcp:tool:{tool_name}")

            tool_detections.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canon,
                    display_name=tool_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.92,
                    metadata={
                        "framework": "mcp-server",
                        "server_name": server_name or "unknown",
                        "decorator": f"@{call.receiver or 'server'}.tool()",
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"@{call.receiver or 'server'}.tool()\ndef {tool_name}(...)",
                    evidence_kind="ast_decorator",
                )
            )

        # ------------------------------------------------------------------
        # FRAMEWORK node — built last so it can reference all other canonicals
        # ------------------------------------------------------------------
        fw_display = server_name if server_name else f"framework:{self.name}"
        fw_relationships: list[RelationshipHint] = []

        for tool in tool_detections:
            fw_relationships.append(
                RelationshipHint(
                    source_canonical=fw_canonical,
                    source_type=ComponentType.FRAMEWORK,
                    target_canonical=tool.canonical_name,
                    target_type=ComponentType.TOOL,
                    relationship_type="CALLS",
                )
            )
        for auth in auth_detections:
            fw_relationships.append(
                RelationshipHint(
                    source_canonical=fw_canonical,
                    source_type=ComponentType.FRAMEWORK,
                    target_canonical=auth.canonical_name,
                    target_type=ComponentType.AUTH,
                    relationship_type="USES",
                )
            )
        for ep in endpoint_detections:
            fw_relationships.append(
                RelationshipHint(
                    source_canonical=fw_canonical,
                    source_type=ComponentType.FRAMEWORK,
                    target_canonical=ep.canonical_name,
                    target_type=ComponentType.API_ENDPOINT,
                    relationship_type="USES",
                )
            )

        # AUTH → API_ENDPOINT (PROTECTS)
        for auth in auth_detections:
            for ep in endpoint_detections:
                auth.relationships.append(
                    RelationshipHint(
                        source_canonical=auth.canonical_name,
                        source_type=ComponentType.AUTH,
                        target_canonical=ep.canonical_name,
                        target_type=ComponentType.API_ENDPOINT,
                        relationship_type="PROTECTS",
                    )
                )

        fw_node = ComponentDetection(
            component_type=ComponentType.FRAMEWORK,
            canonical_name=fw_canonical,
            display_name=fw_display,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.95,
            metadata={
                "framework": self.name,
                "server_name": server_name or "unknown",
            },
            file_path=file_path,
            line=0,
            snippet=f"import {self.name}",
            evidence_kind="ast_import",
            relationships=fw_relationships,
        )

        return [fw_node, *tool_detections, *auth_detections, *endpoint_detections]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip("'\"` ")
    if s.startswith("$") or s in {"<complex>", "<lambda>", "<dict>", "<list>"}:
        return ""
    return s


def _auth_kind(name: str) -> str:
    """Map a class / keyword name to a normalised auth-type label."""
    n = name.lower()
    if "oauth" in n:
        return "oauth2"
    if "bearer" in n:
        return "bearer"
    if "apikey" in n or "api_key" in n:
        return "api_key"
    if "jwt" in n:
        return "jwt"
    if "token" in n:
        return "token"
    return "unknown"
