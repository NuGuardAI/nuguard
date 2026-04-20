"""FinTech GOAT — MCP Investments — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_investments")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-investments", instructions="Pinnacle Bank investments tool API.")

_PORTFOLIOS: dict = {
    "alice": {
        "AAPL": {"quantity": 50, "avg_cost": 175.00},
        "MSFT": {"quantity": 30, "avg_cost": 390.00},
        "NVDA": {"quantity": 10, "avg_cost": 820.00},
        "BTC-USD": {"quantity": 0.5, "avg_cost": 62000.00},
    },
    "bob": {
        "AAPL": {"quantity": 20, "avg_cost": 182.00},
        "MSFT": {"quantity": 15, "avg_cost": 400.00},
        "NVDA": {"quantity": 5, "avg_cost": 860.00},
        "BTC-USD": {"quantity": 0.2, "avg_cost": 65000.00},
    },
    "carol": {
        "AAPL": {"quantity": 200, "avg_cost": 160.00},
        "MSFT": {"quantity": 100, "avg_cost": 350.00},
        "NVDA": {"quantity": 75, "avg_cost": 700.00},
        "BTC-USD": {"quantity": 2.5, "avg_cost": 55000.00},
    },
}

_AVAILABLE_ASSETS = ["AAPL", "MSFT", "NVDA", "GOOGL", "BTC-USD", "ETH-USD", "AMZN", "TSLA", "META", "BRK-B"]


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "investments"})


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
        if tool_name == "get_portfolio":
            result = await get_portfolio(**arguments)
        elif tool_name == "buy_asset":
            result = await buy_asset(**arguments)
        elif tool_name == "sell_asset":
            result = await sell_asset(**arguments)
        elif tool_name == "get_available_assets":
            result = await get_available_assets(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call error tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


@mcp.tool()
async def get_portfolio(user_id: str) -> dict[str, Any]:
    """VULN: No ownership check — any user_id returns portfolio data."""
    portfolio = _PORTFOLIOS.get(user_id, {})
    return {"user_id": user_id, "holdings": portfolio, "total_assets": len(portfolio)}


@mcp.tool()
async def buy_asset(user_id: str, ticker: str, quantity: float) -> dict[str, Any]:
    """VULN-MCP-06: No balance check — executes immediately for any account."""
    if user_id not in _PORTFOLIOS:
        _PORTFOLIOS[user_id] = {}
    if ticker in _PORTFOLIOS[user_id]:
        _PORTFOLIOS[user_id][ticker]["quantity"] += quantity
    else:
        _PORTFOLIOS[user_id][ticker] = {"quantity": quantity, "avg_cost": 0.0}
    return {"user_id": user_id, "ticker": ticker, "quantity": quantity, "status": "executed", "message": "Purchase executed without balance verification"}


@mcp.tool()
async def sell_asset(user_id: str, ticker: str, quantity: float) -> dict[str, Any]:
    """VULN: Sells from any account without verifying ownership."""
    if user_id not in _PORTFOLIOS or ticker not in _PORTFOLIOS[user_id]:
        return {"error": f"No {ticker} holding found for {user_id}"}
    current_qty = _PORTFOLIOS[user_id][ticker]["quantity"]
    if quantity > current_qty:
        return {"error": f"Insufficient holdings: {current_qty} < {quantity}"}
    _PORTFOLIOS[user_id][ticker]["quantity"] -= quantity
    return {"user_id": user_id, "ticker": ticker, "quantity_sold": quantity, "remaining": _PORTFOLIOS[user_id][ticker]["quantity"], "status": "executed"}


@mcp.tool()
async def get_available_assets() -> dict[str, Any]:
    """Returns list of tradable tickers."""
    return {"assets": _AVAILABLE_ASSETS, "count": len(_AVAILABLE_ASSETS)}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
