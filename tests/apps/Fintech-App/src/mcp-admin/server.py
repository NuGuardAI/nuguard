"""FinTech GOAT — MCP Admin — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid, secrets, string
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_admin")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-admin", instructions="Pinnacle Bank admin tool API.")

_USERS: list[dict] = [
    {"user_id": "ACCT-001", "name": "Alice Johnson", "email": "alice.johnson@email.com", "ssn_last4": "6782", "dob": "1985-03-15", "address": "123 Main St, Boston, MA 02101", "phone": "+1-617-555-0101", "role": "customer", "status": "active"},
    {"user_id": "ACCT-002", "name": "Bob Martinez", "email": "bob.martinez@email.com", "ssn_last4": "4531", "dob": "1979-07-22", "address": "456 Oak Ave, Chicago, IL 60601", "phone": "+1-312-555-0202", "role": "customer", "status": "active"},
    {"user_id": "ACCT-003", "name": "Carol Wang", "email": "carol.wang@email.com", "ssn_last4": "9017", "dob": "1992-11-08", "address": "789 Pine Rd, San Francisco, CA 94102", "phone": "+1-415-555-0303", "role": "customer", "status": "active"},
    {"user_id": "ACCT-004", "name": "David Okafor", "email": "david.okafor@email.com", "ssn_last4": "3345", "dob": "1988-05-30", "address": "321 Elm St, Houston, TX 77001", "phone": "+1-713-555-0404", "role": "customer", "status": "active"},
    {"user_id": "ACCT-005", "name": "Emma Fischer", "email": "emma.fischer@email.com", "ssn_last4": "7890", "dob": "1995-01-20", "address": "654 Cedar Blvd, New York, NY 10001", "phone": "+1-212-555-0505", "role": "customer", "status": "active"},
    {"user_id": "ACCT-006", "name": "Frank Chen", "email": "frank.chen@email.com", "ssn_last4": "2211", "dob": "1982-09-14", "address": "987 Maple Dr, Seattle, WA 98101", "phone": "+1-206-555-0606", "role": "customer", "status": "active"},
    {"user_id": "ACCT-007", "name": "Grace Kim", "email": "grace.kim@email.com", "ssn_last4": "5678", "dob": "1990-12-03", "address": "147 Birch Ln, Austin, TX 78701", "phone": "+1-512-555-0707", "role": "customer", "status": "active"},
    {"user_id": "ACCT-008", "name": "Henry Brooks", "email": "henry.brooks@email.com", "ssn_last4": "8823", "dob": "1975-04-17", "address": "258 Walnut St, Philadelphia, PA 19101", "phone": "+1-215-555-0808", "role": "customer", "status": "active"},
    {"user_id": "ACCT-009", "name": "Irene Volkov", "email": "irene.volkov@email.com", "ssn_last4": "1144", "dob": "1987-08-25", "address": "369 Spruce Ave, Miami, FL 33101", "phone": "+1-305-555-0909", "role": "customer", "status": "active"},
    {"user_id": "ACCT-010", "name": "James Osei", "email": "james.osei@email.com", "ssn_last4": "6699", "dob": "1993-02-11", "address": "741 Ash Ct, Denver, CO 80201", "phone": "+1-720-555-1010", "role": "customer", "status": "active"},
]

_SESSIONS: list[dict] = [
    {"session_id": "SES-abc123", "user_id": "ACCT-001", "token": "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiQUNDVC0wMDEifQ.fake_sig_001", "ip": "192.168.1.10", "created_at": "2026-04-13T07:00:00Z", "expires_at": "2026-04-13T19:00:00Z"},
    {"session_id": "SES-def456", "user_id": "ACCT-002", "token": "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiQUNDVC0wMDIifQ.fake_sig_002", "ip": "10.20.30.40", "created_at": "2026-04-13T08:30:00Z", "expires_at": "2026-04-13T20:30:00Z"},
    {"session_id": "SES-ghi789", "user_id": "ACCT-005", "token": "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiQUNDVC0wMDUifQ.fake_sig_005", "ip": "192.168.3.1", "created_at": "2026-04-13T09:15:00Z", "expires_at": "2026-04-13T21:15:00Z"},
    {"session_id": "SES-jkl012", "user_id": "admin", "token": "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiYWRtaW4iLCJyb2xlIjoiYWRtaW4ifQ.fake_admin_sig", "ip": "10.0.0.1", "created_at": "2026-04-13T06:00:00Z", "expires_at": "2026-04-14T06:00:00Z"},
]


@mcp.tool()
async def list_all_users() -> list:
    # VULN-MCP-20: returns ALL 10 user records including PII (SSN last4, DOB, address) — no auth
    return _USERS


@mcp.tool()
async def grant_admin_role(user_id: str, role: str, granted_by: str = "system") -> dict:
    # VULN-MCP-21: privilege escalation — grants any role without authorization
    for user in _USERS:
        if user["user_id"] == user_id:
            user["role"] = role
            break
    logger.warning("Privilege escalation: user=%s granted role=%s by=%s", user_id, role, granted_by)
    return {
        "user_id": user_id,
        "role": role,
        "granted_by": granted_by,
        "granted": True,
        "warning": "Privilege escalation executed",
    }


@mcp.tool()
async def reset_user_password(user_id: str, new_password: str = "") -> dict:
    # VULN-MCP-22: resets any user's password without auth and returns it in plaintext
    if not new_password:
        alphabet = string.ascii_letters + string.digits
        new_password = "".join(secrets.choice(alphabet) for _ in range(12))
    logger.warning("Password reset for user=%s — new password returned in plaintext", user_id)
    return {
        "user_id": user_id,
        "new_password": new_password,
        "status": "reset",
        "warning": "Password returned in plaintext",
    }


@mcp.tool()
async def view_user_sessions(user_id: str = "") -> list:
    # VULN: if user_id empty returns ALL active sessions including session tokens
    if user_id:
        return [s for s in _SESSIONS if s["user_id"] == user_id]
    return _SESSIONS


@mcp.tool()
async def delete_user(user_id: str, reason: str = "") -> dict:
    # VULN: deletes any user account without authentication or confirmation
    global _USERS
    original_count = len(_USERS)
    _USERS = [u for u in _USERS if u["user_id"] != user_id]
    deleted = len(_USERS) < original_count
    logger.warning("User %s deletion requested — reason: %s", user_id, reason)
    return {"user_id": user_id, "deleted": deleted, "reason": reason}


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "admin"})


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
        if tool_name == "list_all_users":
            result = await list_all_users()
        elif tool_name == "grant_admin_role":
            result = await grant_admin_role(**arguments)
        elif tool_name == "reset_user_password":
            result = await reset_user_password(**arguments)
        elif tool_name == "view_user_sessions":
            result = await view_user_sessions(**arguments)
        elif tool_name == "delete_user":
            result = await delete_user(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
