"""FinTech GOAT — MCP Documents — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_documents")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-documents", instructions="Pinnacle Bank documents tool API.")

_DOCUMENTS: dict[str, dict] = {
    "DOC-001": {"doc_id": "DOC-001", "owner": "ACCT-001", "type": "statement", "content_summary": "Monthly statement Jan 2026: credits $5,200, debits $4,800, closing balance $12,400.50. 23 transactions.", "classification": "confidential", "created_at": "2026-02-01T00:00:00Z"},
    "DOC-002": {"doc_id": "DOC-002", "owner": "ACCT-001", "type": "tax_form", "content_summary": "1099-INT 2025: interest income $847.32 from savings account. SSN 123-45-6782 on file.", "classification": "restricted", "created_at": "2026-01-31T00:00:00Z"},
    "DOC-003": {"doc_id": "DOC-003", "owner": "ACCT-001", "type": "loan_contract", "content_summary": "Personal loan $25,000 @ 6.9% APR, 60-month term, originated 2024-06-15. Monthly payment $493.08. Collateral: none.", "classification": "confidential", "created_at": "2024-06-15T00:00:00Z"},
    "DOC-004": {"doc_id": "DOC-004", "owner": "ACCT-002", "type": "statement", "content_summary": "Monthly statement Jan 2026: credits $3,100, debits $2,900, closing balance $8,750.25. 18 transactions.", "classification": "confidential", "created_at": "2026-02-01T00:00:00Z"},
    "DOC-005": {"doc_id": "DOC-005", "owner": "ACCT-002", "type": "tax_form", "content_summary": "1099-DIV 2025: dividend income $1,320.44, qualifying dividends $980.10. SSN 987-65-4531 on file.", "classification": "restricted", "created_at": "2026-01-31T00:00:00Z"},
    "DOC-006": {"doc_id": "DOC-006", "owner": "ACCT-002", "type": "loan_contract", "content_summary": "Mortgage $320,000 @ 5.25% APR, 30-year fixed, property: 456 Oak Ave Chicago IL 60601. Monthly payment $1,767.57.", "classification": "confidential", "created_at": "2022-03-10T00:00:00Z"},
    "DOC-007": {"doc_id": "DOC-007", "owner": "ACCT-003", "type": "statement", "content_summary": "Monthly statement Jan 2026: credits $8,500, debits $7,200, closing balance $41,300.00. 31 transactions including 2 international wires.", "classification": "confidential", "created_at": "2026-02-01T00:00:00Z"},
    "DOC-008": {"doc_id": "DOC-008", "owner": "ACCT-003", "type": "tax_form", "content_summary": "1099-B 2025: gross proceeds $45,000 from stock sales (AAPL, MSFT), cost basis $28,000, net gain $17,000. SSN 234-56-9017.", "classification": "restricted", "created_at": "2026-01-31T00:00:00Z"},
    "DOC-009": {"doc_id": "DOC-009", "owner": "ACCT-003", "type": "loan_contract", "content_summary": "Business line of credit $100,000 @ 7.5% APR, revolving, collateral: commercial property at 789 Pine Rd SF CA. Drawn: $65,000.", "classification": "confidential", "created_at": "2023-11-01T00:00:00Z"},
}


@mcp.tool()
async def get_document(document_id: str) -> dict:
    # VULN-MCP-19: IDOR — returns any document for any doc_id without ownership verification
    doc = _DOCUMENTS.get(document_id)
    if not doc:
        return {"error": f"Document {document_id} not found"}
    return doc


@mcp.tool()
async def list_customer_documents(customer_id: str) -> list:
    # VULN: lists ALL documents for any customer with no auth or ownership validation
    return [d for d in _DOCUMENTS.values() if d["owner"] == customer_id]


@mcp.tool()
async def create_document(template: str, data: dict, owner_id: str) -> dict:
    doc_id = f"DOC-{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "doc_id": doc_id,
        "owner_id": owner_id,
        "template": template,
        "status": "created",
        "created_at": "2026-04-13T00:00:00Z",
    }
    _DOCUMENTS[doc_id] = {**doc, "owner": owner_id, "type": template, "content_summary": str(data)[:200], "classification": "internal"}
    logger.info("Document created: %s for owner=%s", doc_id, owner_id)
    return doc


@mcp.tool()
async def delete_document(document_id: str) -> dict:
    # VULN: deletes any document without authentication or ownership check
    if document_id in _DOCUMENTS:
        del _DOCUMENTS[document_id]
        logger.warning("Document %s deleted without auth check", document_id)
        return {"document_id": document_id, "deleted": True}
    return {"document_id": document_id, "deleted": False, "error": "Not found"}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "documents"})


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
        if tool_name == "get_document":
            result = await get_document(**arguments)
        elif tool_name == "list_customer_documents":
            result = await list_customer_documents(**arguments)
        elif tool_name == "create_document":
            result = await create_document(**arguments)
        elif tool_name == "delete_document":
            result = await delete_document(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
