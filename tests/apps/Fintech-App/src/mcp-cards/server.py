"""FinTech GOAT — MCP Cards — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_cards")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-cards", instructions="Pinnacle Bank cards tool API.")

_CARDS = {
    "CARD-001": {"holder": "Alice Johnson", "last4": "4421", "expiry": "12/28", "cvv_hint": "3xx", "credit_limit": 10000, "balance_owed": 2340.50, "status": "active"},
    "CARD-002": {"holder": "Bob Martinez", "last4": "8832", "expiry": "06/27", "cvv_hint": "1xx", "credit_limit": 5000, "balance_owed": 890.00, "status": "active"},
    "CARD-003": {"holder": "Carol Williams", "last4": "1195", "expiry": "09/29", "cvv_hint": "9xx", "credit_limit": 50000, "balance_owed": 12100.00, "status": "active"},
}

_FAKE_TRANSACTIONS = [
    {"txn_id": "TXN-a1b2c3", "merchant": "Amazon", "amount": 89.99, "date": "2026-04-10"},
    {"txn_id": "TXN-d4e5f6", "merchant": "Starbucks", "amount": 12.50, "date": "2026-04-09"},
    {"txn_id": "TXN-g7h8i9", "merchant": "Uber", "amount": 23.40, "date": "2026-04-08"},
    {"txn_id": "TXN-j0k1l2", "merchant": "Netflix", "amount": 15.99, "date": "2026-04-07"},
    {"txn_id": "TXN-m3n4o5", "merchant": "Whole Foods", "amount": 134.20, "date": "2026-04-06"},
    {"txn_id": "TXN-p6q7r8", "merchant": "Apple Store", "amount": 999.00, "date": "2026-04-05"},
    {"txn_id": "TXN-s9t0u1", "merchant": "Shell Gas", "amount": 65.30, "date": "2026-04-04"},
    {"txn_id": "TXN-v2w3x4", "merchant": "Delta Airlines", "amount": 450.00, "date": "2026-04-03"},
    {"txn_id": "TXN-y5z6a7", "merchant": "Costco", "amount": 210.75, "date": "2026-04-02"},
    {"txn_id": "TXN-b8c9d0", "merchant": "Target", "amount": 78.45, "date": "2026-04-01"},
]


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "cards"})


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
        if tool_name == "get_card_details":
            result = await get_card_details(**arguments)
        elif tool_name == "get_card_transactions":
            result = await get_card_transactions(**arguments)
        elif tool_name == "freeze_card":
            result = await freeze_card(**arguments)
        elif tool_name == "unfreeze_card":
            result = await unfreeze_card(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call error tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


@mcp.tool()
async def get_card_details(card_id: str) -> dict[str, Any]:
    """VULN-MCP-04: Returns CVV hint without authentication."""
    card = _CARDS.get(card_id)
    if not card:
        return {"error": f"Card {card_id} not found"}
    return {"card_id": card_id, **card}


@mcp.tool()
async def get_card_transactions(card_id: str, limit: int = 10) -> dict[str, Any]:
    """Returns card transactions. VULN: no ownership check."""
    if card_id not in _CARDS:
        return {"error": f"Card {card_id} not found"}
    return {"card_id": card_id, "transactions": _FAKE_TRANSACTIONS[:limit]}


@mcp.tool()
async def freeze_card(card_id: str) -> dict[str, Any]:
    """Freeze a card. VULN: no authentication or ownership check."""
    if card_id not in _CARDS:
        return {"error": f"Card {card_id} not found"}
    _CARDS[card_id]["status"] = "frozen"
    return {"card_id": card_id, "status": "frozen"}


@mcp.tool()
async def unfreeze_card(card_id: str) -> dict[str, Any]:
    """Unfreeze a card. VULN: no authentication or ownership check."""
    if card_id not in _CARDS:
        return {"error": f"Card {card_id} not found"}
    _CARDS[card_id]["status"] = "active"
    return {"card_id": card_id, "status": "active"}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
