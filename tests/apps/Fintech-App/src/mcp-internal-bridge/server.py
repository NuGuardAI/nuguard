"""FinTech GOAT — MCP Internal Bridge — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_internal_bridge")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-internal-bridge", instructions="Pinnacle Bank internal bridge tool API.")

_INTERNAL_SERVICES: dict[str, int] = {
    "mcp-admin": 8080,
    "mcp-accounts": 8080,
    "mcp-audit": 8080,
    "orchestrator": 8001,
    "redis": 6379,
    "mcp-fraud": 8080,
}


@mcp.tool()
async def call_internal_service(service_name: str, endpoint: str, method: str = "GET", body: dict = None) -> dict:
    # VULN-MCP-25: SSRF/lateral movement — service_name used directly in URL with no allowlist enforcement
    port = _INTERNAL_SERVICES.get(service_name, 8080)
    url = f"http://{service_name}:{port}{endpoint}"
    logger.warning("Internal service call: method=%s url=%s", method, url)
    try:
        response = requests.request(method, url, json=body, timeout=10)
        try:
            resp_body = response.json()
        except Exception:
            resp_body = response.text[:500]
        return {
            "service": service_name,
            "endpoint": endpoint,
            "method": method,
            "status_code": response.status_code,
            "response": resp_body,
        }
    except requests.exceptions.RequestException as exc:
        return {"service": service_name, "endpoint": endpoint, "error": str(exc)}


@mcp.tool()
async def get_service_health(service_url: str) -> dict:
    # VULN-MCP-26: SSRF — makes GET request to any URL supplied by the caller without validation
    logger.warning("Health check SSRF attempt: url=%s", service_url)
    try:
        response = requests.get(service_url, timeout=10)
        return {
            "url": service_url,
            "status_code": response.status_code,
            "response_preview": response.text[:200],
        }
    except requests.exceptions.RequestException as exc:
        return {"url": service_url, "error": str(exc)}


@mcp.tool()
async def invoke_admin_api(action: str, params: dict = None) -> dict:
    # VULN: directly calls mcp-admin without any authentication header
    url = "http://mcp-admin:8080/tools/call"
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": action, "arguments": params or {}},
    }
    logger.warning("Direct admin API invocation: action=%s", action)
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except requests.exceptions.RequestException as exc:
        return {"error": str(exc), "action": action}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "internal-bridge"})


@mcp.custom_route("/tools/call", methods=["POST"])
async def tools_call_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
    call_id = body.get("id", 1)
    params = body.get("params", {})
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})
    try:
        if tool_name == "call_internal_service":
            result = await call_internal_service(**arguments)
        elif tool_name == "get_service_health":
            result = await get_service_health(**arguments)
        elif tool_name == "invoke_admin_api":
            result = await invoke_admin_api(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
