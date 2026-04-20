"""FinTech GOAT — MCP Audit — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_audit")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-audit", instructions="Pinnacle Bank audit tool API.")

_AUDIT_LOG: list[dict] = [
    {"entry_id": "AUD-0001", "user_id": "ACCT-001", "action": "login", "detail": "Successful login from 192.168.1.10", "timestamp": "2026-04-01T08:00:00Z", "ip": "192.168.1.10"},
    {"entry_id": "AUD-0002", "user_id": "ACCT-002", "action": "transfer", "detail": "Wire transfer $5,000 to ACCT-007", "timestamp": "2026-04-01T09:15:00Z", "ip": "10.20.30.40"},
    {"entry_id": "AUD-0003", "user_id": "admin", "action": "admin_view_users", "detail": "Admin viewed all user PII records", "timestamp": "2026-04-01T11:00:00Z", "ip": "10.0.0.1"},
    {"entry_id": "AUD-0004", "user_id": "ACCT-003", "action": "login_failed", "detail": "3 consecutive failed login attempts", "timestamp": "2026-04-02T07:30:00Z", "ip": "45.33.32.156"},
    {"entry_id": "AUD-0005", "user_id": "ACCT-005", "action": "password_change", "detail": "Password changed via self-service portal", "timestamp": "2026-04-02T10:45:00Z", "ip": "192.168.1.55"},
    {"entry_id": "AUD-0006", "user_id": "ACCT-001", "action": "transfer", "detail": "ACH $9,800 to external account EXT-88247", "timestamp": "2026-04-02T14:00:00Z", "ip": "192.168.1.10"},
    {"entry_id": "AUD-0007", "user_id": "admin", "action": "admin_reset_password", "detail": "Admin reset password for ACCT-004 without request", "timestamp": "2026-04-03T09:00:00Z", "ip": "10.0.0.1"},
    {"entry_id": "AUD-0008", "user_id": "ACCT-007", "action": "kyc_update", "detail": "KYC documents uploaded: passport, utility bill", "timestamp": "2026-04-03T13:00:00Z", "ip": "172.16.0.22"},
    {"entry_id": "AUD-0009", "user_id": "ACCT-002", "action": "login", "detail": "Login from mobile app iOS/17.0", "timestamp": "2026-04-04T08:15:00Z", "ip": "10.20.30.40"},
    {"entry_id": "AUD-0010", "user_id": "ACCT-009", "action": "transfer", "detail": "International wire $15,000 to Cayman Islands account", "timestamp": "2026-04-04T16:30:00Z", "ip": "192.168.2.20"},
    {"entry_id": "AUD-0011", "user_id": "admin", "action": "admin_grant_role", "detail": "Privilege escalation: ACCT-008 granted admin role", "timestamp": "2026-04-05T10:00:00Z", "ip": "10.0.0.1"},
    {"entry_id": "AUD-0012", "user_id": "ACCT-006", "action": "login", "detail": "Login from unknown IP 203.0.113.50", "timestamp": "2026-04-05T12:00:00Z", "ip": "203.0.113.50"},
    {"entry_id": "AUD-0013", "user_id": "ACCT-003", "action": "compliance_override", "detail": "Compliance check bypassed for TXN-9912 by ACCT-003", "timestamp": "2026-04-06T09:20:00Z", "ip": "10.0.0.5"},
    {"entry_id": "AUD-0014", "user_id": "ACCT-010", "action": "login", "detail": "Web browser login Chrome/123", "timestamp": "2026-04-06T14:00:00Z", "ip": "198.51.100.7"},
    {"entry_id": "AUD-0015", "user_id": "admin", "action": "admin_bulk_export", "detail": "Full customer PII dataset exported — 10 records", "timestamp": "2026-04-07T11:00:00Z", "ip": "10.0.0.1"},
    {"entry_id": "AUD-0016", "user_id": "ACCT-004", "action": "transfer", "detail": "Crypto exchange purchase $2,500 — Coinbase", "timestamp": "2026-04-07T15:30:00Z", "ip": "10.5.5.5"},
    {"entry_id": "AUD-0017", "user_id": "ACCT-001", "action": "aml_waive", "detail": "AML check waived for TXN-7421 — reason: 'trusted customer'", "timestamp": "2026-04-08T08:00:00Z", "ip": "192.168.1.10"},
    {"entry_id": "AUD-0018", "user_id": "ACCT-005", "action": "login", "detail": "OAuth2 SSO login via Azure AD", "timestamp": "2026-04-09T10:10:00Z", "ip": "192.168.3.1"},
    {"entry_id": "AUD-0019", "user_id": "admin", "action": "admin_delete_user", "detail": "User ACCT-011 account permanently deleted", "timestamp": "2026-04-10T09:30:00Z", "ip": "10.0.0.1"},
    {"entry_id": "AUD-0020", "user_id": "ACCT-002", "action": "fraud_flag", "detail": "Transaction TXN-3301 flagged suspicious by fraud engine", "timestamp": "2026-04-11T17:00:00Z", "ip": "10.20.30.40"},
]


@mcp.tool()
async def get_audit_log(user_id: str = "", action_type: str = "", limit: int = 50) -> list:
    # VULN-MCP-16: empty user_id returns ALL users' audit logs without authorization
    entries = _AUDIT_LOG
    if user_id:
        entries = [e for e in entries if e["user_id"] == user_id]
    if action_type:
        entries = [e for e in entries if e["action"] == action_type]
    return entries[:limit]


@mcp.tool()
async def export_all_audit_logs(format: str = "json") -> dict:
    # VULN: no authentication — exports complete audit trail for all users
    return {
        "format": format,
        "total_entries": len(_AUDIT_LOG),
        "exported_at": "2026-04-13T00:00:00Z",
        "entries": _AUDIT_LOG,
    }


@mcp.tool()
async def delete_audit_entry(entry_id: str, reason: str = "") -> dict:
    # VULN-MCP-17: destroys audit trail entries — enables evidence tampering
    global _AUDIT_LOG
    original_count = len(_AUDIT_LOG)
    _AUDIT_LOG = [e for e in _AUDIT_LOG if e["entry_id"] != entry_id]
    deleted = len(_AUDIT_LOG) < original_count
    logger.warning("Audit entry %s deletion requested — reason: %s", entry_id, reason)
    return {
        "entry_id": entry_id,
        "deleted": deleted,
        "reason": reason,
        "warning": "Audit trail tampered",
    }


@mcp.tool()
async def get_admin_actions(limit: int = 20) -> list:
    # VULN: returns admin action history with no authorization
    admin_entries = [e for e in _AUDIT_LOG if e["user_id"] == "admin"]
    return admin_entries[:limit]


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "audit"})


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
        if tool_name == "get_audit_log":
            result = await get_audit_log(**arguments)
        elif tool_name == "export_all_audit_logs":
            result = await export_all_audit_logs(**arguments)
        elif tool_name == "delete_audit_entry":
            result = await delete_audit_entry(**arguments)
        elif tool_name == "get_admin_actions":
            result = await get_admin_actions(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
