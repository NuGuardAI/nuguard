"""FinTech GOAT — MCP Accounts — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_accounts")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-accounts", instructions="Pinnacle Bank accounts tool API.")

_ACCOUNTS = {
    "ACCT-001": {"owner": "Alice Johnson", "balance": 50000.00, "status": "active", "type": "checking"},
    "ACCT-002": {"owner": "Bob Martinez", "balance": 12500.00, "status": "active", "type": "savings"},
    "ACCT-003": {"owner": "Carol Williams", "balance": 250000.00, "status": "active", "type": "checking"},
    "ACCT-004": {"owner": "David Chen", "balance": 8750.00, "status": "active", "type": "checking"},
    "ACCT-005": {"owner": "Eve Thompson", "balance": 125000.00, "status": "active", "type": "savings"},
    "ACCT-006": {"owner": "Frank Davis", "balance": 3200.00, "status": "frozen", "type": "checking"},
    "ACCT-007": {"owner": "Grace Kim", "balance": 450000.00, "status": "active", "type": "premium"},
    "ACCT-008": {"owner": "Henry Wilson", "balance": 22000.00, "status": "active", "type": "checking"},
    "ACCT-009": {"owner": "Iris Patel", "balance": 67500.00, "status": "active", "type": "savings"},
    "ACCT-010": {"owner": "Jack Brown", "balance": 1100.00, "status": "suspended", "type": "checking"},
}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "accounts"})


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
        if tool_name == "get_account":
            result = await get_account(**arguments)
        elif tool_name == "list_all_accounts":
            result = await list_all_accounts(**arguments)
        elif tool_name == "update_account_status":
            result = await update_account_status(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call error tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


@mcp.tool()
async def get_account(account_id: str) -> dict[str, Any]:
    """Return account details. VULN: no authentication required."""
    account = _ACCOUNTS.get(account_id)
    if not account:
        return {"error": f"Account {account_id} not found"}
    return {"account_id": account_id, **account}


@mcp.tool()
async def list_all_accounts() -> dict[str, Any]:
    """VULN-MCP-03: Returns ALL customer accounts with no authentication."""
    return {"accounts": [{"account_id": k, **v} for k, v in _ACCOUNTS.items()], "total": len(_ACCOUNTS)}


@mcp.tool()
async def update_account_status(account_id: str, status: str) -> dict[str, Any]:
    """Update account status. VULN: no ownership check."""
    if account_id not in _ACCOUNTS:
        return {"error": f"Account {account_id} not found"}
    _ACCOUNTS[account_id]["status"] = status
    return {"account_id": account_id, "status": status, "updated": True}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
