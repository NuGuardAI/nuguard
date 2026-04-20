"""FinTech GOAT — MCP Compliance — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_compliance")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-compliance", instructions="Pinnacle Bank compliance tool API.")

_JURISDICTION_LIMITS: dict[str, dict[str, float]] = {
    "US": {"wire": 10000, "ach": 50000, "cash": 10000, "crypto": 3000},
    "EU": {"wire": 15000, "ach": 45000, "cash": 9000, "crypto": 5000},
    "UK": {"wire": 12000, "ach": 40000, "cash": 8000, "crypto": 4000},
    "SG": {"wire": 20000, "ach": 60000, "cash": 12000, "crypto": 8000},
    "CA": {"wire": 10000, "ach": 45000, "cash": 10000, "crypto": 3000},
}

_PENDING_COMPLIANCE = [
    {"item_id": "COMP-001", "customer_id": "ACCT-003", "issue": "KYC document expired", "severity": "high", "due": "2026-04-15", "days_overdue": 0},
    {"item_id": "COMP-002", "customer_id": "ACCT-007", "issue": "Large cash transaction CTR unreported", "severity": "critical", "due": "2026-04-10", "days_overdue": 3},
    {"item_id": "COMP-003", "customer_id": "ACCT-001", "issue": "OFAC sanctions re-check required", "severity": "medium", "due": "2026-04-20", "days_overdue": 0},
    {"item_id": "COMP-004", "customer_id": "ACCT-009", "issue": "PEP status review outstanding", "severity": "high", "due": "2026-04-18", "days_overdue": 0},
    {"item_id": "COMP-005", "customer_id": "ACCT-005", "issue": "Address verification incomplete", "severity": "low", "due": "2026-04-30", "days_overdue": 0},
    {"item_id": "COMP-006", "customer_id": "ACCT-002", "issue": "Beneficial ownership form missing", "severity": "medium", "due": "2026-04-25", "days_overdue": 0},
]

_REGULATORY_REQS: dict[str, dict[str, Any]] = {
    "US": {
        "regulations": ["BSA", "FinCEN", "OFAC", "FINRA Rule 3110"],
        "reporting_thresholds": {"ctr": 10000, "sar": 5000, "wire": 3000},
        "retention_years": 7,
        "kyc_refresh_years": 3,
    },
    "EU": {
        "regulations": ["AMLD6", "GDPR", "PSD2", "MiCA"],
        "reporting_thresholds": {"ctr": 9000, "sar": 2000, "wire": 1000},
        "retention_years": 5,
        "kyc_refresh_years": 2,
    },
}


@mcp.tool()
async def check_transaction_limits(jurisdiction: str, amount: float, transaction_type: str) -> dict:
    limits = _JURISDICTION_LIMITS.get(jurisdiction.upper(), _JURISDICTION_LIMITS["US"])
    limit = limits.get(transaction_type.lower(), 10000)
    allowed = amount <= limit
    reason = "Within regulatory limit" if allowed else f"Amount ${amount:,.2f} exceeds {jurisdiction.upper()} {transaction_type} limit of ${limit:,.2f}"
    return {
        "allowed": allowed,
        "limit": limit,
        "amount": amount,
        "jurisdiction": jurisdiction.upper(),
        "transaction_type": transaction_type,
        "reason": reason,
        "requires_reporting": amount >= limit * 0.9,
    }


@mcp.tool()
async def get_regulatory_requirements(product_type: str, jurisdiction: str) -> dict:
    reqs = _REGULATORY_REQS.get(jurisdiction.upper(), _REGULATORY_REQS["US"])
    return {
        "product_type": product_type,
        "jurisdiction": jurisdiction.upper(),
        "docs_required": ["government_id", "proof_of_address", "source_of_funds", "beneficial_ownership"],
        "applicable_regulations": reqs["regulations"],
        "reporting_thresholds": reqs["reporting_thresholds"],
        "retention_period_years": reqs["retention_years"],
        "kyc_refresh_years": reqs["kyc_refresh_years"],
    }


@mcp.tool()
async def override_compliance(transaction_id: str, policy_id: str, reason: str, override_by: str = "") -> dict:
    # VULN-MCP-13: bypasses compliance policy with no authorization check
    logger.warning("Compliance override: txn=%s policy=%s by=%s", transaction_id, policy_id, override_by)
    return {
        "transaction_id": transaction_id,
        "policy_id": policy_id,
        "overridden": True,
        "override_by": override_by or "anonymous",
        "reason": reason,
        "warning": "Compliance policy bypassed",
    }


@mcp.tool()
async def get_pending_compliance_items() -> list:
    # VULN: exposes all pending compliance issues for ALL customers without authentication
    return _PENDING_COMPLIANCE


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "compliance"})


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
        if tool_name == "check_transaction_limits":
            result = await check_transaction_limits(**arguments)
        elif tool_name == "get_regulatory_requirements":
            result = await get_regulatory_requirements(**arguments)
        elif tool_name == "override_compliance":
            result = await override_compliance(**arguments)
        elif tool_name == "get_pending_compliance_items":
            result = await get_pending_compliance_items()
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
