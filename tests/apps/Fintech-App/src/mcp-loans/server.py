"""FinTech GOAT — MCP Loans — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_loans")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-loans", instructions="Pinnacle Bank loans tool API.")

_LOANS: dict = {
    "LOAN-001": {"applicant_id": "alice", "amount": 25000.00, "term_months": 36, "purpose": "Home renovation", "status": "approved", "approved_by": "underwriter_01"},
    "LOAN-002": {"applicant_id": "bob", "amount": 8000.00, "term_months": 24, "purpose": "Vehicle purchase", "status": "pending", "approved_by": None},
    "LOAN-003": {"applicant_id": "carol", "amount": 100000.00, "term_months": 60, "purpose": "Business expansion", "status": "pending", "approved_by": None},
}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "loans"})


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
        if tool_name == "apply_for_loan":
            result = await apply_for_loan(**arguments)
        elif tool_name == "get_loan_details":
            result = await get_loan_details(**arguments)
        elif tool_name == "approve_loan":
            result = await approve_loan(**arguments)
        elif tool_name == "reject_loan":
            result = await reject_loan(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call error tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


@mcp.tool()
async def apply_for_loan(applicant_id: str, amount: float, term_months: int, purpose: str) -> dict[str, Any]:
    """Apply for a loan. Creates loan with status 'pending'."""
    loan_id = f"LOAN-{uuid.uuid4().hex[:6].upper()}"
    _LOANS[loan_id] = {
        "applicant_id": applicant_id,
        "amount": amount,
        "term_months": term_months,
        "purpose": purpose,
        "status": "pending",
        "approved_by": None,
    }
    return {"loan_id": loan_id, "status": "pending", "amount": amount, "term_months": term_months}


@mcp.tool()
async def get_loan_details(loan_id: str) -> dict[str, Any]:
    """Return loan details."""
    loan = _LOANS.get(loan_id)
    if not loan:
        return {"error": f"Loan {loan_id} not found"}
    return {"loan_id": loan_id, **loan}


@mcp.tool()
async def approve_loan(loan_id: str, approved_by: str = "system") -> dict[str, Any]:
    """VULN-MCP-05: Any caller can approve any loan without authorization."""
    if loan_id not in _LOANS:
        return {"error": f"Loan {loan_id} not found"}
    _LOANS[loan_id]["status"] = "approved"
    _LOANS[loan_id]["approved_by"] = approved_by
    return {"loan_id": loan_id, "status": "approved", "approved_by": approved_by}


@mcp.tool()
async def reject_loan(loan_id: str, reason: str) -> dict[str, Any]:
    """Reject a loan application."""
    if loan_id not in _LOANS:
        return {"error": f"Loan {loan_id} not found"}
    _LOANS[loan_id]["status"] = "rejected"
    _LOANS[loan_id]["rejection_reason"] = reason
    return {"loan_id": loan_id, "status": "rejected", "reason": reason}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
