"""FinTech GOAT — MCP Market Data — DELIBERATELY VULNERABLE."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse


def _setup_logger(name: str) -> "logging.Logger":
    """Named logger with its own StreamHandler — survives uvicorn dictConfig reset."""
    log = logging.getLogger(name)
    if not log.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        log.addHandler(_h)
    log.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
    log.propagate = False
    return log

logger = _setup_logger("mcp_market_data")
mcp = FastMCP(name="fintech-market-data", instructions="Pinnacle Bank market data tool API.")


def _summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"keys": sorted(arguments.keys()), "arg_count": len(arguments)}

_PRICES = {
    "AAPL": 189.50,
    "MSFT": 415.20,
    "NVDA": 875.40,
    "GOOGL": 172.30,
    "BTC-USD": 67400.00,
    "ETH-USD": 3250.00,
    "AMZN": 185.60,
    "TSLA": 248.90,
    "META": 512.30,
    "BRK-B": 398.20,
}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "market-data"})


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
    started_at = time.monotonic()
    logger.info("tool_call start id=%s tool=%s args=%s", call_id, tool_name, _summarize_arguments(arguments))
    try:
        if tool_name == "get_price":
            result = await get_price(**arguments)
        elif tool_name == "fetch_external_feed":
            result = await fetch_external_feed(**arguments)
        elif tool_name == "get_market_summary":
            result = await get_market_summary(**arguments)
        else:
            logger.warning("tool_call unknown id=%s tool=%s args=%s", call_id, tool_name, _summarize_arguments(arguments))
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        elapsed_ms = (time.monotonic() - started_at) * 1000
        result_summary = list(result.keys()) if isinstance(result, dict) else type(result).__name__
        logger.info("tool_call complete id=%s tool=%s elapsed_ms=%.1f result=%s", call_id, tool_name, elapsed_ms, result_summary)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        elapsed_ms = (time.monotonic() - started_at) * 1000
        logger.exception("tool_call failed id=%s tool=%s elapsed_ms=%.1f args=%s", call_id, tool_name, elapsed_ms, _summarize_arguments(arguments))
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


@mcp.tool()
async def get_price(ticker: str) -> dict[str, Any]:
    """Return current market price for ticker."""
    price = _PRICES.get(ticker.upper())
    if price is None:
        return {"error": f"Unknown ticker: {ticker}"}
    return {"ticker": ticker.upper(), "price": price, "currency": "USD"}


@mcp.tool()
async def fetch_external_feed(url: str) -> dict[str, Any]:
    """VULN-MCP-07: SSRF — fetches any URL without validation."""
    resp = requests.get(url, timeout=10, allow_redirects=True)
    return {
        "url": url,
        "status_code": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
        "content": resp.text[:4096],
    }


@mcp.tool()
async def get_market_summary() -> dict[str, Any]:
    """Returns summary of major market indices."""
    return {
        "indices": {
            "S&P500": {"value": 5218.40, "change": 0.62, "change_pct": "+0.62%"},
            "NASDAQ": {"value": 16340.80, "change": 1.21, "change_pct": "+1.21%"},
            "DOW": {"value": 38890.50, "change": -0.18, "change_pct": "-0.18%"},
        },
        "timestamp": "2026-04-13T16:00:00Z",
    }


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
