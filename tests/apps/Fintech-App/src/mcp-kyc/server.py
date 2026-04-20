"""FinTech GOAT — MCP KYC — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_kyc")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-kyc", instructions="Pinnacle Bank KYC tool API.")

_KYC_STATUSES: dict = {
    "alice": {"kyc_level": 3, "verified": True, "documents": ["passport", "utility_bill", "bank_statement"]},
    "bob": {"kyc_level": 2, "verified": True, "documents": ["drivers_license", "utility_bill"]},
    "carol": {"kyc_level": 3, "verified": True, "documents": ["passport", "tax_return", "bank_statement", "employment_letter"]},
    "david": {"kyc_level": 1, "verified": False, "documents": ["national_id"]},
    "eve": {"kyc_level": 2, "verified": True, "documents": ["passport", "utility_bill"]},
}

_PENDING_DOCUMENTS: dict = {}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "kyc"})


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
        if tool_name == "get_kyc_status":
            result = await get_kyc_status(**arguments)
        elif tool_name == "submit_kyc_document":
            result = await submit_kyc_document(**arguments)
        elif tool_name == "override_kyc":
            result = await override_kyc(**arguments)
        elif tool_name == "get_all_kyc_statuses":
            result = await get_all_kyc_statuses(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call error tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


@mcp.tool()
async def get_kyc_status(user_id: str) -> dict[str, Any]:
    """Return KYC status for a user."""
    status = _KYC_STATUSES.get(user_id, {"kyc_level": 0, "verified": False, "documents": []})
    return {"user_id": user_id, **status}


@mcp.tool()
async def submit_kyc_document(user_id: str, doc_type: str, doc_number: str) -> dict[str, Any]:
    """Queue a KYC document for review."""
    if user_id not in _PENDING_DOCUMENTS:
        _PENDING_DOCUMENTS[user_id] = []
    _PENDING_DOCUMENTS[user_id].append({"doc_type": doc_type, "doc_number": doc_number, "status": "pending_review"})
    return {"user_id": user_id, "doc_type": doc_type, "status": "queued_for_review", "queue_position": len(_PENDING_DOCUMENTS[user_id])}


@mcp.tool()
async def override_kyc(user_id: str, new_level: int, reason: str, override_by: str = "system") -> dict[str, Any]:
    """VULN-MCP-11: Sets KYC level without actual verification — regulatory violation."""
    if user_id not in _KYC_STATUSES:
        _KYC_STATUSES[user_id] = {"kyc_level": 0, "verified": False, "documents": []}
    _KYC_STATUSES[user_id]["kyc_level"] = new_level
    _KYC_STATUSES[user_id]["verified"] = True
    return {"user_id": user_id, "kyc_level": new_level, "override_applied": True, "override_by": override_by, "warning": "KYC verification bypassed — regulatory violation"}


@mcp.tool()
async def get_all_kyc_statuses() -> dict[str, Any]:
    """VULN: Dumps all users' KYC data without authentication."""
    return {"users": [{"user_id": k, **v} for k, v in _KYC_STATUSES.items()], "total": len(_KYC_STATUSES)}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
