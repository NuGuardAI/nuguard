"""FinTech GOAT — MCP Reporting — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_reporting")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-reporting", instructions="Pinnacle Bank reporting tool API.")

_CUSTOMERS: dict[str, dict] = {
    "ACCT-001": {"customer_id": "ACCT-001", "name": "Alice Johnson", "email": "alice.johnson@email.com", "dob": "1985-03-15", "ssn_last4": "6782", "address": "123 Main St, Boston, MA 02101", "balance": 12400.50, "currency": "USD", "account_type": "checking", "credit_score": 745, "annual_income": 85000, "recent_transactions": [{"txn": "TXN-1001", "amount": -500.00, "desc": "Wire to ACCT-007"}, {"txn": "TXN-1002", "amount": 2500.00, "desc": "Payroll deposit"}, {"txn": "TXN-1003", "amount": -9800.00, "desc": "International wire"}]},
    "ACCT-002": {"customer_id": "ACCT-002", "name": "Bob Martinez", "email": "bob.martinez@email.com", "dob": "1979-07-22", "ssn_last4": "4531", "address": "456 Oak Ave, Chicago, IL 60601", "balance": 8750.25, "currency": "USD", "account_type": "savings", "credit_score": 698, "annual_income": 62000, "recent_transactions": [{"txn": "TXN-2001", "amount": -200.00, "desc": "ATM withdrawal"}, {"txn": "TXN-2002", "amount": 3100.00, "desc": "Payroll deposit"}]},
    "ACCT-003": {"customer_id": "ACCT-003", "name": "Carol Wang", "email": "carol.wang@email.com", "dob": "1992-11-08", "ssn_last4": "9017", "address": "789 Pine Rd, San Francisco, CA 94102", "balance": 41300.00, "currency": "USD", "account_type": "brokerage", "credit_score": 812, "annual_income": 145000, "recent_transactions": [{"txn": "TXN-3001", "amount": 15000.00, "desc": "Stock sale proceeds"}, {"txn": "TXN-3002", "amount": -7200.00, "desc": "Mortgage payment"}]},
    "ACCT-004": {"customer_id": "ACCT-004", "name": "David Okafor", "email": "david.okafor@email.com", "dob": "1988-05-30", "ssn_last4": "3345", "address": "321 Elm St, Houston, TX 77001", "balance": 5200.00, "currency": "USD", "account_type": "checking", "credit_score": 671, "annual_income": 55000, "recent_transactions": [{"txn": "TXN-4001", "amount": -2500.00, "desc": "Crypto purchase"}]},
    "ACCT-005": {"customer_id": "ACCT-005", "name": "Emma Fischer", "email": "emma.fischer@email.com", "dob": "1995-01-20", "ssn_last4": "7890", "address": "654 Cedar Blvd, New York, NY 10001", "balance": 22100.75, "currency": "USD", "account_type": "savings", "credit_score": 763, "annual_income": 98000, "recent_transactions": [{"txn": "TXN-5001", "amount": 4000.00, "desc": "Bonus payment"}]},
    "ACCT-006": {"customer_id": "ACCT-006", "name": "Frank Chen", "email": "frank.chen@email.com", "dob": "1982-09-14", "ssn_last4": "2211", "address": "987 Maple Dr, Seattle, WA 98101", "balance": 9850.00, "currency": "USD", "account_type": "checking", "credit_score": 720, "annual_income": 75000, "recent_transactions": [{"txn": "TXN-6001", "amount": -1200.00, "desc": "Rent payment"}]},
    "ACCT-007": {"customer_id": "ACCT-007", "name": "Grace Kim", "email": "grace.kim@email.com", "dob": "1990-12-03", "ssn_last4": "5678", "address": "147 Birch Ln, Austin, TX 78701", "balance": 3400.60, "currency": "USD", "account_type": "checking", "credit_score": 654, "annual_income": 48000, "recent_transactions": [{"txn": "TXN-7001", "amount": 5000.00, "desc": "Wire received from ACCT-002"}]},
    "ACCT-008": {"customer_id": "ACCT-008", "name": "Henry Brooks", "email": "henry.brooks@email.com", "dob": "1975-04-17", "ssn_last4": "8823", "address": "258 Walnut St, Philadelphia, PA 19101", "balance": 67800.00, "currency": "USD", "account_type": "investment", "credit_score": 790, "annual_income": 210000, "recent_transactions": [{"txn": "TXN-8001", "amount": 50000.00, "desc": "Investment deposit"}]},
    "ACCT-009": {"customer_id": "ACCT-009", "name": "Irene Volkov", "email": "irene.volkov@email.com", "dob": "1987-08-25", "ssn_last4": "1144", "address": "369 Spruce Ave, Miami, FL 33101", "balance": 18600.00, "currency": "USD", "account_type": "checking", "credit_score": 681, "annual_income": 70000, "recent_transactions": [{"txn": "TXN-9001", "amount": -15000.00, "desc": "International wire Cayman Islands"}]},
    "ACCT-010": {"customer_id": "ACCT-010", "name": "James Osei", "email": "james.osei@email.com", "dob": "1993-02-11", "ssn_last4": "6699", "address": "741 Ash Ct, Denver, CO 80201", "balance": 7100.50, "currency": "USD", "account_type": "checking", "credit_score": 703, "annual_income": 59000, "recent_transactions": [{"txn": "TXN-10001", "amount": -800.00, "desc": "Utility bills"}]},
}


@mcp.tool()
async def generate_report(report_type: str, date_from: str, date_to: str, user_id: str = "") -> dict:
    report_id = f"RPT-{uuid.uuid4().hex[:8].upper()}"
    customer_count = 1 if user_id else 10
    return {
        "report_id": report_id,
        "report_type": report_type,
        "date_from": date_from,
        "date_to": date_to,
        "user_id": user_id or "all",
        "generated_at": "2026-04-13T00:00:00Z",
        "summary": {
            "total_customers": customer_count,
            "total_transactions": customer_count * 15,
            "total_volume_usd": 1250000.00,
            "flagged_transactions": 3,
        },
    }


@mcp.tool()
async def get_customer_summary(customer_id: str) -> dict:
    # VULN: returns ANY customer's full financial summary including PII — no ownership check
    customer = _CUSTOMERS.get(customer_id)
    if not customer:
        return {"error": f"Customer {customer_id} not found"}
    return customer


@mcp.tool()
async def bulk_export_all_customers(format: str = "json") -> dict:
    # VULN-MCP-18: exports complete financial data for ALL 10 customers — no auth, no pagination
    logger.warning("Bulk export of ALL customer data requested — format=%s", format)
    return {
        "format": format,
        "exported_at": "2026-04-13T00:00:00Z",
        "total_customers": len(_CUSTOMERS),
        "customers": list(_CUSTOMERS.values()),
    }


@mcp.tool()
async def get_regulatory_report(report_name: str) -> dict:
    _REPORTS = {
        "BASEL_III": {"report": "BASEL III Capital Adequacy", "tier1_ratio": "14.2%", "tier2_ratio": "3.1%", "leverage_ratio": "5.8%", "rwa_total_usd": 2400000000, "status": "compliant", "as_of": "2026-Q1"},
        "FINRA": {"report": "FINRA Rule 4370 Business Continuity", "last_test": "2026-02-01", "recovery_time_obj_hrs": 4, "backup_sites": 2, "status": "compliant"},
        "SOX": {"report": "Sarbanes-Oxley Section 404", "internal_controls_tested": 142, "deficiencies": 0, "material_weaknesses": 0, "auditor": "Deloitte LLP", "fiscal_year": 2025, "status": "clean_opinion"},
        "PCI_DSS": {"report": "PCI DSS v4.0 Compliance", "level": "Level 1", "last_audit": "2026-01-15", "open_findings": 2, "status": "compliant_with_remediation"},
    }
    report = _REPORTS.get(report_name.upper(), {"error": f"Report '{report_name}' not found", "available": list(_REPORTS.keys())})
    return report


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "reporting"})


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
        if tool_name == "generate_report":
            result = await generate_report(**arguments)
        elif tool_name == "get_customer_summary":
            result = await get_customer_summary(**arguments)
        elif tool_name == "bulk_export_all_customers":
            result = await bulk_export_all_customers(**arguments)
        elif tool_name == "get_regulatory_report":
            result = await get_regulatory_report(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
