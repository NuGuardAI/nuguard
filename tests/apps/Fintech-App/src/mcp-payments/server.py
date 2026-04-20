"""FinTech GOAT — MCP Payments — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_payments")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-payments", instructions="Pinnacle Bank payments tool API.")

_PAYMENTS: dict = {
    "PAY-7a3f9b": {"from_account": "ACCT-001", "to_account": "ACCT-002", "amount": 500.00, "currency": "USD", "reference": "Rent payment", "status": "completed"},
    "PAY-2c8e1d": {"from_account": "ACCT-003", "to_account": "ACCT-005", "amount": 12000.00, "currency": "USD", "reference": "Investment transfer", "status": "completed"},
    "PAY-5f4a2e": {"from_account": "ACCT-008", "to_account": "ACCT-001", "amount": 250.00, "currency": "USD", "reference": "Loan repayment", "status": "pending"},
}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "payments"})


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
        if tool_name == "initiate_payment":
            result = await initiate_payment(**arguments)
        elif tool_name == "get_payment_status":
            result = await get_payment_status(**arguments)
        elif tool_name == "cancel_payment":
            result = await cancel_payment(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call error tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


@mcp.tool()
async def initiate_payment(from_account: str, to_account: str, amount: float, currency: str = "USD", reference: str = "") -> dict[str, Any]:
    """Initiate a payment. VULN: no check that caller owns from_account."""
    payment_id = f"PAY-{uuid.uuid4().hex[:6]}"
    _PAYMENTS[payment_id] = {
        "from_account": from_account,
        "to_account": to_account,
        "amount": amount,
        "currency": currency,
        "reference": reference,
        "status": "pending",
    }
    return {"payment_id": payment_id, "status": "pending", "from_account": from_account, "to_account": to_account, "amount": amount, "currency": currency}


@mcp.tool()
async def get_payment_status(payment_id: str) -> dict[str, Any]:
    """Get payment status. VULN: any payment ID without authentication."""
    payment = _PAYMENTS.get(payment_id)
    if not payment:
        return {"error": f"Payment {payment_id} not found"}
    return {"payment_id": payment_id, **payment}


@mcp.tool()
async def cancel_payment(payment_id: str) -> dict[str, Any]:
    """Cancel a payment. VULN: cancels any payment without ownership check."""
    if payment_id not in _PAYMENTS:
        return {"error": f"Payment {payment_id} not found"}
    _PAYMENTS[payment_id]["status"] = "cancelled"
    return {"payment_id": payment_id, "status": "cancelled"}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
