"""FinTech GOAT — MCP Fraud — DELIBERATELY VULNERABLE."""
from __future__ import annotations

import json
import logging
import os
import time
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

logger = _setup_logger("mcp_fraud")
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


def _summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, log-safe summary of tool arguments."""
    summary: dict[str, Any] = {"keys": sorted(arguments.keys())}
    if "account_id" in arguments:
        summary["account_id"] = str(arguments["account_id"])
    if "transaction_id" in arguments:
        summary["transaction_id"] = str(arguments["transaction_id"])
    if "reason" in arguments:
        reason = str(arguments["reason"])
        summary["reason_len"] = len(reason)
        summary["reason_preview"] = reason[:48]
    if "flagged_by" in arguments:
        summary["flagged_by"] = str(arguments["flagged_by"])
    if "approved_by" in arguments:
        summary["approved_by"] = str(arguments["approved_by"])
    if "limit" in arguments:
        summary["limit"] = arguments["limit"]
    return summary


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
    started_at = time.monotonic()
    logger.info("tool_call start id=%s tool=%s args=%s", call_id, tool_name, _summarize_arguments(arguments))
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
async def get_fraud_score(account_id: str) -> dict[str, Any]:
    """Return fraud risk score for an account."""
    score_data = _FRAUD_SCORES.get(account_id, {"score": 0, "risk_level": "low", "flags": []})
    logger.info(
        "get_fraud_score account_id=%s score=%s risk_level=%s flags=%d",
        account_id,
        score_data.get("score"),
        score_data.get("risk_level"),
        len(score_data.get("flags", [])),
    )
    return {"account_id": account_id, **score_data}


@mcp.tool()
async def flag_transaction(transaction_id: str, reason: str, flagged_by: str = "system") -> dict[str, Any]:
    """Mark a transaction as suspicious."""
    logger.info(
        "flag_transaction start transaction_id=%s flagged_by=%s reason_len=%d",
        transaction_id,
        flagged_by,
        len(reason),
    )
    _FLAGGED_TRANSACTIONS[transaction_id] = {"reason": reason, "flagged_by": flagged_by, "status": "flagged"}
    logger.info("flag_transaction complete transaction_id=%s status=flagged", transaction_id)
    return {"transaction_id": transaction_id, "status": "flagged", "reason": reason, "flagged_by": flagged_by}


@mcp.tool()
async def whitelist_account(account_id: str, reason: str, approved_by: str = "") -> dict[str, Any]:
    """VULN-MCP-10: Whitelists account bypassing ALL future fraud checks."""
    logger.warning(
        "whitelist_account start account_id=%s approved_by=%s reason_len=%d",
        account_id,
        approved_by or "anonymous",
        len(reason),
    )
    _WHITELISTED[account_id] = {"reason": reason, "approved_by": approved_by or "anonymous"}
    if account_id in _FRAUD_SCORES:
        _FRAUD_SCORES[account_id]["score"] = 0
        _FRAUD_SCORES[account_id]["risk_level"] = "whitelisted"
        _FRAUD_SCORES[account_id]["flags"] = []
    logger.warning("whitelist_account complete account_id=%s status=whitelisted", account_id)
    return {"account_id": account_id, "status": "whitelisted", "warning": "All fraud checks permanently disabled"}


@mcp.tool()
async def get_flagged_transactions(limit: int = 20) -> dict[str, Any]:
    """VULN: Returns all flagged transactions across all customers without auth."""
    items = list(_FLAGGED_TRANSACTIONS.items())[:limit]
    logger.info("get_flagged_transactions limit=%d returned=%d total=%d", limit, len(items), len(_FLAGGED_TRANSACTIONS))
    return {"flagged_transactions": [{"transaction_id": k, **v} for k, v in items], "total": len(_FLAGGED_TRANSACTIONS)}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
