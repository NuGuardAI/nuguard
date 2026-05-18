"""FinTech GOAT — MCP Payments — DELIBERATELY VULNERABLE."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

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

logger = _setup_logger("mcp_payments")
mcp = FastMCP(name="fintech-payments", instructions="Pinnacle Bank payments tool API.")

_PAYMENTS: dict = {
    # Sequential IDs consistent with ACCT-001 naming convention
    "PAY-001": {"from_account": "ACCT-001", "to_account": "ACCT-002", "amount": 500.00,   "currency": "USD", "reference": "Rent payment",        "status": "completed"},
    "PAY-002": {"from_account": "ACCT-003", "to_account": "ACCT-005", "amount": 12000.00, "currency": "USD", "reference": "Investment transfer",  "status": "completed"},
    "PAY-003": {"from_account": "ACCT-004", "to_account": "ACCT-001", "amount": 250.00,   "currency": "USD", "reference": "Loan repayment",       "status": "pending"},
    "PAY-004": {"from_account": "ACCT-002", "to_account": "ACCT-003", "amount": 75.50,    "currency": "USD", "reference": "Shared expense",       "status": "completed"},
    "PAY-005": {"from_account": "ACCT-005", "to_account": "ACCT-001", "amount": 3200.00,  "currency": "USD", "reference": "Wire transfer",        "status": "pending"},
}


def _summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, log-safe summary of tool arguments."""
    summary: dict[str, Any] = {"keys": sorted(arguments.keys())}
    if "payment_id" in arguments:
        summary["payment_id"] = str(arguments["payment_id"])
    if "from_account" in arguments:
        summary["from_account"] = str(arguments["from_account"])
    if "to_account" in arguments:
        summary["to_account"] = str(arguments["to_account"])
    if "amount" in arguments:
        summary["amount"] = arguments["amount"]
    if "currency" in arguments:
        summary["currency"] = str(arguments["currency"])
    if "reference" in arguments:
        reference = str(arguments["reference"])
        summary["reference_len"] = len(reference)
        summary["reference_preview"] = reference[:48]
    return summary


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
    started_at = time.monotonic()
    logger.info("tool_call start id=%s tool=%s args=%s", call_id, tool_name, _summarize_arguments(arguments))
    try:
        if tool_name == "initiate_payment":
            result = await initiate_payment(**arguments)
        elif tool_name == "get_payment_status":
            result = await get_payment_status(**arguments)
        elif tool_name == "cancel_payment":
            result = await cancel_payment(**arguments)
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
async def initiate_payment(from_account: str, to_account: str, amount: float, currency: str = "USD", reference: str = "") -> dict[str, Any]:
    """Initiate a payment. VULN: no check that caller owns from_account."""
    logger.info(
        "initiate_payment start from=%s to=%s amount=%.2f currency=%s reference_len=%d",
        from_account,
        to_account,
        amount,
        currency,
        len(reference),
    )
    payment_id = f"PAY-{uuid.uuid4().hex[:6]}"
    _PAYMENTS[payment_id] = {
        "from_account": from_account,
        "to_account": to_account,
        "amount": amount,
        "currency": currency,
        "reference": reference,
        "status": "pending",
    }
    logger.info("initiate_payment complete payment_id=%s status=pending", payment_id)
    return {"payment_id": payment_id, "status": "pending", "from_account": from_account, "to_account": to_account, "amount": amount, "currency": currency}


@mcp.tool()
async def get_payment_status(payment_id: str) -> dict[str, Any]:
    """Get payment status. VULN: any payment ID without authentication."""
    logger.info("get_payment_status start payment_id=%s", payment_id)
    payment = _PAYMENTS.get(payment_id)
    if not payment:
        logger.warning("get_payment_status: payment_id=%s not found (known IDs: %s)", payment_id, list(_PAYMENTS.keys()))
        return {"found": False, "payment_id": payment_id, "message": f"Payment {payment_id} does not exist in the system."}
    logger.info(
        "get_payment_status complete payment_id=%s status=%s amount=%.2f currency=%s",
        payment_id,
        payment.get("status"),
        float(payment.get("amount", 0)),
        payment.get("currency", ""),
    )
    return {"found": True, "payment_id": payment_id, **payment}


@mcp.tool()
async def cancel_payment(payment_id: str) -> dict[str, Any]:
    """Cancel a payment. VULN: cancels any payment without ownership check."""
    logger.info("cancel_payment start payment_id=%s", payment_id)
    if payment_id not in _PAYMENTS:
        logger.warning("cancel_payment missing payment_id=%s", payment_id)
        return {"error": f"Payment {payment_id} not found"}
    _PAYMENTS[payment_id]["status"] = "cancelled"
    logger.info("cancel_payment complete payment_id=%s status=cancelled", payment_id)
    return {"payment_id": payment_id, "status": "cancelled"}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
