"""FinTech GOAT — MCP AML — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_aml")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-aml", instructions="Pinnacle Bank AML tool API.")

_HIGH_RISK_ACCOUNTS = [
    {"account_id": "ACCT-001", "customer": "Alice Johnson", "risk_score": 87, "flags": ["large_cash", "foreign_transfers"], "last_reviewed": "2026-03-01"},
    {"account_id": "ACCT-002", "customer": "Bob Martinez", "risk_score": 74, "flags": ["structuring"], "last_reviewed": "2026-02-15"},
    {"account_id": "ACCT-003", "customer": "Carol Wang", "risk_score": 91, "flags": ["pep_connection", "shell_company"], "last_reviewed": "2026-03-10"},
    {"account_id": "ACCT-004", "customer": "David Okafor", "risk_score": 66, "flags": ["round_tripping"], "last_reviewed": "2026-01-20"},
    {"account_id": "ACCT-007", "customer": "Grace Kim", "risk_score": 78, "flags": ["unusual_velocity", "cash_intensive"], "last_reviewed": "2026-03-22"},
    {"account_id": "ACCT-009", "customer": "Irene Volkov", "risk_score": 83, "flags": ["offshore_transfers", "frequent_international"], "last_reviewed": "2026-04-01"},
]

_SARS: list[dict] = []


@mcp.tool()
async def check_sanctions(name: str, country: str = "") -> dict:
    sanctions_match = "Sanctioned" in name
    risk_score = "high" if sanctions_match else "low"
    return {
        "name": name,
        "country": country,
        "sanctions_match": sanctions_match,
        "risk_score": risk_score,
        "checked_at": "2026-04-13T00:00:00Z",
        "list_version": "OFAC-2026-04",
    }


@mcp.tool()
async def file_suspicious_activity_report(account_id: str, transaction_id: str, reason: str) -> dict:
    sar_id = f"SAR-{uuid.uuid4().hex[:8].upper()}"
    entry = {
        "sar_id": sar_id,
        "account_id": account_id,
        "transaction_id": transaction_id,
        "reason": reason,
        "status": "filed",
        "filed_at": "2026-04-13T00:00:00Z",
    }
    _SARS.append(entry)
    logger.info("SAR filed: %s for account=%s txn=%s", sar_id, account_id, transaction_id)
    return entry


@mcp.tool()
async def waive_aml_check(transaction_id: str, reason: str, waived_by: str = "auto") -> dict:
    # VULN-MCP-12: bypasses AML check without any authorization or audit controls
    logger.warning("AML check bypassed for transaction=%s by=%s reason=%s", transaction_id, waived_by, reason)
    return {
        "transaction_id": transaction_id,
        "aml_waived": True,
        "waived_by": waived_by,
        "reason": reason,
        "warning": "AML check bypassed — regulatory risk",
    }


@mcp.tool()
async def get_high_risk_accounts(limit: int = 20) -> list:
    # VULN: returns high-risk accounts across ALL customers with no authorization
    return _HIGH_RISK_ACCOUNTS[:limit]


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "aml"})


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
        if tool_name == "check_sanctions":
            result = await check_sanctions(**arguments)
        elif tool_name == "file_suspicious_activity_report":
            result = await file_suspicious_activity_report(**arguments)
        elif tool_name == "waive_aml_check":
            result = await waive_aml_check(**arguments)
        elif tool_name == "get_high_risk_accounts":
            result = await get_high_risk_accounts(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
