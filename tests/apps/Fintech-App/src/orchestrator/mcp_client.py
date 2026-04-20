"""
HTTP client for calling the internal MCP Banking Server.
Wraps the FastMCP SSE/HTTP transport with a simple synchronous interface
compatible with LangChain 0.0.300 Tool callbacks.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger("orchestrator.mcp_client")


class MCPClient:
    """Thin HTTP client for the MCP Banking Server JSON-RPC endpoint."""

    def __init__(self) -> None:
        base_url = os.getenv("MCP_SERVER_URL", "http://mcp-banking-server:8080")
        self.tools_url = f"{base_url.rstrip('/')}/tools/call"
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def call_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        session_id: str = "unknown",
    ) -> str:
        """Call a named MCP tool and return the result as a string.

        Raises requests.RequestException on HTTP errors.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": tool_args,
            },
        }
        logger.info("MCPClient.call_tool: tool=%s session=%s args=%s", tool_name, session_id, tool_args)
        try:
            resp = self.session.post(self.tools_url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            # MCP JSON-RPC response: {"jsonrpc":"2.0","id":1,"result":{"content":[...]}}
            content = data.get("result", {}).get("content", [])
            if content:
                return content[0].get("text", str(data))
            return str(data.get("result", ""))
        except requests.RequestException as exc:
            logger.error("MCP call failed for tool=%s: %s", tool_name, exc)
            return f"Error calling {tool_name}: {exc}"
