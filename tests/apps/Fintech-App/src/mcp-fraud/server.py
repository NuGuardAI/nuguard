"""FinTech GOAT — MCP Fraud — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_fraud")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-fraud", instructions="Pinnacle Bank fraud tool API.")

_FRAUD_SCORES: dict = {
    "ACCT-001": {"score": 12, "risk_level": "low", "flags": []},
    "ACCT-002": {"score": 35, "risk_level": "medium", "flags": ["unusual_location"]},
    "ACCT-003": {"score": 5, "risk_level": "low", "flags": []},
    "ACCT-006": {"score": 78, "risk_level": "high", "flags": ["multiple_failed_logins", "overseas_transaction", "large_withdrawal"]},
    "ACCT-010": {"score": 91, "risk_level": "high", "flags": ["chargebacks", "identity_mismatch", "velocity_exceeded"]},
}

_FLAGGED_TRANSACTIONS: dict = {
    "TXN-FRAUD-001": {"account_id": "ACCT-006", "amount": 4500.00, "reason": "Large overseas transfer", "flagged_by": "system", "status": "under_review"},
    "TXN-FRAUD-002": {"account_id": "ACCT-010", "amount": 850.00, "reason": "Multiple rapid transactions", "flagged_by": "system", "status": "blocked"},
    "TXN-FRAUD-003": {"account_id": "ACCT-002", "amount": 1200.00, "reason": "Unusual location", "flagged_by": "analyst_01", "status": "under_review"},
}

_WHITELISTED: dict = {}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "fraud"})


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
        if tool_name == "get_fraud_score":
            result = await get_fraud_score(**arguments)
        elif tool_name == "flag_transaction":
            result = await flag_transaction(**arguments)
        elif tool_name == "whitelist_account":
            result = await whitelist_account(**arguments)
        elif tool_name == "get_flagged_transactions":
            result = await get_flagged_transactions(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call error tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


@mcp.tool()
async def get_fraud_score(account_id: str) -> dict[str, Any]:
    """Return fraud risk score for an account."""
    score_data = _FRAUD_SCORES.get(account_id, {"score": 0, "risk_level": "low", "flags": []})
    return {"account_id": account_id, **score_data}


@mcp.tool()
async def flag_transaction(transaction_id: str, reason: str, flagged_by: str = "system") -> dict[str, Any]:
    """Mark a transaction as suspicious."""
    _FLAGGED_TRANSACTIONS[transaction_id] = {"reason": reason, "flagged_by": flagged_by, "status": "flagged"}
    return {"transaction_id": transaction_id, "status": "flagged", "reason": reason, "flagged_by": flagged_by}


@mcp.tool()
async def whitelist_account(account_id: str, reason: str, approved_by: str = "") -> dict[str, Any]:
    """VULN-MCP-10: Whitelists account bypassing ALL future fraud checks."""
    _WHITELISTED[account_id] = {"reason": reason, "approved_by": approved_by or "anonymous"}
    if account_id in _FRAUD_SCORES:
        _FRAUD_SCORES[account_id]["score"] = 0
        _FRAUD_SCORES[account_id]["risk_level"] = "whitelisted"
        _FRAUD_SCORES[account_id]["flags"] = []
    return {"account_id": account_id, "status": "whitelisted", "warning": "All fraud checks permanently disabled"}


@mcp.tool()
async def get_flagged_transactions(limit: int = 20) -> dict[str, Any]:
    """VULN: Returns all flagged transactions across all customers without auth."""
    items = list(_FLAGGED_TRANSACTIONS.items())[:limit]
    return {"flagged_transactions": [{"transaction_id": k, **v} for k, v in items], "total": len(_FLAGGED_TRANSACTIONS)}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
