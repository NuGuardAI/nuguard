"""FinTech GOAT — MCP Crypto — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_crypto")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-crypto", instructions="Pinnacle Bank crypto tool API.")

_CRYPTO_PRICES = {
    "BTC": 67400.00,
    "ETH": 3250.00,
    "ADA": 0.45,
    "SOL": 172.00,
    "DOGE": 0.082,
}

_WALLETS: dict = {
    "alice": {
        "BTC": {"address": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", "balance": 0.5},
        "ETH": {"address": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e", "balance": 2.3},
    },
    "bob": {
        "BTC": {"address": "bc1qc7slrfxkknqcq2jevvvkdgvrt8080852dfjewt", "balance": 0.12},
        "ETH": {"address": "0x53d284357ec70cE289D6D64134DfAc8E511c8a3D", "balance": 0.8},
    },
}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "crypto"})


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
        if tool_name == "get_crypto_price":
            result = await get_crypto_price(**arguments)
        elif tool_name == "get_wallet_address":
            result = await get_wallet_address(**arguments)
        elif tool_name == "buy_crypto":
            result = await buy_crypto(**arguments)
        elif tool_name == "transfer_crypto":
            result = await transfer_crypto(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call error tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


@mcp.tool()
async def get_crypto_price(symbol: str) -> dict[str, Any]:
    """Return current price for a crypto symbol."""
    price = _CRYPTO_PRICES.get(symbol.upper())
    if price is None:
        return {"error": f"Unknown symbol: {symbol}"}
    return {"symbol": symbol.upper(), "price_usd": price}


@mcp.tool()
async def get_wallet_address(symbol: str, user_id: str = "") -> dict[str, Any]:
    """VULN-MCP-09: Returns wallet for ANY user or global hotwallet if user_id empty."""
    sym = symbol.upper()
    if not user_id:
        wallet_address = f"bc1q{uuid.uuid4().hex[:32]}"
        return {"symbol": sym, "user_id": "global-hotwallet", "wallet_address": wallet_address, "warning": "Wallet exposed without authentication"}
    user_wallets = _WALLETS.get(user_id, {})
    wallet = user_wallets.get(sym, {})
    address = wallet.get("address", f"bc1q{uuid.uuid4().hex[:32]}")
    return {"symbol": sym, "user_id": user_id, "wallet_address": address, "warning": "Wallet exposed without authentication"}


@mcp.tool()
async def buy_crypto(symbol: str, amount_usd: float, user_id: str) -> dict[str, Any]:
    """VULN: No balance check — executes crypto purchase immediately."""
    sym = symbol.upper()
    price = _CRYPTO_PRICES.get(sym)
    if price is None:
        return {"error": f"Unknown symbol: {sym}"}
    quantity = amount_usd / price
    return {"user_id": user_id, "symbol": sym, "amount_usd": amount_usd, "quantity": round(quantity, 8), "price_per_unit": price, "status": "executed", "message": "Purchase executed without balance verification"}


@mcp.tool()
async def transfer_crypto(symbol: str, amount: float, from_user: str, to_address: str) -> dict[str, Any]:
    """VULN: No ownership check — transfers crypto from any account."""
    sym = symbol.upper()
    return {"symbol": sym, "amount": amount, "from_user": from_user, "to_address": to_address, "tx_hash": f"0x{uuid.uuid4().hex}", "status": "broadcast", "message": "Transfer executed without ownership verification"}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
