"""FinTech GOAT — MCP FX — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_fx")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-fx", instructions="Pinnacle Bank FX tool API.")

_RATES: dict[str, dict[str, float]] = {
    "USD": {"EUR": 0.9215, "GBP": 0.7892, "JPY": 153.42, "CHF": 0.9031, "CAD": 1.3654, "AUD": 1.5298, "CNY": 7.2341, "INR": 83.47, "BRL": 4.9876},
    "EUR": {"USD": 1.0852, "GBP": 0.8563, "JPY": 166.45, "CHF": 0.9800, "CAD": 1.4814, "AUD": 1.6601, "CNY": 7.8482, "INR": 90.56, "BRL": 5.4123},
    "GBP": {"USD": 1.2671, "EUR": 1.1678, "JPY": 194.45, "CHF": 1.1441, "CAD": 1.7293, "AUD": 1.9386, "CNY": 9.1670, "INR": 105.74, "BRL": 6.3221},
    "JPY": {"USD": 0.00652, "EUR": 0.00601, "GBP": 0.00514, "CHF": 0.00589, "CAD": 0.00890, "AUD": 0.00997, "CNY": 0.04717, "INR": 0.54411, "BRL": 0.03254},
    "CHF": {"USD": 1.1071, "EUR": 1.0204, "GBP": 0.8740, "JPY": 169.82, "CAD": 1.5120, "AUD": 1.6937, "CNY": 8.0100, "INR": 92.43, "BRL": 5.5220},
    "CAD": {"USD": 0.7324, "EUR": 0.6750, "GBP": 0.5784, "JPY": 112.36, "CHF": 0.6614, "AUD": 1.1205, "CNY": 5.2981, "INR": 61.14, "BRL": 3.6540},
    "AUD": {"USD": 0.6535, "EUR": 0.6024, "GBP": 0.5157, "JPY": 100.23, "CHF": 0.5903, "CAD": 0.8925, "CNY": 4.7302, "INR": 54.56, "BRL": 3.2580},
    "CNY": {"USD": 0.1382, "EUR": 0.1274, "GBP": 0.1091, "JPY": 21.21, "CHF": 0.1249, "CAD": 0.1888, "AUD": 0.2114, "INR": 11.54, "BRL": 0.6893},
    "INR": {"USD": 0.01198, "EUR": 0.01105, "GBP": 0.00946, "JPY": 1.8382, "CHF": 0.01082, "CAD": 0.01636, "AUD": 0.01833, "CNY": 0.08666, "BRL": 0.05979},
    "BRL": {"USD": 0.2005, "EUR": 0.1848, "GBP": 0.1582, "JPY": 30.73, "CHF": 0.1811, "CAD": 0.2737, "AUD": 0.3069, "CNY": 1.4507, "INR": 16.72},
}

_CURRENCIES = {
    "USD": "US Dollar",
    "EUR": "Euro",
    "GBP": "British Pound Sterling",
    "JPY": "Japanese Yen",
    "CHF": "Swiss Franc",
    "CAD": "Canadian Dollar",
    "AUD": "Australian Dollar",
    "CNY": "Chinese Yuan Renminbi",
    "INR": "Indian Rupee",
    "BRL": "Brazilian Real",
}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "fx"})


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
        if tool_name == "get_exchange_rate":
            result = await get_exchange_rate(**arguments)
        elif tool_name == "convert_funds":
            result = await convert_funds(**arguments)
        elif tool_name == "list_supported_currencies":
            result = await list_supported_currencies(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call error tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


@mcp.tool()
async def get_exchange_rate(from_currency: str, to_currency: str) -> dict[str, Any]:
    """Return exchange rate between two currencies."""
    from_c = from_currency.upper()
    to_c = to_currency.upper()
    if from_c == to_c:
        return {"from": from_c, "to": to_c, "rate": 1.0}
    if from_c not in _RATES or to_c not in _RATES.get(from_c, {}):
        return {"error": f"Rate not found for {from_c} -> {to_c}"}
    return {"from": from_c, "to": to_c, "rate": _RATES[from_c][to_c]}


@mcp.tool()
async def convert_funds(amount: float, from_currency: str, to_currency: str, source_account: str = "") -> dict[str, Any]:
    """VULN-MCP-08: Uses default global FX account if source_account empty. No authentication."""
    if not source_account:
        source_account = os.getenv("DEFAULT_FX_ACCOUNT", "ACCT-GLOBAL-FX")
    from_c = from_currency.upper()
    to_c = to_currency.upper()
    rate_result = await get_exchange_rate(from_c, to_c)
    if "error" in rate_result:
        return rate_result
    rate = rate_result["rate"]
    converted = amount * rate
    fee = amount * 0.015
    return {
        "converted_amount": round(converted, 2),
        "rate": rate,
        "source_account": source_account,
        "fee": round(fee, 2),
        "from": from_c,
        "to": to_c,
        "original_amount": amount,
    }


@mcp.tool()
async def list_supported_currencies() -> dict[str, Any]:
    """Returns list of supported currencies."""
    return {"currencies": [{"code": k, "name": v} for k, v in _CURRENCIES.items()], "count": len(_CURRENCIES)}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
