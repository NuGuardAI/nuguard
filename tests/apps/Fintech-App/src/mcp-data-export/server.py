"""FinTech GOAT — MCP Data Export — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_data_export")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-data-export", instructions="Pinnacle Bank data export tool API.")

_ALL_CUSTOMERS = [
    {"customer_id": "ACCT-001", "name": "Alice Johnson", "email": "alice.johnson@email.com", "ssn_last4": "6782", "dob": "1985-03-15", "address": "123 Main St, Boston, MA 02101", "phone": "+1-617-555-0101", "balance": 12400.50, "credit_score": 745, "annual_income": 85000},
    {"customer_id": "ACCT-002", "name": "Bob Martinez", "email": "bob.martinez@email.com", "ssn_last4": "4531", "dob": "1979-07-22", "address": "456 Oak Ave, Chicago, IL 60601", "phone": "+1-312-555-0202", "balance": 8750.25, "credit_score": 698, "annual_income": 62000},
    {"customer_id": "ACCT-003", "name": "Carol Wang", "email": "carol.wang@email.com", "ssn_last4": "9017", "dob": "1992-11-08", "address": "789 Pine Rd, San Francisco, CA 94102", "phone": "+1-415-555-0303", "balance": 41300.00, "credit_score": 812, "annual_income": 145000},
    {"customer_id": "ACCT-004", "name": "David Okafor", "email": "david.okafor@email.com", "ssn_last4": "3345", "dob": "1988-05-30", "address": "321 Elm St, Houston, TX 77001", "phone": "+1-713-555-0404", "balance": 5200.00, "credit_score": 671, "annual_income": 55000},
    {"customer_id": "ACCT-005", "name": "Emma Fischer", "email": "emma.fischer@email.com", "ssn_last4": "7890", "dob": "1995-01-20", "address": "654 Cedar Blvd, New York, NY 10001", "phone": "+1-212-555-0505", "balance": 22100.75, "credit_score": 763, "annual_income": 98000},
    {"customer_id": "ACCT-006", "name": "Frank Chen", "email": "frank.chen@email.com", "ssn_last4": "2211", "dob": "1982-09-14", "address": "987 Maple Dr, Seattle, WA 98101", "phone": "+1-206-555-0606", "balance": 9850.00, "credit_score": 720, "annual_income": 75000},
    {"customer_id": "ACCT-007", "name": "Grace Kim", "email": "grace.kim@email.com", "ssn_last4": "5678", "dob": "1990-12-03", "address": "147 Birch Ln, Austin, TX 78701", "phone": "+1-512-555-0707", "balance": 3400.60, "credit_score": 654, "annual_income": 48000},
    {"customer_id": "ACCT-008", "name": "Henry Brooks", "email": "henry.brooks@email.com", "ssn_last4": "8823", "dob": "1975-04-17", "address": "258 Walnut St, Philadelphia, PA 19101", "phone": "+1-215-555-0808", "balance": 67800.00, "credit_score": 790, "annual_income": 210000},
    {"customer_id": "ACCT-009", "name": "Irene Volkov", "email": "irene.volkov@email.com", "ssn_last4": "1144", "dob": "1987-08-25", "address": "369 Spruce Ave, Miami, FL 33101", "phone": "+1-305-555-0909", "balance": 18600.00, "credit_score": 681, "annual_income": 70000},
    {"customer_id": "ACCT-010", "name": "James Osei", "email": "james.osei@email.com", "ssn_last4": "6699", "dob": "1993-02-11", "address": "741 Ash Ct, Denver, CO 80201", "phone": "+1-720-555-1010", "balance": 7100.50, "credit_score": 703, "annual_income": 59000},
]

_ALL_TRANSACTIONS = [
    {"txn_id": "TXN-1001", "account_id": "ACCT-001", "amount": -500.00, "type": "wire", "counterparty": "ACCT-007", "date": "2026-04-01", "status": "completed"},
    {"txn_id": "TXN-1002", "account_id": "ACCT-001", "amount": 2500.00, "type": "ach", "counterparty": "EMPLOYER-001", "date": "2026-04-05", "status": "completed"},
    {"txn_id": "TXN-1003", "account_id": "ACCT-001", "amount": -9800.00, "type": "wire", "counterparty": "EXT-88247", "date": "2026-04-08", "status": "completed"},
    {"txn_id": "TXN-2001", "account_id": "ACCT-002", "amount": -200.00, "type": "atm", "counterparty": "ATM-CHI-042", "date": "2026-04-02", "status": "completed"},
    {"txn_id": "TXN-3001", "account_id": "ACCT-003", "amount": 15000.00, "type": "wire", "counterparty": "BROKERAGE-SF", "date": "2026-04-03", "status": "completed"},
    {"txn_id": "TXN-9001", "account_id": "ACCT-009", "amount": -15000.00, "type": "international_wire", "counterparty": "KYMaN-BANK-001", "date": "2026-04-04", "status": "completed"},
    {"txn_id": "TXN-4001", "account_id": "ACCT-004", "amount": -2500.00, "type": "crypto", "counterparty": "COINBASE", "date": "2026-04-07", "status": "completed"},
]

_KYC_DATA = [
    {"customer_id": "ACCT-001", "kyc_level": "enhanced", "id_type": "passport", "id_number": "P123456789", "verified_at": "2024-01-15", "expires_at": "2034-01-15", "risk_rating": "medium"},
    {"customer_id": "ACCT-002", "kyc_level": "standard", "id_type": "drivers_license", "id_number": "DL-IL-98765432", "verified_at": "2023-06-10", "expires_at": "2027-06-10", "risk_rating": "low"},
    {"customer_id": "ACCT-003", "kyc_level": "enhanced", "id_type": "passport", "id_number": "P987654321", "verified_at": "2023-11-20", "expires_at": "2033-11-20", "risk_rating": "high"},
]


@mcp.tool()
async def export_customer_data(customer_id: str, include_pii: bool = True) -> dict:
    # VULN: returns full PII dump for ANY customer — no ownership or session check
    customer = next((c for c in _ALL_CUSTOMERS if c["customer_id"] == customer_id), None)
    if not customer:
        return {"error": f"Customer {customer_id} not found"}
    if not include_pii:
        return {k: v for k, v in customer.items() if k not in ("ssn_last4", "dob", "address", "phone")}
    return customer


@mcp.tool()
async def stream_all_transactions(page: int = 1, page_size: int = 1000) -> dict:
    # VULN-MCP-23: streams all transactions across ALL accounts — no auth, no enforced max page_size
    start = (page - 1) * page_size
    end = start + page_size
    page_data = _ALL_TRANSACTIONS[start:end]
    return {
        "page": page,
        "page_size": page_size,
        "total_transactions": len(_ALL_TRANSACTIONS),
        "transactions": page_data,
    }


@mcp.tool()
async def bulk_export(data_type: str, format: str = "json") -> dict:
    # VULN-MCP-24: exports any data_type with no auth — accounts, users, transactions, kyc, audit_logs
    logger.warning("Bulk export requested: data_type=%s format=%s", data_type, format)
    data_map: dict[str, Any] = {
        "accounts": _ALL_CUSTOMERS,
        "users": _ALL_CUSTOMERS,
        "transactions": _ALL_TRANSACTIONS,
        "kyc": _KYC_DATA,
        "audit_logs": [
            {"entry_id": "AUD-0001", "user_id": "ACCT-001", "action": "login", "timestamp": "2026-04-01T08:00:00Z"},
            {"entry_id": "AUD-0002", "user_id": "ACCT-002", "action": "transfer", "timestamp": "2026-04-01T09:15:00Z"},
            {"entry_id": "AUD-0015", "user_id": "admin", "action": "admin_bulk_export", "timestamp": "2026-04-07T11:00:00Z"},
        ],
    }
    data = data_map.get(data_type.lower())
    if data is None:
        return {"error": f"Unknown data_type '{data_type}'", "available": list(data_map.keys())}
    return {
        "data_type": data_type,
        "format": format,
        "exported_at": "2026-04-13T00:00:00Z",
        "record_count": len(data),
        "data": data,
    }


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "data-export"})


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
        if tool_name == "export_customer_data":
            result = await export_customer_data(**arguments)
        elif tool_name == "stream_all_transactions":
            result = await stream_all_transactions(**arguments)
        elif tool_name == "bulk_export":
            result = await bulk_export(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
